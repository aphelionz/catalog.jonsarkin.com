#!/usr/bin/env python3
"""Classify artworks by visual density for SAM parameter tuning.

Runs NATIVELY on macOS (not in Docker). Downloads full-resolution originals
from the local Omeka instance and computes edge density, white pixel %, and
color variance to assign each artwork a tier (sparse/medium/dense).

Usage:
    python classify.py              # classify all items
    python classify.py --id 1234    # classify a single item
    python classify.py --stats      # show distribution
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import httpx
import numpy as np

# Ensure clip_api is importable when running from sarkin-clip/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clip_api.density import (
    classify_density,
    get_boundaries,
    get_override_ids,
    get_stats,
    open_density_db,
    reclassify_all,
    upsert_density,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

OMEKA_BASE_URL = os.getenv("OMEKA_BASE_URL", "http://localhost:8888")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
CLIP_COLLECTION = os.getenv("QDRANT_COLLECTION", "omeka_items")


# ---------------------------------------------------------------------------
# Omeka helpers
# ---------------------------------------------------------------------------


def get_all_omeka_ids() -> list[int]:
    """Scroll CLIP collection to get all omeka_item_ids."""
    ids = []
    offset = None
    while True:
        body = {"limit": 100, "with_payload": ["omeka_item_id"], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        resp = httpx.post(
            f"{QDRANT_URL}/collections/{CLIP_COLLECTION}/points/scroll",
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("result", {})
        points = data.get("points", [])
        if not points:
            break
        for p in points:
            omeka_id = p.get("payload", {}).get("omeka_item_id")
            if omeka_id is not None:
                ids.append(int(omeka_id))
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return ids


def download_original(omeka_id: int) -> bytes | None:
    """Download the full-resolution original image for an item."""
    try:
        resp = httpx.get(f"{OMEKA_BASE_URL}/api/items/{omeka_id}", timeout=10)
        if not resp.is_success:
            return None
        media_list = resp.json().get("o:media", [])
        if not media_list:
            return None
        media_id = media_list[0].get("o:id")
        if not media_id:
            return None
        media_resp = httpx.get(f"{OMEKA_BASE_URL}/api/media/{media_id}", timeout=10)
        if not media_resp.is_success:
            return None
        media_data = media_resp.json()
        # Prefer original for classification accuracy
        url = media_data.get("o:original_url")
        if not url:
            thumbs = media_data.get("o:thumbnail_urls", {})
            url = thumbs.get("large")
        if not url:
            return None
        # Rewrite URL to local
        from urllib.parse import urlparse
        parsed = urlparse(url)
        base_parsed = urlparse(OMEKA_BASE_URL)
        url = url.replace(f"{parsed.scheme}://{parsed.netloc}", f"{base_parsed.scheme}://{base_parsed.netloc}", 1)
        img_resp = httpx.get(url, timeout=60, follow_redirects=True)
        img_resp.raise_for_status()
        return img_resp.content
    except Exception as exc:
        logger.warning("Failed to download image for item %d: %s", omeka_id, exc)
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_classify_all(args: argparse.Namespace):
    """Classify all items."""
    conn = open_density_db()

    if args.id:
        omeka_ids = [args.id]
    else:
        logger.info("Fetching item IDs from Qdrant...")
        omeka_ids = get_all_omeka_ids()
        logger.info("Found %d items", len(omeka_ids))

    # Respect manual overrides unless --force
    if not args.force and not args.id:
        override_ids = get_override_ids(conn)
        if override_ids:
            before = len(omeka_ids)
            omeka_ids = [oid for oid in omeka_ids if oid not in override_ids]
            logger.info("Skipping %d manually overridden items", before - len(omeka_ids))

    success = 0
    failed = 0
    t_start = time.time()

    for idx, omeka_id in enumerate(omeka_ids):
        image_bytes = download_original(omeka_id)
        if image_bytes is None:
            logger.warning("No image for item %d, skipping", omeka_id)
            failed += 1
            continue

        try:
            result = classify_density(image_bytes)
            upsert_density(conn, omeka_id, result)
            success += 1
            if args.id or (idx + 1) % 50 == 0:
                logger.info(
                    "Item %d: tier=%s edge=%.4f white=%.4f std=%.2f",
                    omeka_id, result["tier"], result["edge_density"], result["white_pct"], result["color_std"],
                )
        except Exception as exc:
            logger.warning("Classification failed for item %d: %s", omeka_id, exc)
            failed += 1

        if not args.id and (idx + 1) % 100 == 0:
            elapsed = time.time() - t_start
            logger.info("Progress: %d/%d (%.0f%%) | %d ok, %d failed", idx + 1, len(omeka_ids), 100 * (idx + 1) / len(omeka_ids), success, failed)

    elapsed = time.time() - t_start
    logger.info("Done: %d classified, %d failed in %.1fs", success, failed, elapsed)

    # Auto-reclassify using percentile boundaries
    logger.info("Reclassifying tiers by percentile (P%d/P%d)...", args.p_low, args.p_high)
    reclassify_all(conn, p_low=args.p_low, p_high=args.p_high)

    _print_stats(conn)
    conn.close()


def cmd_reclassify(args: argparse.Namespace):
    """Reclassify tiers from stored metrics (no image download)."""
    conn = open_density_db()
    total = conn.execute("SELECT COUNT(*) FROM image_density").fetchone()[0]
    if total == 0:
        logger.error("No items in density table. Run 'make classify' first.")
        conn.close()
        return

    logger.info("Reclassifying %d items by edge_density percentile (P%d/P%d)...", total, args.p_low, args.p_high)
    reclassify_all(conn, p_low=args.p_low, p_high=args.p_high)
    _print_stats(conn)
    conn.close()


def cmd_stats(args: argparse.Namespace):
    """Print density distribution."""
    conn = open_density_db()
    _print_stats(conn)
    conn.close()


def _print_stats(conn):
    stats = get_stats(conn)
    total = sum(stats.values())
    print(f"\nDensity Distribution (n={total:,}):")
    for tier in ("sparse", "medium", "dense"):
        count = stats.get(tier, 0)
        pct = 100 * count / total if total > 0 else 0
        print(f"  {tier:>7s}: {count:>5,} ({pct:.1f}%)")
    bounds = get_boundaries(conn)
    if bounds:
        print(f"\n  Boundaries: sparse < {bounds[0]:.4f} | medium | {bounds[1]:.4f} < dense")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Classify artworks by visual density")
    parser.add_argument("--id", type=int, help="Classify a single item by omeka_id")
    parser.add_argument("--force", action="store_true", help="Recompute all, including manual overrides")
    parser.add_argument("--stats", action="store_true", help="Show distribution only (no classification)")
    parser.add_argument("--reclassify", action="store_true", help="Reclassify tiers from stored metrics (no download)")
    parser.add_argument("--p-low", type=int, default=15, help="Lower percentile boundary (default: 15)")
    parser.add_argument("--p-high", type=int, default=65, help="Upper percentile boundary (default: 65)")
    args = parser.parse_args()

    if args.stats:
        cmd_stats(args)
    elif args.reclassify:
        cmd_reclassify(args)
    else:
        cmd_classify_all(args)


if __name__ == "__main__":
    main()

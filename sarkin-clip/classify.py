#!/usr/bin/env python3
"""Classify artworks by visual density for SAM parameter tuning.

Runs NATIVELY on macOS (not in Docker). Two modes:
  --metadata (default): derives density from motif count + transcription length in MariaDB
  --opencv: downloads images and computes edge density / white pixel % / color std

Usage:
    python classify.py                  # metadata-derived classification (fast)
    python classify.py --opencv         # OpenCV-based classification (slow, downloads images)
    python classify.py --reclassify     # re-tier from stored scores
    python classify.py --stats          # show distribution
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import httpx

# Ensure clip_api is importable when running from sarkin-clip/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clip_api.density import (
    classify_density,
    get_boundaries,
    get_override_ids,
    get_stats,
    get_stats_by_source,
    open_density_db,
    reclassify_all,
    reclassify_metadata,
    upsert_density,
    upsert_metadata_density,
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

# MariaDB connection defaults (same as docker-compose)
DB_HOST = os.getenv("OMEKA_DB_HOST", "localhost")
DB_PORT = int(os.getenv("OMEKA_DB_PORT", "3306"))
DB_USER = os.getenv("OMEKA_DB_USER", "omeka")
DB_PASS = os.getenv("OMEKA_DB_PASS", "omeka")
DB_NAME = os.getenv("OMEKA_DB_NAME", "omeka")

# Density scoring formula
MOTIF_WEIGHT = 5
TRANSCRIPTION_CAP = 20
TRANSCRIPTION_DIVISOR = 100

# Confirmed property IDs from this Omeka installation
PROP_SUBJECT = 3    # dcterms:subject (motifs)
PROP_CONTENT = 91   # bibo:content (transcription)

# resource_class_id 225 = schema:VisualArtwork (excludes writings/CreativeWork)
RESOURCE_CLASS_ARTWORK = 225

METADATA_SCORE_SQL = f"""
SELECT
    r.id as omeka_id,
    COALESCE(motifs.motif_count, 0) as motif_count,
    COALESCE(LENGTH(transcription.value), 0) as transcription_length
FROM resource r
JOIN item i ON r.id = i.id
LEFT JOIN (
    SELECT v.resource_id, COUNT(*) as motif_count
    FROM value v
    WHERE v.property_id = {PROP_SUBJECT}
    GROUP BY v.resource_id
) motifs ON motifs.resource_id = r.id
LEFT JOIN (
    SELECT v.resource_id, v.value
    FROM value v
    WHERE v.property_id = {PROP_CONTENT}
) transcription ON transcription.resource_id = r.id
WHERE r.resource_class_id = {RESOURCE_CLASS_ARTWORK}
"""


def compute_density_score(motif_count: int, transcription_length: int) -> float:
    """Compute density score: motifs weighted, transcription capped."""
    return (motif_count * MOTIF_WEIGHT) + min(transcription_length / TRANSCRIPTION_DIVISOR, TRANSCRIPTION_CAP)


# ---------------------------------------------------------------------------
# Omeka helpers (for OpenCV mode)
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
        url = media_data.get("o:original_url")
        if not url:
            thumbs = media_data.get("o:thumbnail_urls", {})
            url = thumbs.get("large")
        if not url:
            return None
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


def _fetch_metadata_rows_docker() -> list[dict]:
    """Fetch metadata via docker compose exec (when MariaDB port isn't exposed)."""
    import subprocess

    logger.info("Querying MariaDB via docker compose exec...")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db",
         "mariadb", "-uomeka", "-pomeka", "omeka",
         "--batch", "--skip-column-names", "-e", METADATA_SCORE_SQL],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose exec failed: {result.stderr.strip()}")

    rows = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append({
                "omeka_id": int(parts[0]),
                "motif_count": int(parts[1]),
                "transcription_length": int(parts[2]),
            })
    return rows


def cmd_metadata(args: argparse.Namespace):
    """Classify using metadata from MariaDB (motif count + transcription length)."""
    rows = None

    # Try direct pymysql connection first
    db_host = args.db_host or DB_HOST
    try:
        import pymysql

        logger.info("Connecting to MariaDB at %s:%d...", db_host, DB_PORT)
        maria = pymysql.connect(
            host=db_host, port=DB_PORT, user=DB_USER, password=DB_PASS,
            database=DB_NAME, cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
        )
        with maria.cursor() as cur:
            cur.execute(METADATA_SCORE_SQL)
            rows = cur.fetchall()
        maria.close()
    except Exception as exc:
        logger.info("Direct MariaDB connection failed (%s), falling back to docker exec", exc)

    if rows is None:
        rows = _fetch_metadata_rows_docker()

    logger.info("Fetched %d artwork items from MariaDB", len(rows))

    conn = open_density_db()

    # Remove any non-artwork items from previous runs
    artwork_ids = {row["omeka_id"] for row in rows}
    existing = {r[0] for r in conn.execute("SELECT omeka_id FROM image_density").fetchall()}
    stale = existing - artwork_ids
    if stale:
        placeholders = ",".join("?" * len(stale))
        conn.execute(f"DELETE FROM image_density WHERE omeka_id IN ({placeholders})", list(stale))
        conn.commit()
        logger.info("Removed %d non-artwork items from density table", len(stale))

    t_start = time.time()
    updated = 0

    for row in rows:
        motif_count = row["motif_count"]
        transcription_length = row["transcription_length"]
        score = compute_density_score(motif_count, transcription_length)
        # Tier assigned later by reclassify_metadata; use 'medium' as placeholder
        upsert_metadata_density(conn, row["omeka_id"], "medium", motif_count, transcription_length, score)
        updated += 1

    elapsed = time.time() - t_start
    logger.info("Scored %d items in %.1fs", updated, elapsed)

    # Reclassify using percentile boundaries on density_score
    logger.info("Reclassifying by density_score percentile (P%d/P%d)...", args.p_low, args.p_high)
    reclassify_metadata(conn, p_low=args.p_low, p_high=args.p_high)

    _print_stats(conn)
    conn.close()


def cmd_opencv(args: argparse.Namespace):
    """Classify using OpenCV (downloads images)."""
    conn = open_density_db()

    if args.id:
        omeka_ids = [args.id]
    else:
        logger.info("Fetching item IDs from Qdrant...")
        omeka_ids = get_all_omeka_ids()
        logger.info("Found %d items", len(omeka_ids))

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
            logger.info("Progress: %d/%d (%.0f%%) | %d ok, %d failed", idx + 1, len(omeka_ids), 100 * (idx + 1) / len(omeka_ids), success, failed)

    elapsed = time.time() - t_start
    logger.info("Done: %d classified, %d failed in %.1fs", success, failed, elapsed)

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

    # Use metadata reclassify if we have density_score data, otherwise edge_density
    has_scores = conn.execute("SELECT COUNT(*) FROM image_density WHERE density_score > 0").fetchone()[0]
    if has_scores > 0:
        logger.info("Reclassifying %d items by density_score percentile (P%d/P%d)...", has_scores, args.p_low, args.p_high)
        reclassify_metadata(conn, p_low=args.p_low, p_high=args.p_high)
    else:
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

    source_stats = get_stats_by_source(conn)
    if source_stats:
        print(f"\n  Sources: {', '.join(f'{k}={v}' for k, v in sorted(source_stats.items()))}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Classify artworks by visual density")
    parser.add_argument("--id", type=int, help="Classify a single item by omeka_id")
    parser.add_argument("--force", action="store_true", help="Recompute all, including manual overrides")
    parser.add_argument("--stats", action="store_true", help="Show distribution only")
    parser.add_argument("--opencv", action="store_true", help="Use OpenCV classification (downloads images)")
    parser.add_argument("--reclassify", action="store_true", help="Reclassify tiers from stored metrics")
    parser.add_argument("--p-low", type=int, default=15, help="Lower percentile boundary (default: 15)")
    parser.add_argument("--p-high", type=int, default=65, help="Upper percentile boundary (default: 65)")
    parser.add_argument("--db-host", type=str, default=None, help="MariaDB host (default: localhost)")
    args = parser.parse_args()

    if args.stats:
        cmd_stats(args)
    elif args.reclassify:
        cmd_reclassify(args)
    elif args.opencv:
        cmd_opencv(args)
    else:
        cmd_metadata(args)


if __name__ == "__main__":
    main()

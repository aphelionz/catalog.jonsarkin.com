#!/usr/bin/env python3
"""Local SAM 2.1 segmentation + push-to-prod pipeline.

Runs NATIVELY on macOS (not in Docker) to use MPS acceleration.
Requires: sam2, torch (with MPS), clip_api package on PYTHONPATH.

Subcommands:
    segment [--force]   Segment all artworks with SAM 2.1 + DINOv2 CLS
    push                Push segment JPEGs + Qdrant vectors to production

Usage:
    python local_segment_ingest.py segment          # incremental
    python local_segment_ingest.py segment --force   # re-segment all
    python local_segment_ingest.py push              # push to prod
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import signal
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx
import numpy as np
from PIL import Image

# Ensure clip_api is importable when running from sarkin-clip/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — defaults for running natively on macOS against local Docker stack
# ---------------------------------------------------------------------------

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
CLIP_COLLECTION = os.getenv("QDRANT_COLLECTION", "omeka_items")
SEGMENT_COLLECTION = os.getenv("SEGMENT_COLLECTION", "sarkin_motif_segments")
SEGMENT_DIR = os.getenv("SEGMENT_DIR", os.path.join(os.path.dirname(__file__), "segments"))
OMEKA_BASE_URL = os.getenv("OMEKA_BASE_URL", "http://localhost:8888")

# Prod push config (from Makefile)
PROD_HOST = os.getenv("PROD_HOST", "omeka.us-east1-b.folkloric-rite-468520-r2")
PROD_USER = os.getenv("PROD_USER", "mark")
PROD_SEGMENT_DIR = os.getenv("PROD_SEGMENT_DIR", "/opt/catalog/segments")
PROD_QDRANT_LOCAL_PORT = 16333  # SSH tunnel local port

# Lazy model imports
dino = None
sam = None


def _load_models():
    global dino, sam
    if dino is None:
        from clip_api import dino as _dino
        dino = _dino
    if sam is None:
        from clip_api import sam as _sam
        sam = _sam


# ---------------------------------------------------------------------------
# Qdrant helpers (same pattern as batch_segment_ingest.py)
# ---------------------------------------------------------------------------

def ensure_collection(qdrant_url: str = QDRANT_URL):
    """Create the segment collection if it doesn't exist."""
    _load_models()
    url = f"{qdrant_url}/collections/{SEGMENT_COLLECTION}"
    resp = httpx.get(url, timeout=5)
    if resp.is_success:
        logger.info("Collection %s already exists", SEGMENT_COLLECTION)
        return
    resp = httpx.put(
        url,
        json={
            "vectors": {"size": dino.DINO_DIM, "distance": "Cosine"},
            "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
            "optimizers_config": {"memmap_threshold": 20000},
        },
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Created collection %s", SEGMENT_COLLECTION)


def get_all_items(qdrant_url: str = QDRANT_URL) -> list[dict]:
    """Scroll through all points in the CLIP collection to get item metadata."""
    items = []
    offset = None
    while True:
        body = {
            "limit": 100,
            "with_payload": ["omeka_item_id", "omeka_url", "thumb_url"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        resp = httpx.post(
            f"{qdrant_url}/collections/{CLIP_COLLECTION}/points/scroll",
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("result", {})
        points = data.get("points", [])
        if not points:
            break
        for p in points:
            payload = p.get("payload", {})
            items.append({
                "omeka_item_id": payload.get("omeka_item_id", p.get("id")),
                "omeka_url": payload.get("omeka_url", ""),
                "thumb_url": payload.get("thumb_url", ""),
            })
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return items


def get_existing_item_ids(qdrant_url: str = QDRANT_URL) -> set[int]:
    """Get omeka_item_ids already in the segment collection."""
    ids = set()
    offset = None
    while True:
        body = {
            "limit": 100,
            "with_payload": ["omeka_item_id"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        resp = httpx.post(
            f"{qdrant_url}/collections/{SEGMENT_COLLECTION}/points/scroll",
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("result", {})
        points = data.get("points", [])
        if not points:
            break
        for p in points:
            payload = p.get("payload", {})
            omeka_id = payload.get("omeka_item_id")
            if omeka_id is not None:
                ids.add(int(omeka_id))
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return ids


def scroll_all_points(qdrant_url: str, collection: str) -> list[dict]:
    """Scroll all points (with vectors) from a collection."""
    points = []
    offset = None
    while True:
        body = {
            "limit": 100,
            "with_payload": True,
            "with_vector": True,
        }
        if offset is not None:
            body["offset"] = offset
        resp = httpx.post(
            f"{qdrant_url}/collections/{collection}/points/scroll",
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json().get("result", {})
        batch = data.get("points", [])
        if not batch:
            break
        points.extend(batch)
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return points


# ---------------------------------------------------------------------------
# Image URL resolution
# ---------------------------------------------------------------------------

def _rewrite_url(url: str) -> str:
    """Rewrite Omeka-generated URLs to use the local base."""
    parsed = urlparse(url)
    base_parsed = urlparse(OMEKA_BASE_URL)
    return url.replace(
        f"{parsed.scheme}://{parsed.netloc}",
        f"{base_parsed.scheme}://{base_parsed.netloc}",
        1,
    )


def get_image_url(omeka_item_id: int, thumb_url: str) -> str:
    """Get the best available image URL for an item."""
    try:
        resp = httpx.get(f"{OMEKA_BASE_URL}/api/items/{omeka_item_id}", timeout=10)
        if resp.is_success:
            item_data = resp.json()
            media_list = item_data.get("o:media", [])
            if media_list:
                media_id = media_list[0].get("o:id")
                if media_id:
                    media_resp = httpx.get(f"{OMEKA_BASE_URL}/api/media/{media_id}", timeout=10)
                    if media_resp.is_success:
                        media_data = media_resp.json()
                        thumbs = media_data.get("o:thumbnail_urls", {})
                        large = thumbs.get("large")
                        if large:
                            return _rewrite_url(large)
                        original = media_data.get("o:original_url")
                        if original:
                            return _rewrite_url(original)
    except Exception:
        pass
    return thumb_url


# ---------------------------------------------------------------------------
# Segment processing (SAM + DINOv2 + JPEG + Qdrant)
# ---------------------------------------------------------------------------

def process_item(item: dict) -> bool:
    """Download image, segment, embed each segment, save JPEGs, upsert to Qdrant."""
    _load_models()
    omeka_id = int(item["omeka_item_id"])
    image_url = get_image_url(omeka_id, item.get("thumb_url", ""))
    if not image_url:
        logger.warning("No image URL for item %d, skipping", omeka_id)
        return False

    try:
        resp = httpx.get(image_url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        image_bytes = resp.content
    except Exception as exc:
        logger.warning("Failed to download image for item %d: %s", omeka_id, exc)
        return False

    # Segment with SAM 2.1 (or MobileSAM fallback)
    try:
        segments = sam.segment_image(image_bytes)
    except Exception as exc:
        logger.warning("SAM segmentation failed for item %d: %s", omeka_id, exc)
        return False

    if not segments:
        logger.info("Item %d produced 0 segments, skipping", omeka_id)
        return True

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    segment_dir = os.path.join(SEGMENT_DIR, str(omeka_id))
    os.makedirs(segment_dir, exist_ok=True)

    points = []
    meta_entries = []
    omeka_url = item.get("omeka_url", "")
    thumb_url = item.get("thumb_url", "")

    for idx, seg in enumerate(segments):
        try:
            embedding = dino.embed_segment(image, seg["mask"], seg["bbox"])
        except Exception as exc:
            logger.warning("DINOv2 embed failed for item %d segment %d: %s", omeka_id, idx, exc)
            continue

        # Save masked segment JPEG
        x, y, w, h = seg["bbox"]
        mask_crop = seg["mask"][y:y + h, x:x + w]
        crop_array = np.array(image.crop((x, y, x + w, y + h)))
        gray_bg = np.full_like(crop_array, 128)
        masked = np.where(mask_crop[:, :, None], crop_array, gray_bg)
        seg_image = Image.fromarray(masked)
        seg_path = os.path.join(segment_dir, f"{idx}.jpg")
        seg_image.save(seg_path, "JPEG", quality=85)

        segment_url = f"/segments/{omeka_id}/{idx}.jpg"

        points.append({
            "id": omeka_id * 1000 + idx,
            "vector": embedding,
            "payload": {
                "omeka_item_id": omeka_id,
                "omeka_url": omeka_url,
                "thumb_url": thumb_url,
                "segment_index": idx,
                "segment_url": segment_url,
                "bbox": list(seg["bbox"]),
                "area": seg["area"],
            },
        })

        meta_entries.append({
            "segment_index": idx,
            "bbox": list(seg["bbox"]),
            "area": seg["area"],
            "area_pct": seg["area_pct"],
            "stability_score": seg["stability_score"],
        })

    if not points:
        logger.warning("Item %d: all segments failed embedding", omeka_id)
        return False

    # Save metadata JSON
    meta_path = os.path.join(segment_dir, "meta.json")
    with open(meta_path, "w") as f:
        json.dump({"omeka_item_id": omeka_id, "segments": meta_entries}, f)

    # Delete existing segments for this item, then upsert new ones
    httpx.post(
        f"{QDRANT_URL}/collections/{SEGMENT_COLLECTION}/points/delete",
        json={"filter": {"must": [{"key": "omeka_item_id", "match": {"value": omeka_id}}]}},
        timeout=30,
    )
    resp = httpx.put(
        f"{QDRANT_URL}/collections/{SEGMENT_COLLECTION}/points",
        json={"points": points},
        timeout=60,
    )
    resp.raise_for_status()
    logger.info("Item %d: %d segments ingested", omeka_id, len(points))
    return True


# ---------------------------------------------------------------------------
# Subcommand: segment
# ---------------------------------------------------------------------------

def cmd_segment(args: argparse.Namespace):
    """Run SAM 2.1 segmentation + DINOv2 embedding on all artworks."""
    logger.info("Backend: %s", sam.SAM_BACKEND if sam else "(loading...)")
    logger.info("Qdrant: %s", QDRANT_URL)
    logger.info("Omeka: %s", OMEKA_BASE_URL)
    logger.info("Segment dir: %s", SEGMENT_DIR)

    os.makedirs(SEGMENT_DIR, exist_ok=True)
    ensure_collection()

    logger.info("Fetching items from CLIP collection...")
    items = get_all_items()
    logger.info("Found %d items", len(items))

    if not args.force:
        existing = get_existing_item_ids()
        before = len(items)
        items = [i for i in items if int(i["omeka_item_id"]) not in existing]
        logger.info("Skipping %d already-embedded items, %d remaining", before - len(items), len(items))

    if not items:
        logger.info("Nothing to segment")
        return

    # Warm up models
    logger.info("Loading SAM + DINOv2 models (first run downloads weights)...")
    _load_models()
    dino._get_dino()
    sam._get_sam()
    logger.info("Models loaded (SAM backend: %s)", sam.SAM_BACKEND)

    success = 0
    failed = 0
    t_start = time.time()

    for idx, item in enumerate(items):
        omeka_id = item["omeka_item_id"]
        try:
            ok = process_item(item)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as exc:
            logger.error("Unexpected error for item %s: %s", omeka_id, exc)
            failed += 1

        if (idx + 1) % 10 == 0 or idx == len(items) - 1:
            elapsed = time.time() - t_start
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            eta = (len(items) - idx - 1) / rate if rate > 0 else 0
            logger.info(
                "Progress: %d/%d (%.0f%%) | %d ok, %d failed | %.1f items/s | ETA %.0fm",
                idx + 1, len(items), 100 * (idx + 1) / len(items),
                success, failed, rate, eta / 60,
            )

    elapsed = time.time() - t_start
    logger.info("Done: %d succeeded, %d failed in %.1f minutes", success, failed, elapsed / 60)


# ---------------------------------------------------------------------------
# Subcommand: push
# ---------------------------------------------------------------------------

def cmd_push(args: argparse.Namespace):
    """Push locally-computed segment vectors + JPEGs to production."""
    prod_target = f"{PROD_USER}@{PROD_HOST}"
    tunnel_url = f"http://localhost:{PROD_QDRANT_LOCAL_PORT}"

    # Step 1: rsync segment JPEGs to prod
    logger.info("Rsyncing segment JPEGs to %s:%s ...", prod_target, PROD_SEGMENT_DIR)
    rsync_cmd = [
        "rsync", "-avz", "--compress", "--partial", "--progress",
        f"{SEGMENT_DIR}/",
        f"{prod_target}:{PROD_SEGMENT_DIR}/",
    ]
    rsync_result = subprocess.run(rsync_cmd)
    if rsync_result.returncode != 0:
        logger.error("rsync failed with exit code %d", rsync_result.returncode)
        sys.exit(1)
    logger.info("rsync complete")

    # Step 2: open SSH tunnel to prod Qdrant
    logger.info("Opening SSH tunnel to prod Qdrant (local:%d -> prod:6333)...", PROD_QDRANT_LOCAL_PORT)
    tunnel_proc = subprocess.Popen(
        [
            "ssh", "-N", "-L",
            f"{PROD_QDRANT_LOCAL_PORT}:localhost:6333",
            prod_target,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Give the tunnel a moment to establish
    time.sleep(2)
    if tunnel_proc.poll() is not None:
        logger.error("SSH tunnel failed to start")
        sys.exit(1)

    try:
        # Step 3: ensure collection exists on prod
        ensure_collection(qdrant_url=tunnel_url)

        # Step 4: scroll all local segment points
        logger.info("Scrolling local segment collection...")
        local_points = scroll_all_points(QDRANT_URL, SEGMENT_COLLECTION)
        logger.info("Found %d local segment points to push", len(local_points))

        if not local_points:
            logger.info("No segments to push")
            return

        # Step 5: upsert to prod in batches
        batch_size = 100
        for i in range(0, len(local_points), batch_size):
            batch = local_points[i:i + batch_size]
            # Format points for upsert
            upsert_points = []
            for p in batch:
                upsert_points.append({
                    "id": p["id"],
                    "vector": p["vector"],
                    "payload": p.get("payload", {}),
                })

            resp = httpx.put(
                f"{tunnel_url}/collections/{SEGMENT_COLLECTION}/points",
                json={"points": upsert_points},
                timeout=60,
            )
            resp.raise_for_status()
            logger.info("Pushed %d/%d points", min(i + batch_size, len(local_points)), len(local_points))

        logger.info("All %d segment vectors pushed to prod", len(local_points))

    finally:
        # Step 6: close SSH tunnel
        logger.info("Closing SSH tunnel...")
        tunnel_proc.send_signal(signal.SIGTERM)
        tunnel_proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Local SAM 2.1 segmentation + push-to-prod pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    seg_parser = subparsers.add_parser("segment", help="Segment artworks with SAM 2.1")
    seg_parser.add_argument("--force", action="store_true", help="Re-segment all items")

    subparsers.add_parser("push", help="Push segments to production")

    args = parser.parse_args()

    if args.command == "segment":
        cmd_segment(args)
    elif args.command == "push":
        cmd_push(args)


if __name__ == "__main__":
    main()

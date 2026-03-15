#!/usr/bin/env python3
"""One-time batch SAM segmentation + DINOv2 CLS embedding for all artworks.

Reads all items from the existing CLIP Qdrant collection (omeka_items),
downloads each image, segments with MobileSAM, embeds each segment with
DINOv2 CLS token, saves segment JPEGs, and upserts to the
sarkin_motif_segments collection.

Usage:
    docker compose exec clip-api python3 batch_segment_ingest.py [--force]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time

import httpx
import numpy as np
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
CLIP_COLLECTION = os.getenv("QDRANT_COLLECTION", "omeka_items")
SEGMENT_COLLECTION = os.getenv("SEGMENT_COLLECTION", "sarkin_motif_segments")
SEGMENT_DIR = os.getenv("SEGMENT_DIR", "/app/segments")

# Lazy imports
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


def ensure_collection():
    """Create the segment collection if it doesn't exist."""
    _load_models()
    url = f"{QDRANT_URL}/collections/{SEGMENT_COLLECTION}"
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


def get_all_items() -> list[dict]:
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


def get_existing_item_ids() -> set[int]:
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
            f"{QDRANT_URL}/collections/{SEGMENT_COLLECTION}/points/scroll",
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


def _rewrite_url(url: str, omeka_base: str) -> str:
    """Rewrite Omeka-generated URLs to use the container-reachable base."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base_parsed = urlparse(omeka_base)
    return url.replace(f"{parsed.scheme}://{parsed.netloc}", f"{base_parsed.scheme}://{base_parsed.netloc}", 1)


def get_image_url(omeka_item_id: int, thumb_url: str) -> str:
    """Get the best available image URL for an item."""
    omeka_base = os.getenv("OMEKA_BASE_URL", "http://omeka:8888")
    try:
        resp = httpx.get(
            f"{omeka_base}/api/items/{omeka_item_id}",
            timeout=10,
        )
        if resp.is_success:
            item_data = resp.json()
            media_list = item_data.get("o:media", [])
            if media_list:
                media_ref = media_list[0]
                media_id = media_ref.get("o:id")
                if media_id:
                    media_resp = httpx.get(
                        f"{omeka_base}/api/media/{media_id}",
                        timeout=10,
                    )
                    if media_resp.is_success:
                        media_data = media_resp.json()
                        thumbs = media_data.get("o:thumbnail_urls", {})
                        large = thumbs.get("large")
                        if large:
                            return _rewrite_url(large, omeka_base)
                        original = media_data.get("o:original_url")
                        if original:
                            return _rewrite_url(original, omeka_base)
    except Exception:
        pass
    return thumb_url


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

    # Segment with MobileSAM
    try:
        segments = sam.segment_image(image_bytes)
    except Exception as exc:
        logger.warning("SAM segmentation failed for item %d: %s", omeka_id, exc)
        return False

    if not segments:
        logger.info("Item %d produced 0 segments, skipping", omeka_id)
        return True  # Not an error, just no segments

    # Open image for DINOv2 embedding + segment JPEG saving
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Prepare segment output directory
    segment_dir = os.path.join(SEGMENT_DIR, str(omeka_id))
    os.makedirs(segment_dir, exist_ok=True)

    points = []
    meta_entries = []
    omeka_url = item.get("omeka_url", "")
    thumb_url = item.get("thumb_url", "")

    for idx, seg in enumerate(segments):
        # Embed segment with DINOv2 CLS token
        try:
            embedding = dino.embed_segment(image, seg["mask"], seg["bbox"])
        except Exception as exc:
            logger.warning("DINOv2 embed failed for item %d segment %d: %s", omeka_id, idx, exc)
            continue

        # Save segment JPEG
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

    # Delete existing segments for this item
    httpx.post(
        f"{QDRANT_URL}/collections/{SEGMENT_COLLECTION}/points/delete",
        json={
            "filter": {
                "must": [{"key": "omeka_item_id", "match": {"value": omeka_id}}]
            }
        },
        timeout=30,
    )

    # Upsert all segment points
    resp = httpx.put(
        f"{QDRANT_URL}/collections/{SEGMENT_COLLECTION}/points",
        json={"points": points},
        timeout=60,
    )
    resp.raise_for_status()
    return True


def main():
    parser = argparse.ArgumentParser(description="Batch SAM segment embedding")
    parser.add_argument("--force", action="store_true", help="Re-embed items already in collection")
    args = parser.parse_args()

    logger.info("Starting SAM + DINOv2 batch segment embedding")
    logger.info("CLIP collection: %s", CLIP_COLLECTION)
    logger.info("Segment collection: %s", SEGMENT_COLLECTION)
    logger.info("Segment directory: %s", SEGMENT_DIR)

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
        logger.info("Nothing to embed")
        return

    # Warm up models
    logger.info("Loading MobileSAM + DINOv2 models (first run may download weights)...")
    _load_models()
    dino._get_dino()
    sam._get_sam()
    logger.info("Models loaded")

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
                idx + 1,
                len(items),
                100 * (idx + 1) / len(items),
                success,
                failed,
                rate,
                eta / 60,
            )

    elapsed = time.time() - t_start
    logger.info(
        "Done: %d succeeded, %d failed in %.1f minutes",
        success,
        failed,
        elapsed / 60,
    )


if __name__ == "__main__":
    main()

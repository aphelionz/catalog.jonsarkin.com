#!/usr/bin/env python3
"""One-time batch DINOv2 patch embedding for all artworks in the catalog.

Reads all items from the existing CLIP Qdrant collection (omeka_items),
downloads each image, extracts DINOv2 patch embeddings, and upserts to
the sarkin_motif_patches collection.

Usage:
    docker compose exec clip-api python3 batch_dino_ingest.py [--force]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
CLIP_COLLECTION = os.getenv("QDRANT_COLLECTION", "omeka_items")
DINO_COLLECTION = os.getenv("DINO_COLLECTION", "sarkin_motif_patches_518")

# Lazy import to avoid loading torch at module level
dino = None


def _load_dino():
    global dino
    if dino is None:
        from clip_api import dino as _dino
        dino = _dino


def ensure_collection():
    """Create the DINOv2 patch collection if it doesn't exist."""
    _load_dino()
    url = f"{QDRANT_URL}/collections/{DINO_COLLECTION}"
    resp = httpx.get(url, timeout=5)
    if resp.is_success:
        logger.info("Collection %s already exists", DINO_COLLECTION)
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
    logger.info("Created collection %s", DINO_COLLECTION)


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
    """Get omeka_item_ids already in the patch collection."""
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
            f"{QDRANT_URL}/collections/{DINO_COLLECTION}/points/scroll",
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
    """Rewrite Omeka-generated URLs to use the container-reachable base.

    Omeka returns URLs based on its configured server name (e.g. localhost:8888
    or catalog.jonsarkin.com), which may not be reachable from inside Docker.
    Replace the scheme+host with omeka_base so the container can fetch them.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base_parsed = urlparse(omeka_base)
    return url.replace(f"{parsed.scheme}://{parsed.netloc}", f"{base_parsed.scheme}://{base_parsed.netloc}", 1)


def get_image_url(omeka_item_id: int, thumb_url: str) -> str:
    """Get the best available image URL for an item.

    Prefer the 'large' thumbnail from Omeka API, falling back to thumb_url.
    """
    # Try Omeka API for higher resolution
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
    """Download image, extract patches, upsert to Qdrant. Returns True on success."""
    _load_dino()
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

    try:
        patch_vectors, grid_h, grid_w = dino.extract_patches(image_bytes)
    except Exception as exc:
        logger.warning("DINOv2 extraction failed for item %d: %s", omeka_id, exc)
        return False

    # Delete existing patches
    httpx.post(
        f"{QDRANT_URL}/collections/{DINO_COLLECTION}/points/delete",
        json={
            "filter": {
                "must": [{"key": "omeka_item_id", "match": {"value": omeka_id}}]
            }
        },
        timeout=30,
    )

    # Build and upsert points
    points = [
        {
            "id": omeka_id * 10000 + i,
            "vector": patch_vec,
            "payload": {
                "omeka_item_id": omeka_id,
                "omeka_url": item.get("omeka_url", ""),
                "thumb_url": item.get("thumb_url", ""),
                "patch_index": i,
            },
        }
        for i, patch_vec in enumerate(patch_vectors)
    ]

    resp = httpx.put(
        f"{QDRANT_URL}/collections/{DINO_COLLECTION}/points",
        json={"points": points},
        timeout=60,
    )
    resp.raise_for_status()
    return True


def main():
    parser = argparse.ArgumentParser(description="Batch DINOv2 patch embedding")
    parser.add_argument("--force", action="store_true", help="Re-embed items already in collection")
    args = parser.parse_args()

    logger.info("Starting DINOv2 batch patch embedding")
    logger.info("CLIP collection: %s", CLIP_COLLECTION)
    logger.info("DINOv2 collection: %s", DINO_COLLECTION)

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

    # Warm up the model with first item
    logger.info("Loading DINOv2 model (first run may download weights)...")
    _load_dino()
    # Force model load
    dino._get_dino()
    logger.info("Model loaded")

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

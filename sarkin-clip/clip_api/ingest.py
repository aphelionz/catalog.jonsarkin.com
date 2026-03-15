"""Single-item ingest: embed image + text → upsert to Qdrant + FTS index.

Reuses embeddings from clip_api.embeddings (already loaded for search)
and search_index.upsert_document for FTS.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

from clip_api import embeddings
from clip_api.config import Settings
from clip_api.preprocess import PREPROC_VERSION, compose_text_blob
from clip_api.search_index import upsert_document

logger = logging.getLogger(__name__)

CATALOG_VERSION = 2


async def fetch_image_bytes(image_url: str) -> bytes:
    """Download image bytes from a URL."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        return resp.content


async def ingest_item(
    settings: Settings,
    omeka_item_id: int,
    image_url: str,
    title: str = "",
    description: str = "",
    subjects: Optional[List[str]] = None,
    year: Optional[int] = None,
    curator_notes: Optional[List[str]] = None,
    omeka_url: str = "",
    thumb_url: str = "",
) -> Dict[str, Any]:
    """Embed a single item and upsert to Qdrant + SQLite FTS.

    Returns timing and status info.
    """
    subjects = subjects or []
    curator_notes = curator_notes or []

    t_start = time.perf_counter()

    # Download and embed image
    image_bytes = await fetch_image_bytes(image_url)
    visual_vec = embeddings.embed_image(image_bytes)

    # Build text blob and embed
    text_parts = [f"Description: {description}"] if description else []
    if subjects:
        text_parts.append(f"Subjects / Tags / Themes: {', '.join(subjects)}")
    if year:
        text_parts.append(f"Year: {year}")
    if curator_notes:
        text_parts.append(f"Curator Notes: {', '.join(curator_notes)}")
    text_blob = "\n".join(text_parts)
    text_vec = embeddings.embed_text(text_blob) if text_blob.strip() else visual_vec

    updated_at = int(time.time())

    # Upsert to Qdrant via HTTP (clip-api uses httpx, not qdrant-client SDK)
    point = {
        "id": omeka_item_id,
        "vector": {
            settings.vector_name: visual_vec,
            settings.text_vector_name: text_vec,
        },
        "payload": {
            "omeka_item_id": omeka_item_id,
            "omeka_url": omeka_url,
            "thumb_url": thumb_url,
            "omeka_description": description,
            "subjects": subjects,
            "curator_notes": curator_notes,
            "year": year,
            "text_blob": text_blob,
            "ocr_text": "",
            "ocr_text_raw": "",
            "catalog_version": CATALOG_VERSION,
            "embed_model": embeddings.EMBED_MODEL,
            "preproc_version": PREPROC_VERSION,
            "updated_at": updated_at,
        },
    }

    qdrant_url = settings.qdrant_url
    collection = settings.qdrant_collection
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{qdrant_url}/collections/{collection}/points",
            json={"points": [point]},
        )
        resp.raise_for_status()

    # Update SQLite FTS index
    upsert_document({
        "omeka_item_id": omeka_item_id,
        "catalog_version": CATALOG_VERSION,
        "omeka_url": omeka_url,
        "thumb_url": thumb_url,
        "omeka_description": description,
        "subjects": ", ".join(subjects),
        "mediums": "",
        "years": str(year) if year else "",
        "curator_notes": ", ".join(curator_notes),
        "ocr_text_raw": "",
        "ocr_text_norm": "",
        "text_blob": text_blob,
    })

    # DINOv2 patch embeddings (optional, non-blocking)
    if settings.dino_enabled:
        try:
            await ingest_dino_patches(
                settings,
                omeka_item_id,
                image_bytes=image_bytes,
                omeka_url=omeka_url,
                thumb_url=thumb_url,
            )
        except Exception:
            logger.warning("DINOv2 patch ingest failed for item %d", omeka_item_id, exc_info=True)

    # SAM segment embeddings (optional, non-blocking)
    if settings.segment_enabled:
        try:
            await ingest_segments(
                settings,
                omeka_item_id,
                image_bytes=image_bytes,
                omeka_url=omeka_url,
                thumb_url=thumb_url,
            )
        except Exception:
            logger.warning("Segment ingest failed for item %d", omeka_item_id, exc_info=True)

    elapsed = time.perf_counter() - t_start
    logger.info("Ingested item %d in %.2fs", omeka_item_id, elapsed)

    return {
        "status": "ok",
        "omeka_item_id": omeka_item_id,
        "elapsed_seconds": round(elapsed, 2),
    }


async def ingest_dino_patches(
    settings: Settings,
    omeka_item_id: int,
    *,
    image_bytes: Optional[bytes] = None,
    image_url: str = "",
    omeka_url: str = "",
    thumb_url: str = "",
) -> None:
    """Extract DINOv2 patch embeddings and upsert to the motif patches collection."""
    from clip_api import dino

    if image_bytes is None:
        image_bytes = await fetch_image_bytes(image_url)

    patch_vectors, grid_h, grid_w = dino.extract_patches(image_bytes)

    # Delete existing patches for this item (idempotent re-ingest)
    collection = settings.dino_collection
    qdrant_url = settings.qdrant_url
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{qdrant_url}/collections/{collection}/points/delete",
            json={
                "filter": {
                    "must": [{"key": "omeka_item_id", "match": {"value": omeka_item_id}}]
                }
            },
        )

    # Build points: ID = omeka_id * 10000 + patch_index
    points = [
        {
            "id": omeka_item_id * 10000 + i,
            "vector": patch_vec,
            "payload": {
                "omeka_item_id": omeka_item_id,
                "omeka_url": omeka_url,
                "thumb_url": thumb_url,
                "patch_index": i,
            },
        }
        for i, patch_vec in enumerate(patch_vectors)
    ]

    # Upsert all patches in a single request
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.put(
            f"{qdrant_url}/collections/{collection}/points",
            json={"points": points},
        )
        resp.raise_for_status()

    logger.info(
        "DINOv2: ingested %d patches for item %d",
        len(points),
        omeka_item_id,
    )


def _segment_and_embed_sync(
    image_bytes: bytes,
    omeka_item_id: int,
    segment_dir_base: str,
    omeka_url: str,
    thumb_url: str,
) -> tuple:
    """Synchronous CPU-heavy work: SAM segmentation + DINOv2 embedding + JPEG saving.

    Returns (points, meta_entries) or (None, None) if no segments produced.
    """
    import json
    import os
    from PIL import Image
    from clip_api import dino, sam

    segments = sam.segment_image(image_bytes)
    if not segments:
        return None, None

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    segment_dir = os.path.join(segment_dir_base, str(omeka_item_id))
    os.makedirs(segment_dir, exist_ok=True)

    points = []
    meta_entries = []

    for idx, seg in enumerate(segments):
        embedding = dino.embed_segment(image, seg["mask"], seg["bbox"])

        x, y, w, h = seg["bbox"]
        mask_crop = seg["mask"][y:y + h, x:x + w]
        crop_array = np.array(image.crop((x, y, x + w, y + h)))
        gray_bg = np.full_like(crop_array, 128)
        masked = np.where(mask_crop[:, :, None], crop_array, gray_bg)
        seg_image = Image.fromarray(masked)
        seg_path = os.path.join(segment_dir, f"{idx}.jpg")
        seg_image.save(seg_path, "JPEG", quality=85)

        segment_url = f"/segments/{omeka_item_id}/{idx}.jpg"

        points.append({
            "id": omeka_item_id * 1000 + idx,
            "vector": embedding,
            "payload": {
                "omeka_item_id": omeka_item_id,
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

    # Free segment mask arrays — they're large and no longer needed
    for seg in segments:
        del seg["mask"]
    del segments

    meta_path = os.path.join(segment_dir, "meta.json")
    with open(meta_path, "w") as f:
        json.dump({"omeka_item_id": omeka_item_id, "segments": meta_entries}, f)

    # Force garbage collection to prevent OOM in long batch runs
    import gc
    gc.collect()

    return points, meta_entries


async def ingest_segments(
    settings: Settings,
    omeka_item_id: int,
    *,
    image_bytes: Optional[bytes] = None,
    image_url: str = "",
    omeka_url: str = "",
    thumb_url: str = "",
) -> int:
    """Segment image with MobileSAM, embed each segment with DINOv2 CLS, upsert to Qdrant.

    Returns the number of segments ingested.
    """
    import json
    import os
    from PIL import Image
    from clip_api import dino, sam

    import asyncio

    if image_bytes is None:
        image_bytes = await fetch_image_bytes(image_url)

    # CPU-heavy work (SAM + DINOv2) runs in a thread to avoid blocking the event loop
    points, meta_entries = await asyncio.to_thread(
        _segment_and_embed_sync,
        image_bytes, omeka_item_id, settings.segment_dir, omeka_url, thumb_url,
    )

    if points is None:
        logger.info("Segments: item %d produced 0 segments, skipping", omeka_item_id)
        return 0

    # Delete existing segments for this item
    collection = settings.segment_collection
    qdrant_url = settings.qdrant_url
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{qdrant_url}/collections/{collection}/points/delete",
            json={
                "filter": {
                    "must": [{"key": "omeka_item_id", "match": {"value": omeka_item_id}}]
                }
            },
        )

    # Upsert all segment points
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.put(
            f"{qdrant_url}/collections/{collection}/points",
            json={"points": points},
        )
        resp.raise_for_status()

    logger.info(
        "Segments: ingested %d segments for item %d",
        len(points),
        omeka_item_id,
    )
    return len(points)

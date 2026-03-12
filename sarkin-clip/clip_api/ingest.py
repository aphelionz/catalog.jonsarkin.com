"""Single-item ingest: embed image + text → upsert to Qdrant + FTS index.

Reuses embeddings from clip_api.embeddings (already loaded for search)
and search_index.upsert_document for FTS.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

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

    elapsed = time.perf_counter() - t_start
    logger.info("Ingested item %d in %.2fs", omeka_item_id, elapsed)

    return {
        "status": "ok",
        "omeka_item_id": omeka_item_id,
        "elapsed_seconds": round(elapsed, 2),
    }

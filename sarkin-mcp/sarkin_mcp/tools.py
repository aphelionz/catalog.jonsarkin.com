from __future__ import annotations

import base64
import json
import logging

import httpx
import pymysql

from .clip_client import ClipClient
from .config import Config
from .db import (
    corpus_statistics,
    fetch_item_metadata,
    fulltext_search,
    get_connection,
    get_item,
    resolve_catalog_number,
    search_catalog,
)

logger = logging.getLogger(__name__)


def _conn(cfg: Config) -> pymysql.Connection:
    return get_connection(cfg)


def _json(obj: object) -> str:
    return json.dumps(obj, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool 1: get_item
# ---------------------------------------------------------------------------


def tool_get_item(
    cfg: Config,
    item_id: int | None = None,
    catalog_number: str | None = None,
) -> str:
    """Get full metadata for a specific catalog item by Omeka ID or catalog number (e.g. JS-2016-00042).

    Returns all known properties: date, type, medium, motifs, dimensions, support,
    condition, owner, provenance, transcription, signature, and collection membership.
    """
    if not item_id and not catalog_number:
        return "Error: provide either item_id or catalog_number."

    conn = _conn(cfg)
    try:
        item = get_item(conn, cfg, item_id=item_id, catalog_number=catalog_number)
    finally:
        conn.close()

    if not item:
        lookup = catalog_number or f"item {item_id}"
        return f"Item not found: {lookup}"

    return f"Found item {item.get('catalog_number', item.get('id'))}.\n\n{_json(item)}"


# ---------------------------------------------------------------------------
# Tool 2: search_catalog
# ---------------------------------------------------------------------------


def tool_search_catalog(
    cfg: Config,
    date_from: int | None = None,
    date_to: int | None = None,
    motifs: list[str] | None = None,
    work_type: str | None = None,
    medium: str | None = None,
    support: str | None = None,
    collection: str | None = None,
    owner: str | None = None,
    condition: str | None = None,
    min_width: float | None = None,
    max_width: float | None = None,
    min_height: float | None = None,
    max_height: float | None = None,
    has_transcription: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Search the catalog by structured metadata filters. Supports filtering by date range,
    motifs (AND logic), work type, medium, support, collection, owner, dimensions, and condition.

    Use for questions like "all drawings from 2016-2020 with the Cactus motif" or
    "paintings on canvas larger than 24 inches."
    """
    conn = _conn(cfg)
    try:
        result = search_catalog(
            conn, cfg,
            date_from=date_from, date_to=date_to,
            motifs=motifs, work_type=work_type, medium=medium,
            support=support, collection=collection, owner=owner,
            condition=condition,
            min_width=min_width, max_width=max_width,
            min_height=min_height, max_height=max_height,
            has_transcription=has_transcription,
            limit=limit, offset=offset,
        )
    finally:
        conn.close()

    summary = f"Found {result['total_count']} items"
    filters_desc = []
    if work_type:
        filters_desc.append(work_type)
    if motifs:
        filters_desc.append(f"motifs: {', '.join(motifs)}")
    if date_from or date_to:
        filters_desc.append(f"{date_from or '...'}-{date_to or '...'}")
    if filters_desc:
        summary += f" ({'; '.join(filters_desc)})"
    summary += f". Showing {len(result['items'])} (offset {offset})."

    return f"{summary}\n\n{_json(result)}"


# ---------------------------------------------------------------------------
# Tool 3: search_transcriptions
# ---------------------------------------------------------------------------


def tool_search_transcriptions(
    cfg: Config,
    clip: ClipClient,
    query: str,
    mode: str = "hybrid",
    limit: int = 20,
    offset: int = 0,
) -> str:
    """Full-text search across OCR transcriptions and descriptions of artworks.
    Sarkin's work often contains dense visible text. Finds works containing specific
    words or phrases.

    Modes: "hybrid" (semantic + lexical, best default), "exact" (precise word matching),
    "semantic" (meaning-based, good for conceptual queries).
    """
    try:
        clip_result = clip.search(query=query, mode=mode, limit=limit, offset=offset)
    except httpx.HTTPError as e:
        return f"clip-api search failed: {e}"

    # Enrich with MariaDB metadata
    items = clip_result.get("results", clip_result.get("items", []))
    item_ids = [it.get("omeka_item_id", it.get("id")) for it in items if it.get("omeka_item_id") or it.get("id")]
    item_ids = [iid for iid in item_ids if iid is not None]

    enriched_items = []
    if item_ids:
        conn = _conn(cfg)
        try:
            metadata = fetch_item_metadata(conn, item_ids, cfg)
        finally:
            conn.close()

        for it in items:
            iid = it.get("omeka_item_id", it.get("id"))
            meta = metadata.get(iid, {})
            enriched = {**meta}
            if "score" in it:
                enriched["score"] = it["score"]
            if "snippet" in it:
                enriched["snippet"] = it["snippet"]
            elif "text_blob" in it:
                enriched["snippet"] = it["text_blob"][:200]
            enriched_items.append(enriched)

    result = {
        "query": query,
        "mode": mode,
        "total_results": len(enriched_items),
        "items": enriched_items,
    }

    return f"Found {len(enriched_items)} results for \"{query}\" ({mode} mode).\n\n{_json(result)}"


# ---------------------------------------------------------------------------
# Tool 4: find_similar
# ---------------------------------------------------------------------------


def tool_find_similar(
    cfg: Config,
    clip: ClipClient,
    item_id: int | None = None,
    catalog_number: str | None = None,
    limit: int = 20,
) -> str:
    """Find visually similar artworks to a given catalog item using CLIP embeddings.
    Matches overall visual similarity — composition, color palette, density, style.
    """
    if not item_id and not catalog_number:
        return "Error: provide either item_id or catalog_number."

    resolved_id = item_id
    if catalog_number and not item_id:
        conn = _conn(cfg)
        try:
            resolved_id = resolve_catalog_number(conn, catalog_number)
        finally:
            conn.close()
        if not resolved_id:
            return f"Item not found: {catalog_number}"

    try:
        clip_result = clip.find_similar(resolved_id, limit=limit)
    except httpx.HTTPError as e:
        return f"clip-api similarity search failed: {e}"

    similar = clip_result.get("matches", clip_result.get("results", clip_result.get("similar", [])))
    sim_ids = [it.get("omeka_item_id", it.get("id")) for it in similar]
    sim_ids = [iid for iid in sim_ids if iid is not None]

    enriched = []
    if sim_ids:
        conn = _conn(cfg)
        try:
            metadata = fetch_item_metadata(conn, sim_ids, cfg)
            source_meta = fetch_item_metadata(conn, [resolved_id], cfg)
        finally:
            conn.close()

        for it in similar:
            iid = it.get("omeka_item_id", it.get("id"))
            meta = metadata.get(iid, {})
            entry = {**meta}
            if "score" in it:
                entry["score"] = round(it["score"], 4)
            enriched.append(entry)
    else:
        source_meta = {}

    source = source_meta.get(resolved_id, {"id": resolved_id})

    result = {"source": source, "similar_items": enriched}
    cn = source.get("catalog_number", resolved_id)
    return f"Found {len(enriched)} items similar to {cn}.\n\n{_json(result)}"


# ---------------------------------------------------------------------------
# Tool 5: search_by_image
# ---------------------------------------------------------------------------


def tool_search_by_image(
    cfg: Config,
    clip: ClipClient,
    image_base64: str | None = None,
    image_url: str | None = None,
    limit: int = 20,
) -> str:
    """Find catalog items visually similar to an uploaded image using CLIP embeddings.
    Useful for identifying unknown works, finding stylistic matches, or exploring the
    corpus from an external reference image.

    Provide either image_base64 (base64-encoded JPEG/PNG) or image_url.
    """
    if not image_base64 and not image_url:
        return "Error: provide either image_base64 or image_url."

    if image_url and not image_base64:
        try:
            resp = httpx.get(image_url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            image_bytes = resp.content
        except httpx.HTTPError as e:
            return f"Failed to fetch image from URL: {e}"
    else:
        try:
            image_bytes = base64.b64decode(image_base64)
        except Exception as e:
            return f"Invalid base64 image data: {e}"

    try:
        clip_result = clip.search_by_image(image_bytes, limit=limit)
    except httpx.HTTPError as e:
        return f"clip-api image search failed: {e}"

    items = clip_result.get("matches", clip_result.get("results", clip_result.get("similar", [])))
    item_ids = [it.get("omeka_item_id", it.get("id")) for it in items]
    item_ids = [iid for iid in item_ids if iid is not None]

    enriched = []
    if item_ids:
        conn = _conn(cfg)
        try:
            metadata = fetch_item_metadata(conn, item_ids, cfg)
        finally:
            conn.close()

        for it in items:
            iid = it.get("omeka_item_id", it.get("id"))
            meta = metadata.get(iid, {})
            entry = {**meta}
            if "score" in it:
                entry["score"] = round(it["score"], 4)
            enriched.append(entry)

    result = {"total_results": len(enriched), "items": enriched}
    return f"Found {len(enriched)} matching items.\n\n{_json(result)}"


# ---------------------------------------------------------------------------
# Tool 6: iconographic_profile
# ---------------------------------------------------------------------------


def tool_iconographic_profile(
    cfg: Config,
    clip: ClipClient,
    item_id: int | None = None,
    catalog_number: str | None = None,
) -> str:
    """Get the iconographic (motif) rarity profile for an artwork. Shows which of
    Sarkin's recurring motifs appear, how common each is across the corpus, and an
    overall rarity score (class 1-5, where 5 is rarest).
    """
    if not item_id and not catalog_number:
        return "Error: provide either item_id or catalog_number."

    resolved_id = item_id
    if catalog_number and not item_id:
        conn = _conn(cfg)
        try:
            resolved_id = resolve_catalog_number(conn, catalog_number)
        finally:
            conn.close()
        if not resolved_id:
            return f"Item not found: {catalog_number}"

    try:
        result = clip.iconography(resolved_id)
    except httpx.HTTPError as e:
        return f"clip-api iconography lookup failed: {e}"

    cn = catalog_number or f"item {resolved_id}"
    rarity = result.get("rarity_class_number", result.get("rarity_class", "?"))
    return f"Iconographic profile for {cn} (rarity class {rarity}).\n\n{_json(result)}"


# ---------------------------------------------------------------------------
# Tool 7: fulltext_search
# ---------------------------------------------------------------------------


def tool_fulltext_search(
    cfg: Config,
    query: str,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """Full-text search across all item transcriptions using direct SQL matching.
    Finds exact substring matches in the visible text on artworks. No external API needed.
    Good for finding specific names, words, or phrases (e.g. "Jim", "Brancusi", "Robert Johnson").
    """
    conn = _conn(cfg)
    try:
        result = fulltext_search(conn, cfg, query=query, limit=limit, offset=offset)
    finally:
        conn.close()

    return f"Found {result['total_count']} items containing \"{query}\". Showing {len(result['items'])} (offset {offset}).\n\n{_json(result)}"


# ---------------------------------------------------------------------------
# Tool 8: corpus_statistics
# ---------------------------------------------------------------------------


def tool_corpus_statistics(
    cfg: Config,
    breakdown: str = "summary",
) -> str:
    """Get aggregate statistics about the Jon Sarkin catalog. Total item counts,
    breakdowns by work type, motif frequency, date range coverage, collection sizes.

    Breakdown options: summary, by_year, by_type, by_motif, by_support, by_medium,
    by_collection, by_condition.
    """
    conn = _conn(cfg)
    try:
        result = corpus_statistics(conn, breakdown=breakdown)
    finally:
        conn.close()

    if breakdown == "summary":
        total = result.get("total_items", "?")
        return f"Catalog contains {total} items.\n\n{_json(result)}"

    entries = result.get("breakdown", {})
    return f"{breakdown}: {len(entries)} categories.\n\n{_json(result)}"

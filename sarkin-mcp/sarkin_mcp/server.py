from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from .clip_client import ClipClient
from .config import Config
from .tools import (
    tool_corpus_statistics,
    tool_find_similar,
    tool_fulltext_search,
    tool_get_item,
    tool_iconographic_profile,
    tool_search_by_image,
    tool_search_catalog,
    tool_search_transcriptions,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

cfg = Config()
clip = ClipClient(cfg)

mcp = FastMCP(
    "sarkin-catalog",
    instructions=(
        "MCP server for the Jon Sarkin catalog raisonné. "
        "Provides structured access to ~4,400 cataloged artworks via MariaDB "
        "and CLIP-based visual/semantic search via clip-api."
    ),
    host=cfg.mcp_host,
    port=cfg.mcp_port,
    log_level="WARNING",
)


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_item(item_id: int | None = None, catalog_number: str | None = None) -> str:
    """Get full metadata for a specific catalog item by Omeka ID or catalog number (e.g. JS-2016-00042).

    Returns all known properties: date, type, medium, motifs, dimensions, support,
    condition, owner, provenance, transcription, signature, and collection membership.
    """
    return tool_get_item(cfg, item_id=item_id, catalog_number=catalog_number)


@mcp.tool()
def search_catalog(
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

    Work types: Drawing, Painting, Collage, Mixed Media, Sculpture, Print, Other.
    Supports: Paper, Cardboard, Canvas, Board, Wood, Found Object, Album Sleeve, etc.
    Conditions: Excellent, Good, Fair, Poor, Not Examined.
    """
    return tool_search_catalog(
        cfg,
        date_from=date_from, date_to=date_to, motifs=motifs,
        work_type=work_type, medium=medium, support=support,
        collection=collection, owner=owner, condition=condition,
        min_width=min_width, max_width=max_width,
        min_height=min_height, max_height=max_height,
        has_transcription=has_transcription, limit=limit, offset=offset,
    )


@mcp.tool()
def search_transcriptions(
    query: str,
    mode: str = "hybrid",
    limit: int = 20,
    offset: int = 0,
) -> str:
    """Full-text search across OCR transcriptions and descriptions of artworks.
    Sarkin's work often contains dense visible text — words, phrases, names, fragments.

    Modes: "hybrid" (semantic + lexical, best default), "exact" (precise word matching),
    "semantic" (meaning-based, good for conceptual queries).
    """
    return tool_search_transcriptions(cfg, clip, query=query, mode=mode, limit=limit, offset=offset)


@mcp.tool()
def find_similar(
    item_id: int | None = None,
    catalog_number: str | None = None,
    limit: int = 20,
) -> str:
    """Find visually similar artworks to a given catalog item using CLIP embeddings.
    Matches overall visual similarity — composition, color palette, density, style.
    """
    return tool_find_similar(cfg, clip, item_id=item_id, catalog_number=catalog_number, limit=limit)


@mcp.tool()
def search_by_image(
    image_base64: str | None = None,
    image_url: str | None = None,
    limit: int = 20,
) -> str:
    """Find catalog items visually similar to an uploaded image using CLIP embeddings.
    Useful for identifying unknown works or finding stylistic matches.

    Provide either image_base64 (base64-encoded JPEG/PNG) or image_url.
    """
    return tool_search_by_image(cfg, clip, image_base64=image_base64, image_url=image_url, limit=limit)


@mcp.tool()
def iconographic_profile(
    item_id: int | None = None,
    catalog_number: str | None = None,
) -> str:
    """Get the iconographic (motif) rarity profile for an artwork. Shows which of
    Sarkin's recurring motifs appear, how common each is across the corpus, and an
    overall rarity score (class 1-5, where 5 is rarest).
    """
    return tool_iconographic_profile(cfg, clip, item_id=item_id, catalog_number=catalog_number)


@mcp.tool()
def fulltext_search(
    query: str,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """Full-text search across all item transcriptions using direct SQL matching.
    Finds exact substring matches in the visible text on artworks. No external API needed.
    Good for finding specific names, words, or phrases (e.g. "Jim", "Brancusi", "Robert Johnson").
    """
    return tool_fulltext_search(cfg, query=query, limit=limit, offset=offset)


@mcp.tool()
def corpus_statistics(breakdown: str = "summary") -> str:
    """Get aggregate statistics about the Jon Sarkin catalog.

    Breakdown options: summary, by_year, by_type, by_motif, by_support, by_medium,
    by_collection, by_condition.
    """
    return tool_corpus_statistics(cfg, breakdown=breakdown)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    transport = cfg.transport.lower()
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()

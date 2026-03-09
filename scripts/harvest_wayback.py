#!/usr/bin/env python3
"""
harvest_wayback.py — Harvest Jon Sarkin's writings from the Wayback Machine.

Queries the Wayback Machine CDX API for all archived content from jsarkin.com,
fetches HTML pages, extracts text content preserving formatting, deduplicates
across Drupal and WordPress eras, and outputs Omeka S CSV + master JSON.

Usage:
  # Full pipeline (discover, fetch, extract, output)
  python3 scripts/harvest_wayback.py

  # Individual phases
  python3 scripts/harvest_wayback.py discover    # CDX query + classify URLs
  python3 scripts/harvest_wayback.py fetch       # Fetch HTML (resumable)
  python3 scripts/harvest_wayback.py extract     # Parse HTML + deduplicate
  python3 scripts/harvest_wayback.py output      # Generate CSV, JSON, report

  # Options
  python3 scripts/harvest_wayback.py discover --dry-run  # Preview URL counts
  python3 scripts/harvest_wayback.py fetch --limit 10    # Fetch first N only
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(line_buffering=True)

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARVEST_DIR = PROJECT_ROOT / "harvest"
RAW_HTML_DIR = HARVEST_DIR / "raw_html"
TEXTS_DIR = HARVEST_DIR / "texts"
IMAGES_DIR = HARVEST_DIR / "images"


def setup_logging() -> None:
    """Configure logging to both file and stderr."""
    HARVEST_DIR.mkdir(parents=True, exist_ok=True)

    log_format = "%(asctime)s %(levelname)-8s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(HARVEST_DIR / "harvest.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


# ── Phase implementations ────────────────────────────────────────────────

def do_discover(args: argparse.Namespace) -> list:
    """Phase 1: Query CDX API and classify URLs."""
    from harvest.cdx import query_cdx, select_best_captures
    from harvest.classify import classify_all

    log = logging.getLogger(__name__)
    log.info("=== Phase 1: Discovery ===")

    # Query CDX API
    records = query_cdx(HARVEST_DIR)
    log.info("CDX returned %d total captures", len(records))

    # Select best capture per unique URL
    best = select_best_captures(records)
    log.info("Selected %d unique URLs", len(best))

    # Classify URLs
    content_urls = classify_all(best)
    log.info("Classified %d content URLs", len(content_urls))

    # Print scope summary
    era_counts = Counter(u.era for u in content_urls)
    section_counts = Counter(u.section for u in content_urls)

    print("\n" + "=" * 60)
    print("  DISCOVERY SUMMARY")
    print("=" * 60)
    print(f"  Total CDX captures:         {len(records)}")
    print(f"  Unique URLs:                {len(best)}")
    print(f"  Content pages to fetch:     {len(content_urls)}")
    print()
    print("  By era:")
    for era, count in sorted(era_counts.items()):
        print(f"    {era:20s}  {count}")
    print()
    print("  By section:")
    for section, count in sorted(section_counts.items()):
        print(f"    {section:20s}  {count}")
    print("=" * 60 + "\n")

    if args.dry_run:
        print("DRY RUN — not saving URL list.")
        return content_urls

    # Save classified URLs
    url_data = [
        {
            "original_url": u.original_url,
            "timestamp": u.timestamp,
            "digest": u.digest,
            "era": u.era,
            "section": u.section,
            "url_type": u.url_type,
            "slug": u.slug,
            "node_id": u.node_id,
            "wp_post_id": u.wp_post_id,
        }
        for u in content_urls
    ]
    urls_path = HARVEST_DIR / "urls_to_fetch.json"
    urls_path.write_text(json.dumps(url_data, indent=2), encoding="utf-8")
    log.info("Saved %d content URLs to %s", len(content_urls), urls_path)

    return content_urls


def do_fetch(args: argparse.Namespace) -> dict[str, str]:
    """Phase 2: Fetch archived HTML pages."""
    from harvest.classify import ClassifiedUrl
    from harvest.fetch import fetch_all
    from harvest.models import ClassifiedUrl as CU

    log = logging.getLogger(__name__)
    log.info("=== Phase 2: Fetch ===")

    # Load URL list
    urls = _load_urls()
    log.info("Loaded %d content URLs to fetch", len(urls))

    limit = args.limit if hasattr(args, "limit") and args.limit else None
    if limit:
        log.info("Limiting to first %d URLs", limit)

    results = fetch_all(urls, RAW_HTML_DIR, limit=limit)

    # Save fetch results
    results_path = HARVEST_DIR / "fetch_results.json"
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    ok = sum(1 for v in results.values() if v == "ok")
    cached = sum(1 for v in results.values() if v == "cached")
    errors = sum(1 for v in results.values() if v.startswith("error"))
    print(f"\nFetch complete: {ok} new, {cached} cached, {errors} errors")

    return results


def do_extract(args: argparse.Namespace) -> list:
    """Phase 3: Extract content from HTML + deduplicate."""
    from harvest.extract import extract_all
    from harvest.dedup import deduplicate, get_unique_pieces

    log = logging.getLogger(__name__)
    log.info("=== Phase 3: Extract + Deduplicate ===")

    urls = _load_urls()
    log.info("Extracting from %d pages", len(urls))

    pieces = extract_all(urls, RAW_HTML_DIR, IMAGES_DIR)
    log.info("Extracted %d pieces", len(pieces))

    # Deduplicate
    pieces, merged_pairs = deduplicate(pieces)
    unique = get_unique_pieces(pieces)
    log.info("After dedup: %d unique pieces (%d duplicates merged)",
             len(unique), len(merged_pairs))

    # Save intermediate state
    _save_pieces(pieces, merged_pairs)

    type_counts = Counter(p.content_type for p in unique)
    print(f"\nExtraction complete: {len(unique)} unique pieces")
    print("  By type:", dict(type_counts))

    return pieces


def do_output(args: argparse.Namespace) -> None:
    """Phase 4: Generate all output files."""
    from harvest.output import assign_ids, write_text_files, write_master_json, write_omeka_csv, write_harvest_report

    log = logging.getLogger(__name__)
    log.info("=== Phase 4: Output ===")

    pieces, merged_pairs = _load_pieces()
    log.info("Loaded %d pieces (%d merged pairs)", len(pieces), len(merged_pairs))

    # Load fetch results and CDX stats
    fetch_results = {}
    fr_path = HARVEST_DIR / "fetch_results.json"
    if fr_path.exists():
        fetch_results = json.loads(fr_path.read_text())

    cdx_path = HARVEST_DIR / "cdx_raw_results.json"
    total_cdx = 0
    if cdx_path.exists():
        total_cdx = len(json.loads(cdx_path.read_text()))

    urls_path = HARVEST_DIR / "urls_to_fetch.json"
    total_content = 0
    if urls_path.exists():
        total_content = len(json.loads(urls_path.read_text()))

    # Assign IDs
    ordered = assign_ids(pieces)

    # Generate outputs
    write_text_files(pieces, TEXTS_DIR)
    write_master_json(ordered, pieces, HARVEST_DIR, merged_pairs)
    write_omeka_csv(ordered, HARVEST_DIR)
    write_harvest_report(ordered, pieces, merged_pairs, fetch_results,
                         total_cdx, total_content, HARVEST_DIR)

    print(f"\nOutput complete:")
    print(f"  {len(ordered)} items in sarkin_jsarkin_complete.json")
    print(f"  {len(ordered)} rows in omeka_import.csv")
    print(f"  Text files in harvest/texts/")
    print(f"  Report in harvest/harvest_report.txt")


# ── Helpers ──────────────────────────────────────────────────────────────

def _load_urls():
    """Load classified URLs from urls_to_fetch.json."""
    from harvest.models import ClassifiedUrl

    urls_path = HARVEST_DIR / "urls_to_fetch.json"
    if not urls_path.exists():
        print("ERROR: No urls_to_fetch.json found. Run 'discover' first.", file=sys.stderr)
        sys.exit(1)

    data = json.loads(urls_path.read_text())
    return [
        ClassifiedUrl(
            original_url=d["original_url"],
            timestamp=d["timestamp"],
            digest=d["digest"],
            era=d["era"],
            section=d["section"],
            url_type=d["url_type"],
            slug=d["slug"],
            node_id=d.get("node_id"),
            wp_post_id=d.get("wp_post_id"),
        )
        for d in data
    ]


def _save_pieces(pieces, merged_pairs):
    """Save extracted pieces to intermediate JSON for the output phase."""
    data = {
        "merged_pairs": merged_pairs,
        "pieces": [
            {
                "title": p.title,
                "body": p.body,
                "content_type": p.content_type,
                "date_iso": p.date_iso,
                "date_display": p.date_display,
                "era": p.era,
                "section": p.section,
                "original_url": p.original_url,
                "wayback_url": p.wayback_url,
                "timestamp": p.timestamp,
                "slug": p.slug,
                "node_id": p.node_id,
                "wp_post_id": p.wp_post_id,
                "word_count": p.word_count,
                "categories": p.categories,
                "dedup_key": p.dedup_key,
                "dedup_group": p.dedup_group,
                "all_source_urls": p.all_source_urls,
                "images": [
                    {
                        "original_src": img.original_src,
                        "wayback_url": img.wayback_url,
                        "alt_text": img.alt_text,
                        "local_filename": img.local_filename,
                        "position": img.position,
                        "caption": img.caption,
                        "credit": img.credit,
                        "title_attr": img.title_attr,
                    }
                    for img in p.images
                ],
            }
            for p in pieces
        ],
    }
    path = HARVEST_DIR / "extracted_pieces.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_pieces():
    """Load extracted pieces from intermediate JSON."""
    from harvest.models import ExtractedPiece, ExtractedImage

    path = HARVEST_DIR / "extracted_pieces.json"
    if not path.exists():
        print("ERROR: No extracted_pieces.json found. Run 'extract' first.", file=sys.stderr)
        sys.exit(1)

    data = json.loads(path.read_text())
    pieces = []
    for d in data["pieces"]:
        images = [
            ExtractedImage(
                original_src=img["original_src"],
                wayback_url=img["wayback_url"],
                alt_text=img["alt_text"],
                local_filename=img["local_filename"],
                position=img["position"],
                caption=img.get("caption", ""),
                credit=img.get("credit", ""),
                title_attr=img.get("title_attr", ""),
            )
            for img in d.get("images", [])
        ]
        pieces.append(ExtractedPiece(
            title=d["title"],
            body=d["body"],
            content_type=d["content_type"],
            date_iso=d["date_iso"],
            date_display=d["date_display"],
            era=d["era"],
            section=d["section"],
            original_url=d["original_url"],
            wayback_url=d["wayback_url"],
            timestamp=d["timestamp"],
            slug=d["slug"],
            node_id=d.get("node_id"),
            wp_post_id=d.get("wp_post_id"),
            word_count=d["word_count"],
            categories=d.get("categories", []),
            dedup_key=d.get("dedup_key", ""),
            dedup_group=d.get("dedup_group"),
            all_source_urls=d.get("all_source_urls", []),
            images=images,
        ))
    return pieces, data.get("merged_pairs", [])


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Harvest Jon Sarkin's jsarkin.com from the Wayback Machine.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # discover
    p_discover = subparsers.add_parser("discover", help="CDX query + classify URLs")
    p_discover.add_argument("--dry-run", action="store_true", help="Preview without saving")

    # fetch
    p_fetch = subparsers.add_parser("fetch", help="Fetch archived HTML pages (resumable)")
    p_fetch.add_argument("--limit", type=int, help="Fetch only first N pages")

    # extract
    p_extract = subparsers.add_parser("extract", help="Extract content + deduplicate")

    # output
    p_output = subparsers.add_parser("output", help="Generate CSV, JSON, and report")

    # Top-level options (for full pipeline)
    parser.add_argument("--dry-run", action="store_true", help="Preview discovery only")
    parser.add_argument("--limit", type=int, help="Limit fetch to first N pages")

    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger(__name__)

    if args.command == "discover":
        do_discover(args)
    elif args.command == "fetch":
        do_fetch(args)
    elif args.command == "extract":
        do_extract(args)
    elif args.command == "output":
        do_output(args)
    else:
        # Full pipeline
        log.info("Running full harvest pipeline")
        content_urls = do_discover(args)
        if args.dry_run:
            return
        do_fetch(args)
        do_extract(args)
        do_output(args)
        log.info("Harvest complete!")


if __name__ == "__main__":
    main()

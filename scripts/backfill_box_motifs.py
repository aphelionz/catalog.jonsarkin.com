#!/usr/bin/env python3
"""
backfill_box_motifs.py — Add a motif matching each item's box category.

Parses the box field (e.g. "Comic (Box 2) 123") to extract the category
prefix ("Comic"), then adds it to dcterms:subject if not already present.

Usage:
  python scripts/backfill_box_motifs.py --dry-run   # Preview changes
  python scripts/backfill_box_motifs.py              # Apply changes
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(line_buffering=True)

import argparse
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich_metadata import (
    OMEKA_BASE,
    PROP,
    get_items_page,
    extract_value,
    extract_all_values,
    omeka_patch,
    _clean_value,
    literal_value,
)

# ── Configuration ────────────────────────────────────────────────────────

PATCH_WORKERS = 10

# Regex: capture everything before first standalone number or "(box"
BOX_CATEGORY_RE = re.compile(r"^(.+?)(?:\s*\(box\b|\s+\d)", re.IGNORECASE)

# Known typo corrections
TYPO_CORRECTIONS = {
    "ladie": "Ladies",
}

# Terms that should stay uppercase
ABBREVIATIONS = {"CBM", "MRI"}


# ── Parsing & normalization ──────────────────────────────────────────────

def parse_box_category(box_value: str) -> str | None:
    """Extract the category prefix from a box value like 'Comic (Box 2) 123'."""
    if not box_value or not box_value.strip():
        return None
    m = BOX_CATEGORY_RE.match(box_value.strip())
    if not m:
        return None  # purely numeric like "97"
    cat = m.group(1).strip()
    if not cat or not any(c.isalpha() for c in cat):
        return None
    return cat


def normalize_category(raw: str) -> str:
    """Normalize a raw box category to vocab-style label."""
    low = raw.lower()
    if low in TYPO_CORRECTIONS:
        return TYPO_CORRECTIONS[low]
    if raw.upper() in ABBREVIATIONS:
        return raw.upper()
    # Title-case each word, handling "/" compounds like "spiral/mouth"
    return "/".join(
        " ".join(word.capitalize() for word in part.split())
        for part in raw.split("/")
    )


# ── Item processing ──────────────────────────────────────────────────────

def compute_box_motif(item: dict) -> str | None:
    """Extract and normalize the box category for an item."""
    box_val = extract_value(item, "schema:box")
    if not box_val:
        return None
    raw = parse_box_category(box_val)
    if not raw:
        return None
    return normalize_category(raw)


def needs_box_motif(item: dict) -> tuple[str, bool]:
    """Check if item needs a box-derived motif added. Returns (motif, needed)."""
    motif = compute_box_motif(item)
    if not motif:
        return ("", False)
    existing = extract_all_values(item, "dcterms:subject")
    if motif in existing:
        return (motif, False)
    return (motif, True)


def build_payload(item: dict, new_motif: str) -> dict:
    """Build a PATCH payload that appends new_motif to existing dcterms:subject."""
    payload = {}

    # Copy all vocabulary properties (skip Omeka system keys o:*)
    for key, val in item.items():
        if ":" in key and not key.startswith("o:") and isinstance(val, list):
            payload[key] = [_clean_value(v) for v in val if isinstance(v, dict)]

    # Omit o:resource_template to avoid template validation
    for sys_key in ["o:resource_class", "o:item_set", "o:media", "o:is_public"]:
        if sys_key in item:
            payload[sys_key] = item[sys_key]

    # Append the new motif to existing subjects
    subjects = payload.get("dcterms:subject", [])
    subjects.append(literal_value(PROP["dcterms:subject"], new_motif))
    payload["dcterms:subject"] = subjects

    return payload


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Add box-derived motifs to items.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Limit items processed")
    args = parser.parse_args()

    print(f"Fetching items from {OMEKA_BASE}...")
    items = []
    page = 1
    target = args.limit or float("inf")

    while len(items) < target:
        batch, total = get_items_page(page)
        if page == 1:
            print(f"Total items: {total}")
        if not batch:
            break
        items.extend(batch)
        page += 1

    if args.limit:
        items = items[:args.limit]

    # Build work list
    work = []
    skipped_no_box = 0
    skipped_already = 0
    for item in items:
        motif, needed = needs_box_motif(item)
        if not motif:
            skipped_no_box += 1
        elif not needed:
            skipped_already += 1
        else:
            work.append((item, motif))

    print(f"Scanned {len(items)} items: {len(work)} need motif added, "
          f"{skipped_already} already have it, {skipped_no_box} have no parseable box.\n")

    if args.dry_run:
        # Collect category counts for summary
        counts: dict[str, int] = {}
        for item, motif in work:
            counts[motif] = counts.get(motif, 0) + 1
            item_id = item["o:id"]
            identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
            box_val = extract_value(item, "schema:box")
            print(f"  [{identifier}] box=\"{box_val}\" → add motif \"{motif}\"")

        print(f"\nSummary by category:")
        for cat, count in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")
        print(f"\nDone. Would patch: {len(work)}")
        return

    # Parallel patching
    patched = 0
    failed = 0
    lock = threading.Lock()
    counter = [0]

    def patch_one(item, motif):
        item_id = item["o:id"]
        identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
        payload = build_payload(item, motif)
        omeka_patch(item_id, payload)
        with lock:
            counter[0] += 1
            n = counter[0]
        print(f"  [{n}/{len(work)}] {identifier}: +{motif}")

    with ThreadPoolExecutor(max_workers=PATCH_WORKERS) as pool:
        futures = {pool.submit(patch_one, item, motif): item for item, motif in work}
        for future in as_completed(futures):
            try:
                future.result()
                patched += 1
            except Exception as e:
                item = futures[future]
                identifier = extract_value(item, "dcterms:identifier") or f"item-{item['o:id']}"
                print(f"  FAILED {identifier}: {e}")
                failed += 1

    print(f"\nDone. Patched: {patched}, failed: {failed}, "
          f"skipped: {skipped_already + skipped_no_box}")


if __name__ == "__main__":
    main()

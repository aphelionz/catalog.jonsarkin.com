#!/usr/bin/env python3
"""
backfill_defaults.py — Fill in default values for fields missing across all items.

Usage:
  python scripts/backfill_defaults.py --dry-run   # Preview changes
  python scripts/backfill_defaults.py              # Apply changes
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(line_buffering=True)

import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from enrich_metadata import (
    OMEKA_BASE,
    RESOURCE_TEMPLATE_ID,
    PROP,
    get_items_page,
    extract_value,
    omeka_patch,
    _clean_value,
    literal_value,
    resource_value,
)

# ── Configuration ────────────────────────────────────────────────────────

CREATOR_ITEM_ID = 3   # Jon Sarkin Person item
RESOURCE_CLASS_ID = 225  # schema:VisualArtwork
PATCH_WORKERS = 10


# ── Helpers ──────────────────────────────────────────────────────────────

def is_empty(item: dict, term: str, kind: str = "literal") -> bool:
    """Check if an item is missing a field."""
    vals = item.get(term, [])
    if not vals:
        return True
    if kind == "resource":
        return not any(v.get("value_resource_id") for v in vals)
    return not any(v.get("@value", "").strip() for v in vals)


def compute_changes(item: dict) -> dict:
    """Determine what fields need setting on this item. Returns {term: value_dict}."""
    changes = {}

    # Location
    if is_empty(item, "dcterms:spatial"):
        changes["dcterms:spatial"] = literal_value(PROP["dcterms:spatial"], "Gloucester, MA")

    # Creator
    if is_empty(item, "schema:creator", "resource"):
        changes["schema:creator"] = resource_value(PROP["schema:creator"], CREATOR_ITEM_ID)

    # Work Type
    if is_empty(item, "dcterms:type"):
        changes["dcterms:type"] = literal_value(PROP["dcterms:type"], "Drawing")

    # Support
    if is_empty(item, "schema:artworkSurface"):
        changes["schema:artworkSurface"] = literal_value(PROP["schema:artworkSurface"], "Album Sleeve")

    # Owner
    if is_empty(item, "bibo:owner"):
        changes["bibo:owner"] = literal_value(PROP["bibo:owner"], "The Jon Sarkin Estate")

    # Height/Width — set to 12.5 if support is Album Sleeve and dimensions missing
    support = extract_value(item, "schema:artworkSurface")
    if not support:
        support = "Album Sleeve"  # we're setting it above
    if support == "Album Sleeve":
        if is_empty(item, "schema:height"):
            changes["schema:height"] = literal_value(PROP["schema:height"], "12.5")
        if is_empty(item, "schema:width"):
            changes["schema:width"] = literal_value(PROP["schema:width"], "12.5")

    # Box — copy current title to schema:box if box is empty
    if is_empty(item, "schema:box"):
        title = extract_value(item, "dcterms:title")
        if title:
            changes["schema:box"] = literal_value(PROP["schema:box"], title)

    # Resource class (schema:VisualArtwork) — sentinel so build_payload runs
    if not item.get("o:resource_class"):
        changes["_resource_class"] = True

    # Resource template — sentinel so build_payload runs
    if not item.get("o:resource_template"):
        changes["_resource_template"] = True

    return changes


def build_payload(item: dict, changes: dict) -> dict:
    """Build a PATCH payload preserving all existing properties."""
    payload = {}

    # Copy vocabulary properties (skip Omeka system keys o:*)
    for key, val in item.items():
        if ":" in key and not key.startswith("o:") and isinstance(val, list):
            payload[key] = [_clean_value(v) for v in val if isinstance(v, dict)]

    # Set resource template + class if missing
    if item.get("o:resource_template"):
        payload["o:resource_template"] = {"o:id": RESOURCE_TEMPLATE_ID}
    else:
        # New items from prod lack a template; assign it now.
        # Template requires a title — use the existing o:title.
        payload["o:resource_template"] = {"o:id": RESOURCE_TEMPLATE_ID}
        title = item.get("o:title", f"Untitled-{item['o:id']}")
        if "dcterms:title" not in payload or not payload["dcterms:title"]:
            payload["dcterms:title"] = [literal_value(PROP["dcterms:title"], title)]

    if item.get("o:resource_class"):
        payload["o:resource_class"] = item["o:resource_class"]
    else:
        payload["o:resource_class"] = {"o:id": RESOURCE_CLASS_ID}

    for sys_key in ["o:item_set", "o:media", "o:is_public"]:
        if sys_key in item:
            payload[sys_key] = item[sys_key]

    # Ensure dcterms:identifier exists
    has_identifier = any(
        v.get("@value", "").strip() for v in payload.get("dcterms:identifier", [])
    )
    if not has_identifier:
        item_id = item["o:id"]
        date_val = extract_value(item, "dcterms:date")
        year = date_val[:4] if date_val and date_val[:4].isdigit() else "0000"
        temp_id = f"JS-{year}-T{item_id}"
        payload["dcterms:identifier"] = [literal_value(PROP["dcterms:identifier"], temp_id)]

    # Apply changes (skip internal sentinel keys starting with _)
    for term, value_dict in changes.items():
        if not term.startswith("_"):
            payload[term] = [value_dict]

    return payload


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill default metadata values.")
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
    for item in items:
        changes = compute_changes(item)
        if changes:
            work.append((item, changes))

    print(f"Scanning {len(items)} items: {len(work)} need patching, {len(items) - len(work)} already complete.\n")

    if args.dry_run:
        for item, changes in work:
            item_id = item["o:id"]
            identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
            labels = [term.lstrip("_").split(":")[-1] for term in changes]
            print(f"  [{identifier}] would set: {', '.join(labels)}")
        print(f"\nDone. Would patch: {len(work)}")
        return

    # Parallel patching
    patched = 0
    failed = 0
    lock = threading.Lock()
    counter = [0]

    def patch_one(item, changes):
        item_id = item["o:id"]
        identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
        labels = [term.lstrip("_").split(":")[-1] for term in changes]
        payload = build_payload(item, changes)
        omeka_patch(item_id, payload)
        with lock:
            counter[0] += 1
            n = counter[0]
        print(f"  [{n}/{len(work)}] {identifier}: {', '.join(labels)}")

    with ThreadPoolExecutor(max_workers=PATCH_WORKERS) as pool:
        futures = {pool.submit(patch_one, item, changes): item for item, changes in work}
        for future in as_completed(futures):
            try:
                future.result()
                patched += 1
            except Exception as e:
                item = futures[future]
                identifier = extract_value(item, "dcterms:identifier") or f"item-{item['o:id']}"
                print(f"  FAILED {identifier}: {e}")
                failed += 1

    print(f"\nDone. Patched: {patched}, failed: {failed}, skipped: {len(items) - len(work)}")


if __name__ == "__main__":
    main()

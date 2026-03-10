#!/usr/bin/env python3
"""
apply_jim_locations_vocab.py — Create a controlled vocabulary from extracted
JIM Stories locations and add facetable dcterms:spatial values + browse facet.

Reads the cached location data from extract_jim_locations.py, creates a
custom vocabulary, inserts per-location dcterms:spatial values, and adds
a Location facet to the faceted browse page.

Usage:
  python scripts/apply_jim_locations_vocab.py            # dry-run (default)
  python scripts/apply_jim_locations_vocab.py --apply     # write to DB

Prerequisite: scripts/jim_locations_cache.json must exist (run extract_jim_locations.py first).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# ── Constants ──────────────────────────────────────────────────────────────

ITEM_SET_ID = 8020              # JIM Stories
PROPERTY_ID_SPATIAL = 40        # dcterms:spatial
PROPERTY_ID_COVERAGE = 14       # dcterms:coverage (JSON blob source)
CUSTOM_VOCAB_ID = 10            # next available
CUSTOM_VOCAB_TYPE = "customvocab:10"
CUSTOM_VOCAB_LABEL = "JIM Story Locations"

FACETED_BROWSE_CATEGORY_ID = 2  # "Facets" category
FACET_POSITION = 5              # after Narrative Voice (4), before Motifs
FACET_TRUNCATE = "20"           # show 20 initially, expand for more


# ── DB helpers ─────────────────────────────────────────────────────────────

def db_query(sql: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db",
         "mariadb", "-u", "root", "-proot", "omeka",
         "--batch", "--raw", "-e", sql],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def db_execute(sql: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "db",
         "mariadb", "-u", "root", "-proot", "omeka",
         "-e", sql],
        capture_output=True, text=True, check=True,
    )


# ── Logic ──────────────────────────────────────────────────────────────────

def load_cache(cache_path: str) -> list[dict]:
    with open(cache_path) as f:
        return json.load(f)


def collect_unique_locations(cache: list[dict]) -> list[str]:
    """Deduplicate location names across all items, sorted alphabetically."""
    names = set()
    for item in cache:
        for loc in item.get("locations", []):
            names.add(loc["location_name"])
    return sorted(names, key=str.casefold)


def check_vocab_exists() -> bool:
    out = db_query(f"SELECT id FROM custom_vocab WHERE id = {CUSTOM_VOCAB_ID};")
    lines = out.strip().split("\n")
    return len(lines) >= 2


def check_existing_spatial_values() -> set[int]:
    """Return item IDs that already have customvocab:10 spatial values."""
    sql = (
        f"SELECT DISTINCT resource_id FROM value "
        f"WHERE property_id = {PROPERTY_ID_SPATIAL} AND type = '{CUSTOM_VOCAB_TYPE}';"
    )
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        return set()
    return {int(row) for row in out[1:] if row.strip()}


def check_facet_exists() -> bool:
    sql = (
        f"SELECT id FROM faceted_browse_facet "
        f"WHERE category_id = {FACETED_BROWSE_CATEGORY_ID} AND name = 'Location';"
    )
    out = db_query(sql).strip().split("\n")
    return len(out) >= 2


def create_vocab(location_names: list[str], apply: bool) -> None:
    terms_json = json.dumps(location_names, ensure_ascii=False)
    escaped = terms_json.replace("\\", "\\\\").replace("'", "\\'")
    sql = (
        f"INSERT INTO custom_vocab (id, owner_id, label, lang, terms, item_set_id) "
        f"VALUES ({CUSTOM_VOCAB_ID}, 1, '{CUSTOM_VOCAB_LABEL}', '', '{escaped}', NULL);"
    )
    if apply:
        db_execute(sql)
        print(f"  Created custom vocab {CUSTOM_VOCAB_ID} with {len(location_names)} terms.")
    else:
        print(f"  Would create custom vocab {CUSTOM_VOCAB_ID} with {len(location_names)} terms.")


def insert_spatial_values(cache: list[dict], existing: set[int], apply: bool) -> int:
    """Insert dcterms:spatial values for each location per item."""
    total = 0
    for item in cache:
        item_id = item["item_id"]
        if item_id in existing:
            continue
        locations = item.get("locations", [])
        if not locations:
            continue
        for loc in locations:
            name = loc["location_name"]
            escaped = name.replace("\\", "\\\\").replace("'", "\\'")
            sql = (
                f"INSERT INTO `value` (resource_id, property_id, type, `value`, is_public) "
                f"VALUES ({item_id}, {PROPERTY_ID_SPATIAL}, '{CUSTOM_VOCAB_TYPE}', "
                f"'{escaped}', 1);"
            )
            if apply:
                db_execute(sql)
            total += 1
    return total


def create_facet(location_names: list[str], apply: bool) -> None:
    """Add Location facet to the faceted browse page."""
    values_str = "\n".join(location_names)
    data = json.dumps({
        "property_id": str(PROPERTY_ID_SPATIAL),
        "query_type": "eq",
        "select_type": "multiple_list",
        "truncate_values": FACET_TRUNCATE,
        "values": values_str,
    }, separators=(",", ":"))
    escaped_data = data.replace("\\", "\\\\").replace("'", "\\'")

    shift_sql = (
        f"UPDATE faceted_browse_facet SET position = position + 1 "
        f"WHERE category_id = {FACETED_BROWSE_CATEGORY_ID} AND position >= {FACET_POSITION};"
    )
    insert_sql = (
        f"INSERT INTO faceted_browse_facet (category_id, name, type, position, data) "
        f"VALUES ({FACETED_BROWSE_CATEGORY_ID}, 'Location', 'value', "
        f"{FACET_POSITION}, '{escaped_data}');"
    )

    if apply:
        db_execute(shift_sql)
        db_execute(insert_sql)
        print(f"  Created Location facet at position {FACET_POSITION} with {len(location_names)} values.")
    else:
        print(f"  Would create Location facet at position {FACET_POSITION} with {len(location_names)} values.")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Create location vocabulary and faceted browse facet for JIM Stories"
    )
    parser.add_argument("--apply", action="store_true", help="Write to DB (default: dry-run)")
    args = parser.parse_args()

    cache_path = os.path.join(os.path.dirname(__file__), "jim_locations_cache.json")
    if not os.path.exists(cache_path):
        print("ERROR: jim_locations_cache.json not found. Run extract_jim_locations.py first.")
        sys.exit(1)

    cache = load_cache(cache_path)
    location_names = collect_unique_locations(cache)
    print(f"Found {len(location_names)} unique location names across {len(cache)} items.\n")

    mode = "APPLYING" if args.apply else "DRY RUN"
    print(f"=== Step 1: Custom Vocabulary ({mode}) ===")
    if check_vocab_exists():
        print(f"  Custom vocab {CUSTOM_VOCAB_ID} already exists — skipping.")
    else:
        create_vocab(location_names, args.apply)

    print(f"\n=== Step 2: dcterms:spatial Values ({mode}) ===")
    existing = check_existing_spatial_values()
    if existing:
        print(f"  {len(existing)} items already have spatial values — skipping those.")
    total = insert_spatial_values(cache, existing, args.apply)
    if args.apply:
        print(f"  Inserted {total} spatial values.")
    else:
        print(f"  Would insert {total} spatial values.")

    print(f"\n=== Step 3: Faceted Browse Facet ({mode}) ===")
    if check_facet_exists():
        print("  Location facet already exists — skipping.")
    else:
        create_facet(location_names, args.apply)

    if not args.apply:
        print("\nDry run complete. Use --apply to write to DB.")
    else:
        print("\nDone. Run: docker compose restart omeka")


if __name__ == "__main__":
    main()

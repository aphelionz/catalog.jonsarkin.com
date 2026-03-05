#!/usr/bin/env python3
"""
create_box_item_sets.py — Create item sets for each box category and assign items.

Reads schema:box values from the database, parses them into category names
(e.g. "comic (box 2) 123" → "Comic"), creates an Omeka item set for each
category, and assigns every item to its corresponding item set.

Usage:
  python scripts/create_box_item_sets.py --dry-run   # Preview changes
  python scripts/create_box_item_sets.py              # Apply changes
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(line_buffering=True)

import argparse
import shutil
import subprocess
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backfill_box_motifs import parse_box_category, normalize_category

# ── Configuration ────────────────────────────────────────────────────────

BOX_PROPERTY_ID = 1424       # schema:box
TITLE_PROPERTY_ID = 1        # dcterms:title
OWNER_ID = 1                 # admin user
RESOURCE_TYPE = r"Omeka\Entity\ItemSet"

# Path to Omeka files volume (host-mounted)
FILES_DIR = Path(__file__).parent.parent / "omeka" / "volume" / "files"
ASSET_DIR = FILES_DIR / "asset"
SQUARE_DIR = FILES_DIR / "square"


# ── Database helpers ─────────────────────────────────────────────────────

def run_sql(query: str) -> str:
    """Run a SQL query via docker compose exec and return stdout."""
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "db",
            "mariadb", "-uomeka", "-pomeka", "omeka",
            "--batch", "--skip-column-names",
            "-e", query,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SQL error: {result.stderr.strip()}")
    return result.stdout


def run_sql_with_headers(query: str) -> str:
    """Run a SQL query and return stdout with column headers."""
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "db",
            "mariadb", "-uomeka", "-pomeka", "omeka",
            "-e", query,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SQL error: {result.stderr.strip()}")
    return result.stdout


def parse_rows(output: str) -> list[tuple[str, ...]]:
    """Parse tab-separated rows from SQL output (no headers)."""
    rows = []
    for line in output.strip().split("\n"):
        if line.strip():
            rows.append(tuple(line.split("\t")))
    return rows


# ── Core logic ───────────────────────────────────────────────────────────

def load_box_values() -> list[tuple[int, str]]:
    """Query all (item_id, box_value) pairs from the database."""
    output = run_sql(f"""
        SELECT v.resource_id, v.value
        FROM value v
        WHERE v.property_id = {BOX_PROPERTY_ID}
          AND v.value IS NOT NULL
          AND v.value != ''
        ORDER BY v.resource_id;
    """)
    pairs = []
    for row in parse_rows(output):
        if len(row) >= 2:
            pairs.append((int(row[0]), row[1]))
    return pairs


def load_existing_item_sets() -> dict[str, int]:
    """Return {title: resource_id} for all existing item sets."""
    output = run_sql("""
        SELECT r.id, r.title
        FROM resource r
        JOIN item_set s ON r.id = s.id;
    """)
    sets = {}
    for row in parse_rows(output):
        if len(row) >= 2:
            sets[row[1]] = int(row[0])
    return sets


def load_existing_assignments() -> set[tuple[int, int]]:
    """Return set of (item_id, item_set_id) already assigned."""
    output = run_sql("SELECT item_id, item_set_id FROM item_item_set;")
    assignments = set()
    for row in parse_rows(output):
        if len(row) >= 2:
            assignments.add((int(row[0]), int(row[1])))
    return assignments


def build_category_map(box_values: list[tuple[int, str]]) -> dict[str, list[int]]:
    """Parse box values and group item IDs by normalized category."""
    categories: dict[str, list[int]] = {}
    for item_id, box_val in box_values:
        raw = parse_box_category(box_val)
        if not raw:
            continue
        cat = normalize_category(raw)
        categories.setdefault(cat, []).append(item_id)
    return categories


def match_existing(category: str, existing: dict[str, int]) -> int | None:
    """Find an existing item set that matches this category (fuzzy on spacing/prefix)."""
    # Exact match
    if category in existing:
        return existing[category]
    # Normalize for comparison: lowercase, strip "the ", remove spaces
    def norm(s: str) -> str:
        s = s.lower().strip()
        if s.startswith("the "):
            s = s[4:]
        return s.replace(" ", "")
    target = norm(category)
    for title, set_id in existing.items():
        if norm(title) == target:
            return set_id
    return None


def escape_sql(s: str) -> str:
    """Escape a string for SQL single-quote context."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def pick_exemplar_media(item_set_id: int) -> tuple[str, str] | None:
    """Pick a representative media from an item set. Returns (storage_id, extension) or None."""
    output = run_sql(f"""
        SELECT m.storage_id, m.extension
        FROM item_item_set iis
        JOIN media m ON m.item_id = iis.item_id
        WHERE iis.item_set_id = {item_set_id}
          AND m.has_thumbnails = 1
        ORDER BY m.item_id, m.position
        LIMIT 1;
    """)
    rows = parse_rows(output)
    if rows and len(rows[0]) >= 2:
        return (rows[0][0], rows[0][1])
    return None


def create_asset_from_media(storage_id: str) -> int | None:
    """Copy a media's square thumbnail to the asset dir and create an asset DB row.

    Returns the new asset ID, or None on failure.
    """
    # Source: square thumbnail (always jpg regardless of original extension)
    src = SQUARE_DIR / f"{storage_id}.jpg"
    if not src.exists():
        return None

    # Generate unique storage ID for the asset
    asset_storage_id = uuid.uuid4().hex
    dst = ASSET_DIR / f"{asset_storage_id}.jpg"

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

    # Insert asset row and get ID
    esc_storage = escape_sql(asset_storage_id)
    id_output = run_sql(f"""
        INSERT INTO asset (owner_id, name, media_type, storage_id, extension)
        VALUES ({OWNER_ID}, '{esc_storage}.jpg', 'image/jpeg', '{esc_storage}', 'jpg');
        SELECT LAST_INSERT_ID();
    """)
    return int(id_output.strip())


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Create item sets for box categories.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Limit items processed")
    args = parser.parse_args()

    # 1. Load data
    print("Loading box values from database...")
    box_values = load_box_values()
    if args.limit:
        box_values = box_values[:args.limit]
    print(f"  {len(box_values)} items have box values")

    print("Loading existing item sets...")
    existing = load_existing_item_sets()
    print(f"  {len(existing)} item sets exist: {', '.join(sorted(existing.keys()))}")

    print("Loading existing assignments...")
    existing_assignments = load_existing_assignments()
    print(f"  {len(existing_assignments)} existing assignments\n")

    # 2. Parse categories
    categories = build_category_map(box_values)
    print(f"Parsed {len(categories)} box categories:")
    for cat, items in sorted(categories.items(), key=lambda x: -len(x[1])):
        print(f"  {cat}: {len(items)} items")
    print()

    # 3. Plan actions
    renames: list[tuple[int, str, str]] = []       # (set_id, old_title, new_title)
    creates: list[str] = []                         # category names to create
    category_to_set_id: dict[str, int] = {}         # final mapping

    for cat in sorted(categories.keys()):
        set_id = match_existing(cat, existing)
        if set_id is not None:
            # Found an existing item set
            # Check if it needs renaming
            current_title = next(t for t, sid in existing.items() if sid == set_id)
            if current_title != cat:
                renames.append((set_id, current_title, cat))
            category_to_set_id[cat] = set_id
        else:
            creates.append(cat)

    # Count new assignments needed
    new_assignments: list[tuple[int, str]] = []  # (item_id, category)
    for cat, item_ids in categories.items():
        set_id = category_to_set_id.get(cat)
        for item_id in item_ids:
            if set_id is None or (item_id, set_id) not in existing_assignments:
                new_assignments.append((item_id, cat))

    # 4. Report
    if renames:
        print(f"Renames ({len(renames)}):")
        for set_id, old, new in renames:
            print(f"  [{set_id}] \"{old}\" → \"{new}\"")
        print()

    print(f"New item sets to create ({len(creates)}):")
    for cat in creates:
        print(f"  {cat} ({len(categories[cat])} items)")
    print()

    print(f"Item-to-set assignments: {len(new_assignments)} new")
    print()

    if args.dry_run:
        print("Dry run — no changes made.")
        return

    # 5. Execute renames
    for set_id, old_title, new_title in renames:
        esc_new = escape_sql(new_title)
        run_sql(f"UPDATE resource SET title = '{esc_new}' WHERE id = {set_id};")
        run_sql(f"""
            UPDATE value
            SET value = '{esc_new}'
            WHERE resource_id = {set_id}
              AND property_id = {TITLE_PROPERTY_ID};
        """)
        print(f"  Renamed [{set_id}] \"{old_title}\" → \"{new_title}\"")

    # 6. Create new item sets
    for cat in creates:
        esc_cat = escape_sql(cat)
        esc_type = escape_sql(RESOURCE_TYPE)
        # All statements in one connection so LAST_INSERT_ID() works
        # Note: `value` is a reserved word in MariaDB, must be backtick-escaped
        id_output = run_sql(f"""
            INSERT INTO resource (owner_id, title, is_public, created, resource_type)
            VALUES ({OWNER_ID}, '{esc_cat}', 1, NOW(), '{esc_type}');
            SET @new_id = LAST_INSERT_ID();
            INSERT INTO item_set (id, is_open) VALUES (@new_id, 0);
            INSERT INTO `value` (resource_id, property_id, type, `value`, is_public)
            VALUES (@new_id, {TITLE_PROPERTY_ID}, 'literal', '{esc_cat}', 1);
            SELECT @new_id;
        """)
        new_id = int(id_output.strip())
        category_to_set_id[cat] = new_id
        print(f"  Created item set [{new_id}] \"{cat}\"")

    # 7. Assign items to item sets
    assigned = 0
    # Build bulk INSERT for efficiency
    insert_values = []
    for item_id, cat in new_assignments:
        set_id = category_to_set_id[cat]
        insert_values.append(f"({item_id}, {set_id})")

    if insert_values:
        # Batch in chunks of 500
        chunk_size = 500
        for i in range(0, len(insert_values), chunk_size):
            chunk = insert_values[i : i + chunk_size]
            values_str = ", ".join(chunk)
            run_sql(f"""
                INSERT IGNORE INTO item_item_set (item_id, item_set_id)
                VALUES {values_str};
            """)
            assigned += len(chunk)
            print(f"  Assigned {min(i + chunk_size, len(insert_values))}/{len(insert_values)} items...")

    # 8. Assign thumbnails to item sets missing one
    print("\nAssigning thumbnails...")
    thumb_assigned = 0
    thumb_skipped = 0
    # Reload item sets to include newly created ones
    all_box_sets = {cat: category_to_set_id[cat] for cat in categories if cat in category_to_set_id}
    # Check which already have thumbnails
    set_ids_str = ", ".join(str(sid) for sid in all_box_sets.values())
    thumb_output = run_sql(f"""
        SELECT id, thumbnail_id FROM resource
        WHERE id IN ({set_ids_str});
    """)
    has_thumb = set()
    for row in parse_rows(thumb_output):
        if len(row) >= 2 and row[1] != "NULL" and row[1]:
            has_thumb.add(int(row[0]))

    for cat, set_id in sorted(all_box_sets.items()):
        if set_id in has_thumb:
            thumb_skipped += 1
            continue
        media = pick_exemplar_media(set_id)
        if not media:
            print(f"  {cat}: no media found, skipping")
            continue
        storage_id, ext = media
        asset_id = create_asset_from_media(storage_id)
        if asset_id is None:
            print(f"  {cat}: thumbnail file not found, skipping")
            continue
        run_sql(f"UPDATE resource SET thumbnail_id = {asset_id} WHERE id = {set_id};")
        thumb_assigned += 1
        print(f"  {cat}: thumbnail set (asset {asset_id})")

    print(f"\nDone. Created: {len(creates)}, renamed: {len(renames)}, "
          f"assigned: {assigned}, thumbnails: {thumb_assigned} new, {thumb_skipped} existing")


if __name__ == "__main__":
    main()

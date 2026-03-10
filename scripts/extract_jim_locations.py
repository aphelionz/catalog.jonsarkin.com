#!/usr/bin/env python3
"""
extract_jim_locations.py — Extract geographic locations from JIM Stories.

Reads each JIM story's full text from the DB, sends it to Claude for
structured geographic extraction, and writes results back as a JSON blob
in dcterms:coverage.

Usage:
  python scripts/extract_jim_locations.py                  # dry-run (default)
  python scripts/extract_jim_locations.py --apply          # write to DB
  python scripts/extract_jim_locations.py --item-id 7509   # single item
  python scripts/extract_jim_locations.py --model claude-sonnet-4-6  # override model

Environment:
  ANTHROPIC_API_KEY — Required.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from typing import Optional

import anthropic

# ── Constants ──────────────────────────────────────────────────────────────

ITEM_SET_ID = 8020          # JIM Stories
PROPERTY_ID_CONTENT = 91    # bibo:content
PROPERTY_ID_IDENT = 10      # dcterms:identifier
PROPERTY_ID_TITLE = 1       # dcterms:title
PROPERTY_ID_DATE = 7        # dcterms:date
PROPERTY_ID_COVERAGE = 14   # dcterms:coverage

MODEL = "claude-opus-4-6"

VALID_LOCATION_TYPES = ["city", "state", "country", "region", "landmark", "body_of_water"]
VALID_NARRATIVE_ROLES = ["setting", "reference", "origin", "destination"]
VALID_FLAGS = ["ambiguous", "metaphorical", "gloucester_bleed", "cultural_reference"]

SYSTEM_PROMPT = """\
You are a geographic analyst extracting real-world location references from short prose fiction.

You will receive the title and full text of a prose poem featuring a character named "Jim." Extract ALL named real-world geographic references.

## What to extract
- Cities/towns: Memphis, Duluth, Fresno, Clarion, Ogden, Freehold, etc.
- States/provinces: Pennsylvania, Ohio, Montana, California, New Jersey, etc.
- Countries: Nigeria, Cambodia, Italy, Greece, Lithuania, etc.
- Regions/areas: "western Pennsylvania," "northern Maine coast," "the Ohio border"
- Specific landmarks or venues: Fenway Park, the Acropolis, Madison Square Garden, the Parthenon, etc.
- Named bodies of water or geographic features (only if named or regionally identifiable)

## What NOT to extract
- Fictional or unidentifiable places. If the author invents a town name, skip it.
- Generic non-geographic uses. "Edge City" as a metaphorical concept is NOT a real place — but flag it if the title references it as a setting.
- Street names or addresses without a city context.

## For each location, provide:
- location_name: The place as referenced in the text (e.g., "western Pennsylvania," "Memphis," "Fenway Park")
- location_type: One of: city, state, country, region, landmark, body_of_water
- coordinates: [latitude, longitude] — best-estimate. City-center for cities, centroid for states/regions/countries, precise for well-known landmarks.
- narrative_role: One of:
  - setting — Jim is physically present in this location within the narrative
  - reference — The location is mentioned but Jim isn't there (memories, allusions, song lyrics, metaphors)
  - origin — Jim is described as being from this location or having a history there
  - destination — Jim is headed there but hasn't arrived
- text_excerpt: A short excerpt (max ~20 words) showing the location in context
- flags: Array of applicable flags (can be empty):
  - "ambiguous" — location could refer to multiple real places (e.g., "Paris" could be France or Texas)
  - "metaphorical" — location used metaphorically or as a concept rather than a physical place
  - "gloucester_bleed" — reference to Gloucester, Cape Ann, or the North Shore (author's real geography bleeding into fiction)
  - "cultural_reference" — location appears as part of a cultural reference (song lyric, book title, etc.) rather than a narrative place

## Rules
- Multiple locations in one story: extract ALL of them.
- Disambiguate: if "Paris" could be France or Texas, use context. If genuinely ambiguous, default to the more famous one and add "ambiguous" flag.
- Historical/anachronistic settings: extract real locations even if the timeframe is fictional.
- If the title names a location (e.g., "JIM IN NIGERIA") but the text contradicts it or places Jim elsewhere, extract both and note the roles correctly.
- For landmarks within a city (e.g., "Fenway Park"), extract BOTH the landmark AND the city if the city is mentioned or clearly implied.

Respond with a JSON array only. No markdown fences, no explanation. Empty array [] if no geographic references found.

Example response:
[{"location_name":"western Pennsylvania","location_type":"region","coordinates":[41.0,-79.5],"narrative_role":"setting","text_excerpt":"He roamed from town to town, mostly in western Pennsylvania","flags":[]},{"location_name":"Ohio","location_type":"state","coordinates":[40.4,-82.7],"narrative_role":"setting","text_excerpt":"sometimes across the Ohio border","flags":[]}]"""


# ── DB helpers ─────────────────────────────────────────────────────────────

def db_query(sql: str) -> str:
    """Run a SQL query against the local Omeka DB and return stdout."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db",
         "mariadb", "-u", "root", "-proot", "omeka",
         "--batch", "--raw", "-e", sql],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def db_execute(sql: str) -> None:
    """Run a write SQL statement against the local Omeka DB."""
    subprocess.run(
        ["docker", "compose", "exec", "-T", "db",
         "mariadb", "-u", "root", "-proot", "omeka",
         "-e", sql],
        capture_output=True, text=True, check=True,
    )


def fetch_jim_stories(item_id: int | None = None) -> list[dict]:
    """Fetch JIM story items with content, identifier, title, and date."""
    where = f"AND iis.item_id = {int(item_id)}" if item_id else ""
    sep = "<<<REC>>>"
    sql = f"""
        SELECT CONCAT_WS('{sep}',
               iis.item_id,
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_IDENT} THEN v.value END),
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_TITLE} THEN v.value END),
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_DATE} THEN v.value END),
               REPLACE(REPLACE(
                   MAX(CASE WHEN v.property_id = {PROPERTY_ID_CONTENT} THEN v.value END),
                   '\\n', '<<NL>>'), '\\r', '')
        ) AS row_data
        FROM item_item_set iis
        JOIN value v ON iis.item_id = v.resource_id
        WHERE iis.item_set_id = {ITEM_SET_ID}
          AND v.property_id IN ({PROPERTY_ID_IDENT}, {PROPERTY_ID_TITLE}, {PROPERTY_ID_DATE}, {PROPERTY_ID_CONTENT})
          {where}
        GROUP BY iis.item_id
        HAVING MAX(CASE WHEN v.property_id = {PROPERTY_ID_CONTENT} THEN v.value END) IS NOT NULL
        ORDER BY MAX(CASE WHEN v.property_id = {PROPERTY_ID_IDENT} THEN v.value END);
    """
    lines = db_query(sql).strip().split("\n")
    if len(lines) < 2:
        return []
    items = []
    for line in lines[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        parts = line.split(sep)
        if len(parts) < 5:
            continue
        content = parts[4].replace("<<NL>>", "\n")
        items.append({
            "item_id": parts[0],
            "identifier": parts[1],
            "title": parts[2],
            "date": parts[3],
            "content": content,
        })
    return items


def check_existing_coverage() -> set[int]:
    """Return item IDs that already have a dcterms:coverage value."""
    sql = f"SELECT resource_id FROM `value` WHERE property_id = {PROPERTY_ID_COVERAGE};"
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        return set()
    return {int(row) for row in out[1:] if row.strip()}


def get_item_id_for_identifier(identifier: str) -> int | None:
    """Look up item_id by WRT-xxx identifier."""
    escaped = identifier.replace("'", "\\'")
    sql = (
        f"SELECT v.resource_id FROM value v "
        f"JOIN item_item_set iis ON v.resource_id = iis.item_id "
        f"WHERE v.property_id = {PROPERTY_ID_IDENT} AND v.value = '{escaped}' "
        f"AND iis.item_set_id = {ITEM_SET_ID} LIMIT 1;"
    )
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        return None
    return int(out[1].strip())


# ── Location extraction ───────────────────────────────────────────────────

def extract_locations(client: anthropic.Anthropic, model: str, title: str, content: str) -> list[dict]:
    """Send story title + content to Claude and get structured location data."""
    user_msg = f"Title: {title}\n\nFull text:\n{content}"
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # Extract the JSON array — find the outermost [ ... ]
    start = raw.find("[")
    if start == -1:
        raise ValueError(f"No JSON array found in response: {raw[:100]}")
    depth = 0
    end = start
    for i, ch in enumerate(raw[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    locations = json.loads(raw[start:end])
    if not isinstance(locations, list):
        raise ValueError(f"Expected JSON array, got {type(locations).__name__}")
    # Validate each location, dropping invalid entries
    valid = []
    for loc in locations:
        if loc.get("location_type") not in VALID_LOCATION_TYPES:
            continue
        if loc.get("narrative_role") not in VALID_NARRATIVE_ROLES:
            continue
        coords = loc.get("coordinates")
        if not isinstance(coords, list) or len(coords) != 2:
            continue
        if not isinstance(coords[0], (int, float)) or not isinstance(coords[1], (int, float)):
            continue
        # Validate flags
        flags = loc.get("flags", [])
        if not isinstance(flags, list):
            loc["flags"] = []
        else:
            loc["flags"] = [f for f in flags if f in VALID_FLAGS]
        valid.append(loc)
    return valid


# ── Output helpers ─────────────────────────────────────────────────────────

def format_item_summary(item: dict, locations: list[dict]) -> str:
    """Format a single item's locations for the review file."""
    header = f"{item['identifier']} | {item['title']} | {item['date']}"
    if not locations:
        return f"{header}\n  (no geographic references)\n"
    lines = [header]
    for loc in locations:
        flags_str = ""
        if loc.get("flags"):
            flags_str = f" [{', '.join(loc['flags'])}]"
        lines.append(
            f"  - {loc['location_name']} ({loc['location_type']}, {loc['narrative_role']})"
            f"{flags_str} — \"{loc['text_excerpt']}\""
        )
    return "\n".join(lines) + "\n"


def build_aggregate_table(all_results: list[dict]) -> list[dict]:
    """Build the aggregate location frequency table."""
    agg = defaultdict(lambda: {
        "location": "",
        "type": "",
        "total_mentions": 0,
        "as_setting": 0,
        "as_reference": 0,
        "as_origin": 0,
        "as_destination": 0,
        "stories": set(),
    })
    for result in all_results:
        ident = result["identifier"]
        for loc in result["locations"]:
            key = (loc["location_name"].lower(), loc["location_type"])
            entry = agg[key]
            entry["location"] = loc["location_name"]
            entry["type"] = loc["location_type"]
            entry["total_mentions"] += 1
            role_key = f"as_{loc['narrative_role']}"
            if role_key in entry:
                entry[role_key] += 1
            entry["stories"].add(ident)
    # Sort by total mentions descending
    rows = sorted(agg.values(), key=lambda x: (-x["total_mentions"], x["location"]))
    for row in rows:
        row["stories"] = ", ".join(sorted(row["stories"]))
    return rows


def write_aggregate_csv(rows: list[dict], path: str) -> None:
    """Write aggregate table to CSV."""
    fieldnames = ["location", "type", "total_mentions", "as_setting",
                  "as_reference", "as_origin", "as_destination", "stories"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_aggregate_table(rows: list[dict]) -> None:
    """Print the aggregate table to stdout."""
    print(f"\n{'Location':<35} {'Type':<15} {'Total':>5} {'Setting':>7} {'Ref':>4} "
          f"{'Origin':>6} {'Dest':>4}  Stories")
    print("-" * 120)
    for row in rows:
        stories_short = row["stories"]
        if len(stories_short) > 40:
            stories_short = stories_short[:37] + "..."
        print(f"{row['location']:<35} {row['type']:<15} {row['total_mentions']:>5} "
              f"{row['as_setting']:>7} {row['as_reference']:>4} {row['as_origin']:>6} "
              f"{row['as_destination']:>4}  {stories_short}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract geographic locations from JIM Stories")
    parser.add_argument("--apply", action="store_true", help="Write results to DB (default: dry-run)")
    parser.add_argument("--item-id", type=int, help="Process a single item by DB ID")
    parser.add_argument("--model", default=MODEL, help=f"Claude model to use (default: {MODEL})")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY in environment.")
        sys.exit(1)

    client = anthropic.Anthropic()

    # Fetch stories
    items = fetch_jim_stories(args.item_id)
    if not items:
        print("No JIM stories found.")
        sys.exit(1)

    # Load cache (to avoid re-processing on retry)
    cache_path = os.path.join(os.path.dirname(__file__), "jim_locations_cache.json")
    cache = {}
    if os.path.exists(cache_path) and not args.item_id:
        with open(cache_path) as f:
            for entry in json.load(f):
                cache[entry["identifier"]] = entry

    # Skip already-tagged items (in DB)
    existing = check_existing_coverage()
    to_process = [it for it in items if int(it["item_id"]) not in existing]
    skipped = len(items) - len(to_process)
    if skipped:
        print(f"Skipping {skipped} already-tagged items (in DB).")

    # Count cached items
    cached_count = sum(1 for it in to_process if it["identifier"] in cache)
    need_api = len(to_process) - cached_count
    if cached_count:
        print(f"Using cache for {cached_count} items, {need_api} need API calls.")
    print(f"Processing {len(to_process)} items {'(DRY RUN)' if not args.apply else '(APPLYING)'}...")
    print(f"Model: {args.model}\n")

    all_results = []
    errors = []
    review_lines = []

    for i, item in enumerate(to_process):
        item_id = item["item_id"]
        ident = item["identifier"]
        title = item["title"]
        content = item["content"]

        try:
            # Use cache if available
            if ident in cache:
                locations = cache[ident]["locations"]
                print(f"  [{i+1}/{len(to_process)}] {ident} — cached ({len(locations)} locations)")
            else:
                locations = extract_locations(client, args.model, title, content)
                # Small delay between API calls
                if i < len(to_process) - 1:
                    time.sleep(0.3)

                loc_count = len(locations)
                loc_names = ", ".join(l["location_name"] for l in locations[:3])
                if loc_count > 3:
                    loc_names += f", ... (+{loc_count - 3} more)"
                status = f"{loc_count} locations: {loc_names}" if loc_count else "no locations"
                print(f"  [{i+1}/{len(to_process)}] {ident} — {status}")

            result = {
                "item_id": int(item_id),
                "identifier": ident,
                "title": title,
                "date": item["date"],
                "locations": locations,
            }
            all_results.append(result)
            cache[ident] = result

            # Per-item summary
            summary = format_item_summary(item, locations)
            review_lines.append(summary)

            # Write to DB
            if args.apply:
                json_str = json.dumps(locations, separators=(",", ":"))
                escaped = json_str.replace("\\", "\\\\").replace("'", "\\'")
                sql = (
                    f"INSERT INTO `value` (resource_id, property_id, type, `value`, is_public) "
                    f"VALUES ({int(item_id)}, {PROPERTY_ID_COVERAGE}, 'literal', "
                    f"'{escaped}', 1);"
                )
                db_execute(sql)

        except Exception as e:
            print(f"  [{i+1}/{len(to_process)}] {ident} — ERROR: {e}")
            errors.append({"identifier": ident, "item_id": item_id, "error": str(e)})

    # Save cache
    with open(cache_path, "w") as f:
        json.dump(list(cache.values()), f, separators=(",", ":"))

    # ── Write review file ─────────────────────────────────────────────────
    review_path = os.path.join(os.path.dirname(__file__), "jim_locations_review.txt")
    with open(review_path, "w") as f:
        f.write("# JIM Stories — Geographic Location Extraction\n")
        f.write(f"# {len(all_results)} items processed, {len(errors)} errors\n")
        f.write(f"# Model: {args.model}\n\n")
        for line in review_lines:
            f.write(line + "\n")
    print(f"\nPer-item review saved to: {review_path}")

    # ── Aggregate table ───────────────────────────────────────────────────
    agg_rows = build_aggregate_table(all_results)
    agg_path = os.path.join(os.path.dirname(__file__), "jim_locations_aggregate.csv")
    write_aggregate_csv(agg_rows, agg_path)
    print(f"Aggregate CSV saved to: {agg_path}")

    print_aggregate_table(agg_rows)

    # ── Summary ───────────────────────────────────────────────────────────
    total_locations = sum(len(r["locations"]) for r in all_results)
    items_with_locations = sum(1 for r in all_results if r["locations"])
    items_without = sum(1 for r in all_results if not r["locations"])

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"Items processed:      {len(all_results)}")
    print(f"Items with locations: {items_with_locations}")
    print(f"Items without:        {items_without}")
    print(f"Total locations:      {total_locations}")
    print(f"Unique locations:     {len(agg_rows)}")
    print(f"Errors:               {len(errors)}")

    if errors:
        print(f"\nERRORS:")
        for e in errors:
            print(f"  {e['identifier']}: {e['error']}")

    if not args.apply:
        print("\nDry run complete. Use --apply to write results to DB.")
    else:
        print(f"\nDone. {len(all_results)} items written to DB (dcterms:coverage).")
        print("Run: docker compose restart omeka  (to clear Doctrine cache)")


if __name__ == "__main__":
    main()

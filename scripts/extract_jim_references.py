#!/usr/bin/env python3
"""
extract_jim_references.py — Extract literary & cultural references from JIM Stories.

Reads each JIM story's full text from the DB, sends it to Claude for
structured reference extraction, caches results locally, and (with --apply)
writes canonical reference names to dcterms:relation as a custom vocabulary
with a faceted browse facet.

Usage:
  python scripts/extract_jim_references.py                  # dry-run (default)
  python scripts/extract_jim_references.py --apply          # write to DB
  python scripts/extract_jim_references.py --item-id 7638   # single item
  python scripts/extract_jim_references.py --model claude-sonnet-4-6

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
from collections import Counter, defaultdict
from typing import Optional

import anthropic

# ── Constants ──────────────────────────────────────────────────────────────

ITEM_SET_ID = 8020          # JIM Stories
PROPERTY_ID_CONTENT = 91    # bibo:content
PROPERTY_ID_IDENT = 10      # dcterms:identifier
PROPERTY_ID_TITLE = 1       # dcterms:title
PROPERTY_ID_DATE = 7        # dcterms:date
PROPERTY_ID_RELATION = 13   # dcterms:relation (repeatable custom vocab values)

MODEL = "claude-opus-4-6"

CUSTOM_VOCAB_ID = 11
CUSTOM_VOCAB_TYPE = "customvocab:11"
CUSTOM_VOCAB_LABEL = "JIM Story Cultural References"

FACETED_BROWSE_CATEGORY_ID = 2  # "Facets" category
FACET_POSITION = 6              # after Location (5), before Motifs (currently 6)
FACET_NAME = "Cultural Reference"
FACET_TRUNCATE = "20"

VALID_REFERENCE_TYPES = [
    "author", "work", "fictional_character", "musician", "band",
    "song", "album", "film", "tv", "visual_artist", "art_movement",
    "historical_figure", "historical_event", "philosopher",
    "religious", "sports", "venue", "other",
]
VALID_REFERENCE_ROLES = [
    "identification", "allusion", "quotation", "namedrop", "structural",
]

SYSTEM_PROMPT = """\
You are a literary analyst extracting cultural references from short prose fiction by artist Jon Sarkin.

You will receive the title and full text of a prose poem featuring a recurring character named "Jim." Extract ALL named literary, cultural, and historical references.

## What to extract

**Literary:**
- Authors (Dostoevsky, Tolstoy, Kafka, Conrad, Vonnegut, Kerouac, Ginsberg, Burroughs, Ezra Pound, Hubert Selby Jr., Rimbaud, etc.)
- Specific works (Notes from Underground, Heart of Darkness, Slaughterhouse-Five, War and Peace, The Great Gatsby, Waiting for Godot, etc.)
- Fictional characters (Travis Bickle, Billy Pilgrim, Kilgore Trout, Marlowe, Yossarian, Willy Loman, Big Daddy, etc.)
- Poets and poetry (Robert Duncan, Richard Wilbur, etc.)

**Music:**
- Musicians (Miles Davis, Bob Dylan, John Coltrane, Charles Mingus, Neil Young, Glen Campbell, etc.)
- Bands (The Who, etc.)
- Specific songs or albums ("Stuck Inside of Mobile with the Memphis Blues Again," "By the Time I Get to Phoenix," "Just My Imagination," etc.)

**Film & Television:**
- Films (Apocalypse Now, Taxi Driver, etc.)
- Directors, actors
- TV shows (American Idol, etc.)

**Visual Art:**
- Artists, movements, specific works

**Historical/Political:**
- Historical figures (Teddy Kennedy, etc.)
- Events (9/11, Vietnam War, etc.)
- Political references (Tea Party, filibustering, etc.)

**Philosophical/Religious:**
- Philosophers (Thomas Hobbes, etc.)
- Religious references, biblical figures (Isaiah, etc.)
- Named concepts (post-modernism, karma, etc.)

**Sports:**
- Teams, venues (Fenway Park, etc.)
- Athletes, events

## What NOT to extract
- Self-reference or metafiction. If Jon references his own writing process, narrative construction, or the Jim stories themselves ("Jim becomes sick of his own narrative"), that is metanarrative, not a cultural reference. Skip it.
- Generic words that are not references to specific cultural works, people, or events.
- Fictional characters or people invented within the Jim stories themselves (e.g., Jim's friends, unnamed junkies).

## Classification rules

1. **Implicit references.** If Jon writes "a loser is a loser is a loser," that's a Gertrude Stein allusion ("a rose is a rose is a rose"). Extract it, classify as allusion, note "implicit — echoes Stein's 'a rose is a rose is a rose'" in the notes field. But be conservative. Only tag implicit references if the parallel is unmistakable. Don't reach.

2. **Song lyrics woven into prose.** Jon embeds lyrics without quotation marks — "stuck inside of Mobile with the Memphis blues again" (Dylan) or "by the time I get to Phoenix" (Glen Campbell). Extract BOTH the song AND the artist. Mark reference_role as "quotation."

3. **Real people used as fictional characters.** "It was Teddy Kennedy. Then Jim woke up from his dream." Teddy Kennedy is a real person appearing in Jim's fictional world. Extract as historical_figure. If they might be someone from Jon's personal circle rather than a public figure, add "real_person_fictionalized" in notes.

4. **Generic cultural references.** "Kafka nightmare" or "Kafkaesque" — extract Kafka as author with reference_role "allusion." The author's name used as an adjective still counts.

5. **Stacked references.** Some passages pile up references rapidly ("Big Daddy's mendacity and Willy Loman Yossarian-isms"). Extract EACH one individually.

6. **Ambiguous names.** "Hobbes" could be Thomas Hobbes (philosopher) or Calvin and Hobbes (comic strip). Use context. If genuinely ambiguous, pick the most likely and note the ambiguity.

7. **When in doubt, extract.** False negatives (missing real references) are worse than false positives. If something might be a reference, extract it and add a note like "possible reference — needs review."

## Canonical name normalization

Always use the full canonical name:
- "Dylan" → "Bob Dylan"
- "Miles" → "Miles Davis"
- "Mingus" → "Charles Mingus"
- "Dostoevsky" → "Fyodor Dostoevsky"
- "Marlowe" → use context: if Heart of Darkness, it's "Charles Marlow" (the character); if playwright, it's "Christopher Marlowe"

## Response format

Respond with a JSON array only. No markdown fences, no explanation. Empty array [] if no cultural references found.

Each object must have:
- reference_name: Canonical/standard form (e.g., "Bob Dylan", "Fyodor Dostoevsky", "Travis Bickle")
- reference_as_mentioned: Exact form as it appears in the text (e.g., "Dylan", "Dostoevsky", "Travis Bickle")
- reference_type: One of: author, work, fictional_character, musician, band, song, album, film, tv, visual_artist, art_movement, historical_figure, historical_event, philosopher, religious, sports, venue, other
- reference_role: One of: identification (Jim compared to/identifies with the reference), allusion (enriches text without direct identification), quotation (quoting or closely paraphrasing), namedrop (mentioned but not developed), structural (provides organizing framework for the piece)
- text_excerpt: Short excerpt (max ~25 words) showing the reference in context
- notes: Optional. Use for flags like "implicit", "real_person_fictionalized", "possible reference — needs review", "Jon explicitly calls this plagiarism", etc. Omit if nothing to flag.

Example for a story containing "He felt like Travis Bickle, God's lonely man. He thought about the narrator from Notes from Underground. his green light at the end of Daisy's dock a sputtered smoke-ring of being stuck inside of Mobile with the Memphis blues again, of Big Daddy's mendacity and Willy Loman Yossarian-isms":

[{"reference_name":"Travis Bickle","reference_as_mentioned":"Travis Bickle","reference_type":"fictional_character","reference_role":"identification","text_excerpt":"He felt like Travis Bickle, God's lonely man"},{"reference_name":"Taxi Driver","reference_as_mentioned":"Travis Bickle","reference_type":"film","reference_role":"identification","text_excerpt":"He felt like Travis Bickle, God's lonely man","notes":"Travis Bickle is the protagonist of Taxi Driver"},{"reference_name":"Notes from Underground","reference_as_mentioned":"Notes from Underground","reference_type":"work","reference_role":"allusion","text_excerpt":"He thought about the narrator from Notes from Underground"},{"reference_name":"Fyodor Dostoevsky","reference_as_mentioned":"Notes from Underground","reference_type":"author","reference_role":"allusion","text_excerpt":"He thought about the narrator from Notes from Underground","notes":"implicit — Notes from Underground is by Dostoevsky"},{"reference_name":"The Great Gatsby","reference_as_mentioned":"the green light at the end of Daisy's dock","reference_type":"work","reference_role":"allusion","text_excerpt":"his green light at the end of Daisy's dock"},{"reference_name":"Bob Dylan","reference_as_mentioned":"stuck inside of Mobile with the Memphis blues again","reference_type":"musician","reference_role":"quotation","text_excerpt":"stuck inside of Mobile with the Memphis blues again"},{"reference_name":"Stuck Inside of Mobile with the Memphis Blues Again","reference_as_mentioned":"stuck inside of Mobile with the Memphis blues again","reference_type":"song","reference_role":"quotation","text_excerpt":"stuck inside of Mobile with the Memphis blues again"},{"reference_name":"Big Daddy","reference_as_mentioned":"Big Daddy's mendacity","reference_type":"fictional_character","reference_role":"allusion","text_excerpt":"Big Daddy's mendacity and Willy Loman Yossarian-isms"},{"reference_name":"Cat on a Hot Tin Roof","reference_as_mentioned":"Big Daddy's mendacity","reference_type":"work","reference_role":"allusion","text_excerpt":"Big Daddy's mendacity and Willy Loman Yossarian-isms","notes":"Big Daddy and 'mendacity' are from Tennessee Williams' Cat on a Hot Tin Roof"},{"reference_name":"Willy Loman","reference_as_mentioned":"Willy Loman","reference_type":"fictional_character","reference_role":"allusion","text_excerpt":"Big Daddy's mendacity and Willy Loman Yossarian-isms"},{"reference_name":"Yossarian","reference_as_mentioned":"Yossarian-isms","reference_type":"fictional_character","reference_role":"allusion","text_excerpt":"Big Daddy's mendacity and Willy Loman Yossarian-isms"}]"""


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


def check_existing_references() -> set[int]:
    """Return item IDs that already have dcterms:relation custom vocab values."""
    sql = (
        f"SELECT DISTINCT resource_id FROM `value` "
        f"WHERE property_id = {PROPERTY_ID_RELATION} AND type = '{CUSTOM_VOCAB_TYPE}';"
    )
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        return set()
    return {int(row) for row in out[1:] if row.strip()}


# ── Name normalization ─────────────────────────────────────────────────────

# Collapse "X. Y." → "X.Y." for initials, then apply explicit overrides.
import re

_INITIALS_RE = re.compile(r"([A-Z]\.) +([A-Z]\.)")

_NAME_OVERRIDES = {
    "t.s. eliot": "T.S. Eliot",
    "j.d. salinger": "J.D. Salinger",
    "j. d. salinger": "J.D. Salinger",
    "t. s. eliot": "T.S. Eliot",
}


def normalize_name(name: str) -> str:
    """Standardise canonical reference names so aggregates don't split."""
    # Collapse spaces between initials: "T. S." → "T.S."
    name = _INITIALS_RE.sub(r"\1\2", name)
    # Apply explicit overrides (case-insensitive lookup)
    override = _NAME_OVERRIDES.get(name.lower())
    if override:
        return override
    return name


# ── Reference extraction ──────────────────────────────────────────────────

def extract_references(client: anthropic.Anthropic, model: str, title: str, content: str) -> list[dict]:
    """Send story title + content to Claude and get structured reference data."""
    user_msg = f"Title: {title}\n\nFull text:\n{content}"
    response = client.messages.create(
        model=model,
        max_tokens=4096,
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
        raise ValueError(f"No JSON array found in response: {raw[:200]}")
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
    refs = json.loads(raw[start:end])
    if not isinstance(refs, list):
        raise ValueError(f"Expected JSON array, got {type(refs).__name__}")
    # Validate each reference
    valid = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("reference_type") not in VALID_REFERENCE_TYPES:
            continue
        if ref.get("reference_role") not in VALID_REFERENCE_ROLES:
            continue
        if not ref.get("reference_name") or not isinstance(ref["reference_name"], str):
            continue
        ref["reference_name"] = normalize_name(ref["reference_name"])
        if not ref.get("reference_as_mentioned") or not isinstance(ref["reference_as_mentioned"], str):
            continue
        if not ref.get("text_excerpt") or not isinstance(ref["text_excerpt"], str):
            continue
        # Truncate overly long excerpts
        if len(ref["text_excerpt"]) > 150:
            ref["text_excerpt"] = ref["text_excerpt"][:147] + "..."
        # Keep notes if present, otherwise remove key
        if "notes" in ref and not ref["notes"]:
            del ref["notes"]
        valid.append(ref)
    return valid


# ── Output helpers ─────────────────────────────────────────────────────────

def format_item_summary(item: dict, references: list[dict]) -> str:
    """Format a single item's references for the review file."""
    header = f"{item['identifier']} | {item['title']} | {item['date']}"
    if not references:
        return f"{header}\n  (no cultural references)\n"
    lines = [header]
    for ref in references:
        notes_str = ""
        if ref.get("notes"):
            notes_str = f" [{ref['notes']}]"
        lines.append(
            f"  - {ref['reference_name']} ({ref['reference_type']}, {ref['reference_role']})"
            f"{notes_str} — \"{ref['text_excerpt']}\""
        )
    return "\n".join(lines) + "\n"


def build_aggregate_table(all_results: list[dict]) -> list[dict]:
    """Build the aggregate reference frequency table."""
    agg = defaultdict(lambda: {
        "reference_name": "",
        "reference_type": "",
        "total_mentions": 0,
        "roles": Counter(),
        "stories": set(),
    })
    for result in all_results:
        ident = result["identifier"]
        for ref in result["references"]:
            key = ref["reference_name"].lower()
            entry = agg[key]
            entry["reference_name"] = ref["reference_name"]
            entry["reference_type"] = ref["reference_type"]
            entry["total_mentions"] += 1
            entry["roles"][ref["reference_role"]] += 1
            entry["stories"].add(ident)
    rows = sorted(agg.values(), key=lambda x: (-x["total_mentions"], x["reference_name"]))
    for row in rows:
        row["stories_appearing_in"] = len(row["stories"])
        row["story_identifiers"] = ", ".join(sorted(row["stories"]))
        row["most_common_role"] = row["roles"].most_common(1)[0][0] if row["roles"] else ""
        del row["roles"]
        del row["stories"]
    return rows


def build_type_breakdown(all_results: list[dict]) -> list[dict]:
    """Build reference type breakdown table."""
    type_refs = defaultdict(set)
    type_mentions = Counter()
    for result in all_results:
        for ref in result["references"]:
            rtype = ref["reference_type"]
            type_refs[rtype].add(ref["reference_name"])
            type_mentions[rtype] += 1
    rows = []
    for rtype in sorted(type_mentions, key=lambda t: -type_mentions[t]):
        rows.append({
            "reference_type": rtype,
            "unique_references": len(type_refs[rtype]),
            "total_mentions": type_mentions[rtype],
        })
    return rows


def write_csv(rows: list[dict], path: str, fieldnames: list[str]) -> None:
    """Write rows to CSV with given fieldnames."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_aggregate_table(rows: list[dict], limit: int = 30) -> None:
    """Print top entries from the aggregate table."""
    print(f"\n{'Reference':<40} {'Type':<22} {'Total':>5} {'Stories':>7}  Most common role")
    print("-" * 110)
    for row in rows[:limit]:
        print(f"{row['reference_name']:<40} {row['reference_type']:<22} "
              f"{row['total_mentions']:>5} {row['stories_appearing_in']:>7}  "
              f"{row['most_common_role']}")
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more (see CSV)")


def print_type_breakdown(rows: list[dict]) -> None:
    """Print reference type breakdown."""
    print(f"\n{'Type':<25} {'Unique':>6} {'Total':>6}")
    print("-" * 40)
    for row in rows:
        print(f"{row['reference_type']:<25} {row['unique_references']:>6} "
              f"{row['total_mentions']:>6}")


# ── Vocabulary & facet creation ────────────────────────────────────────────

def collect_unique_references(all_results: list[dict]) -> list[str]:
    """Deduplicate canonical reference names across all items, sorted."""
    names = set()
    for result in all_results:
        for ref in result["references"]:
            names.add(ref["reference_name"])
    return sorted(names, key=str.casefold)


def check_vocab_exists() -> bool:
    out = db_query(f"SELECT id FROM custom_vocab WHERE id = {CUSTOM_VOCAB_ID};")
    lines = out.strip().split("\n")
    return len(lines) >= 2


def check_facet_exists() -> bool:
    sql = (
        f"SELECT id FROM faceted_browse_facet "
        f"WHERE category_id = {FACETED_BROWSE_CATEGORY_ID} AND name = '{FACET_NAME}';"
    )
    out = db_query(sql).strip().split("\n")
    return len(out) >= 2


def create_vocab(ref_names: list[str]) -> None:
    terms_json = json.dumps(ref_names, ensure_ascii=False)
    escaped = terms_json.replace("\\", "\\\\").replace("'", "\\'")
    sql = (
        f"INSERT INTO custom_vocab (id, owner_id, label, lang, terms, item_set_id) "
        f"VALUES ({CUSTOM_VOCAB_ID}, 1, '{CUSTOM_VOCAB_LABEL}', '', '{escaped}', NULL);"
    )
    db_execute(sql)
    print(f"  Created custom vocab {CUSTOM_VOCAB_ID} with {len(ref_names)} terms.")


def insert_relation_values(all_results: list[dict], existing: set[int]) -> int:
    """Insert dcterms:relation values for each unique reference per item."""
    total = 0
    for result in all_results:
        item_id = result["item_id"]
        if item_id in existing:
            continue
        refs = result["references"]
        if not refs:
            continue
        # Deduplicate by canonical name within this item
        seen = set()
        for ref in refs:
            name = ref["reference_name"]
            if name in seen:
                continue
            seen.add(name)
            escaped = name.replace("\\", "\\\\").replace("'", "\\'")
            sql = (
                f"INSERT INTO `value` (resource_id, property_id, type, `value`, is_public) "
                f"VALUES ({item_id}, {PROPERTY_ID_RELATION}, '{CUSTOM_VOCAB_TYPE}', "
                f"'{escaped}', 1);"
            )
            db_execute(sql)
            total += 1
    return total


def create_facet(ref_names: list[str]) -> None:
    """Add Cultural Reference facet to the faceted browse page."""
    values_str = "\n".join(ref_names)
    data = json.dumps({
        "property_id": str(PROPERTY_ID_RELATION),
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
        f"VALUES ({FACETED_BROWSE_CATEGORY_ID}, '{FACET_NAME}', 'value', "
        f"{FACET_POSITION}, '{escaped_data}');"
    )
    db_execute(shift_sql)
    db_execute(insert_sql)
    print(f"  Created {FACET_NAME} facet at position {FACET_POSITION} with {len(ref_names)} values.")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract literary & cultural references from JIM Stories"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write results to DB (default: dry-run)")
    parser.add_argument("--item-id", type=int,
                        help="Process a single item by DB ID")
    parser.add_argument("--model", default=MODEL,
                        help=f"Claude model to use (default: {MODEL})")
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

    # Load cache
    cache_path = os.path.join(os.path.dirname(__file__), "jim_references_cache.json")
    cache = {}
    if os.path.exists(cache_path) and not args.item_id:
        with open(cache_path) as f:
            for entry in json.load(f):
                cache[entry["identifier"]] = entry

    # Skip already-tagged items (in DB)
    existing = check_existing_references()
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
            if ident in cache:
                references = cache[ident]["references"]
                print(f"  [{i+1}/{len(to_process)}] {ident} — cached ({len(references)} refs)")
            else:
                references = extract_references(client, args.model, title, content)
                if i < len(to_process) - 1:
                    time.sleep(0.3)

                ref_count = len(references)
                ref_names = ", ".join(r["reference_name"] for r in references[:3])
                if ref_count > 3:
                    ref_names += f", ... (+{ref_count - 3} more)"
                status = f"{ref_count} refs: {ref_names}" if ref_count else "no references"
                print(f"  [{i+1}/{len(to_process)}] {ident} — {status}")

            result = {
                "item_id": int(item_id),
                "identifier": ident,
                "title": title,
                "date": item["date"],
                "references": references,
            }
            all_results.append(result)
            cache[ident] = result

            summary = format_item_summary(item, references)
            review_lines.append(summary)

        except Exception as e:
            print(f"  [{i+1}/{len(to_process)}] {ident} — ERROR: {e}")
            errors.append({"identifier": ident, "item_id": item_id, "error": str(e)})

    # Save cache
    with open(cache_path, "w") as f:
        json.dump(list(cache.values()), f, separators=(",", ":"))

    # ── Write review file ─────────────────────────────────────────────────
    review_path = os.path.join(os.path.dirname(__file__), "jim_references_review.txt")
    with open(review_path, "w") as f:
        f.write("# JIM Stories — Literary & Cultural Reference Index\n")
        f.write(f"# {len(all_results)} items processed, {len(errors)} errors\n")
        f.write(f"# Model: {args.model}\n\n")
        for line in review_lines:
            f.write(line + "\n")
    print(f"\nPer-item review saved to: {review_path}")

    # ── Aggregate table ───────────────────────────────────────────────────
    agg_rows = build_aggregate_table(all_results)
    agg_path = os.path.join(os.path.dirname(__file__), "jim_references_aggregate.csv")
    write_csv(agg_rows, agg_path, [
        "reference_name", "reference_type", "total_mentions",
        "stories_appearing_in", "story_identifiers", "most_common_role",
    ])
    print(f"Aggregate CSV saved to: {agg_path}")
    print_aggregate_table(agg_rows)

    # ── Type breakdown ────────────────────────────────────────────────────
    type_rows = build_type_breakdown(all_results)
    type_path = os.path.join(os.path.dirname(__file__), "jim_references_type_breakdown.csv")
    write_csv(type_rows, type_path, [
        "reference_type", "unique_references", "total_mentions",
    ])
    print(f"\nType breakdown saved to: {type_path}")
    print_type_breakdown(type_rows)

    # ── Summary ───────────────────────────────────────────────────────────
    total_refs = sum(len(r["references"]) for r in all_results)
    items_with_refs = sum(1 for r in all_results if r["references"])
    items_without = sum(1 for r in all_results if not r["references"])

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"Items processed:         {len(all_results)}")
    print(f"Items with references:   {items_with_refs}")
    print(f"Items without:           {items_without}")
    print(f"Total references:        {total_refs}")
    print(f"Unique references:       {len(agg_rows)}")
    print(f"Errors:                  {len(errors)}")

    if errors:
        print(f"\nERRORS:")
        for e in errors:
            print(f"  {e['identifier']}: {e['error']}")

    # ── Apply to DB ───────────────────────────────────────────────────────
    if args.apply:
        print(f"\n{'=' * 70}")
        print("APPLYING TO DATABASE")
        print(f"{'=' * 70}")

        ref_names = collect_unique_references(all_results)

        # Step 1: Custom vocabulary
        print("\n--- Step 1: Custom Vocabulary ---")
        if check_vocab_exists():
            print(f"  Custom vocab {CUSTOM_VOCAB_ID} already exists — skipping.")
        else:
            create_vocab(ref_names)

        # Step 2: dcterms:relation values
        print("\n--- Step 2: dcterms:relation values ---")
        existing_in_db = check_existing_references()
        if existing_in_db:
            print(f"  {len(existing_in_db)} items already tagged — skipping those.")
        total_inserted = insert_relation_values(all_results, existing_in_db)
        print(f"  Inserted {total_inserted} relation values.")

        # Step 3: Faceted browse facet
        print("\n--- Step 3: Faceted Browse Facet ---")
        if check_facet_exists():
            print(f"  {FACET_NAME} facet already exists — skipping.")
        else:
            create_facet(ref_names)

        print(f"\nDone. Run: docker compose restart omeka")
    else:
        print("\nDry run complete. Use --apply to write results to DB.")


if __name__ == "__main__":
    main()

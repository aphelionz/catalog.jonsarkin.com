#!/usr/bin/env python3
"""
tag_narrative_voice.py — Classify narrative voice for JIM Stories.

Reads each JIM story's full text from the DB, sends it to Claude for
classification into one of four narrative voice categories, and writes
the result back as a curation:category value.

Usage:
  python scripts/tag_narrative_voice.py                  # dry-run (default)
  python scripts/tag_narrative_voice.py --apply          # write to DB
  python scripts/tag_narrative_voice.py --item-id 7509   # single item

Environment:
  ANTHROPIC_API_KEY — Required.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Optional

import anthropic

# ── Constants ──────────────────────────────────────────────────────────────

ITEM_SET_ID = 8020          # JIM Stories
PROPERTY_ID_CONTENT = 91    # bibo:content
PROPERTY_ID_IDENT = 10      # dcterms:identifier
PROPERTY_ID_TITLE = 1       # dcterms:title
PROPERTY_ID_CATEGORY = 1698 # curation:category
CUSTOM_VOCAB_TYPE = "customvocab:9"  # Narrative Voice

VALID_VOICES = ["Third Person", "First Person", "Second Person", "Mixed/Unstable"]

MODEL = "claude-opus-4-6"

SYSTEM_PROMPT = """\
You are a literary analyst classifying the narrative point of view in short prose pieces.

Classify the dominant narrative voice into exactly ONE of these four categories:

1. **Third Person** — Jim is referred to as "he/him/Jim" throughout. The narrator is external and observational. No "I" appears as a narrating voice.
2. **First Person** — The narrator speaks as "I" for the majority of the text. Jim may still be a character, but the narrator is a participant.
3. **Second Person** — The narrator addresses "you" as a character in the story (not rhetorical "you").
4. **Mixed/Unstable** — The perspective shifts between two or more of the above within the piece.

Classification rules:
- Be conservative about Mixed/Unstable. A single instance of rhetorical "you" in an otherwise third-person piece ("you know what I mean") does NOT make it Mixed. The shift needs to be a genuine change in who is narrating or who is being addressed.
- Dialogue and quoted speech don't count as perspective shifts. If Jim says "I hate this" in dialogue within a third-person narrative, it's still Third Person.
- An "I" narrator who is Jim's friend is First Person, not Mixed — unless the piece also contains stretches of third-person narration about Jim. The test: does the perspective shift, or is it stable in one mode?
- When in doubt between Third Person and Mixed/Unstable, look for the moment where the boundary between the author and Jim-the-character dissolves. That dissolution IS what we're tagging.
- Interior monologue rendered without quotes in a third-person narrative (free indirect discourse) is still Third Person — it's a narrative technique, not a perspective shift.

Respond with JSON only:
{"voice": "<one of the four categories>", "confidence": "high" or "borderline", "reasoning": "<1-2 sentences explaining your classification>"}"""


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
    """Fetch JIM story items with content, identifier, and title.

    Uses a record-separator approach to handle multi-line content fields.
    """
    where = f"AND iis.item_id = {int(item_id)}" if item_id else ""
    sep = "<<<REC>>>"
    sql = f"""
        SELECT CONCAT_WS('{sep}',
               iis.item_id,
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_IDENT} THEN v.value END),
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_TITLE} THEN v.value END),
               REPLACE(REPLACE(
                   MAX(CASE WHEN v.property_id = {PROPERTY_ID_CONTENT} THEN v.value END),
                   '\\n', '<<NL>>'), '\\r', '')
        ) AS row_data
        FROM item_item_set iis
        JOIN value v ON iis.item_id = v.resource_id
        WHERE iis.item_set_id = {ITEM_SET_ID}
          AND v.property_id IN ({PROPERTY_ID_IDENT}, {PROPERTY_ID_TITLE}, {PROPERTY_ID_CONTENT})
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
        if len(parts) < 4:
            continue
        content = parts[3].replace("<<NL>>", "\n")
        items.append({
            "item_id": parts[0],
            "identifier": parts[1],
            "title": parts[2],
            "content": content,
        })
    return items


def check_existing_tags() -> set[int]:
    """Return item IDs that already have a narrative voice tag."""
    sql = f"SELECT resource_id FROM `value` WHERE property_id = {PROPERTY_ID_CATEGORY};"
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        return set()
    return {int(row) for row in out[1:] if row.strip()}


# ── Classification ─────────────────────────────────────────────────────────

def classify_voice(client: anthropic.Anthropic, text: str) -> dict:
    """Send story text to Claude and get narrative voice classification."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Classify this piece:\n\n{text}"}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(raw)
    if result["voice"] not in VALID_VOICES:
        raise ValueError(f"Invalid voice: {result['voice']}")
    return result


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tag JIM Stories with narrative voice")
    parser.add_argument("--apply", action="store_true", help="Write results to DB (default: dry-run)")
    parser.add_argument("--item-id", type=int, help="Process a single item by ID")
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

    # Skip already-tagged items
    existing = check_existing_tags()
    to_process = [it for it in items if int(it["item_id"]) not in existing]
    if existing:
        print(f"Skipping {len(existing)} already-tagged items.")
    print(f"Processing {len(to_process)} items {'(DRY RUN)' if not args.apply else '(APPLYING)'}...\n")

    results = []
    borderline = []
    errors = []

    for i, item in enumerate(to_process):
        item_id = item["item_id"]
        ident = item["identifier"]
        title = item["title"]
        content = item["content"]

        try:
            classification = classify_voice(client, content)
            voice = classification["voice"]
            confidence = classification["confidence"]
            reasoning = classification["reasoning"]

            results.append({
                "item_id": int(item_id),
                "identifier": ident,
                "title": title,
                "voice": voice,
                "confidence": confidence,
                "reasoning": reasoning,
            })

            if confidence == "borderline":
                borderline.append(results[-1])

            status = "BORDERLINE" if confidence == "borderline" else "ok"
            print(f"  [{i+1}/{len(to_process)}] {ident} → {voice} ({status})")

            # Write to DB
            if args.apply:
                escaped_value = voice.replace("'", "\\'")
                sql = (
                    f"INSERT INTO `value` (resource_id, property_id, type, `value`, is_public) "
                    f"VALUES ({int(item_id)}, {PROPERTY_ID_CATEGORY}, '{CUSTOM_VOCAB_TYPE}', "
                    f"'{escaped_value}', 1);"
                )
                db_execute(sql)

        except Exception as e:
            print(f"  [{i+1}/{len(to_process)}] {ident} → ERROR: {e}")
            errors.append({"identifier": ident, "item_id": item_id, "error": str(e)})

        # Small delay between API calls
        if i < len(to_process) - 1:
            time.sleep(0.5)

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    counts = {}
    examples = {}
    for r in results:
        v = r["voice"]
        counts[v] = counts.get(v, 0) + 1
        if v not in examples:
            examples[v] = r["identifier"]

    print(f"\n{'Category':<20} {'Count':>5}  Example")
    print("-" * 50)
    for voice in VALID_VOICES:
        c = counts.get(voice, 0)
        ex = examples.get(voice, "—")
        print(f"{voice:<20} {c:>5}  {ex}")
    print(f"{'TOTAL':<20} {sum(counts.values()):>5}")

    if errors:
        print(f"\nErrors: {len(errors)}")
        for e in errors:
            print(f"  {e['identifier']}: {e['error']}")

    if borderline:
        print(f"\nBORDERLINE ITEMS ({len(borderline)}) — review manually:")
        print("-" * 70)
        for b in borderline:
            print(f"  {b['identifier']} ({b['title']})")
            print(f"    Classified as: {b['voice']}")
            print(f"    Reasoning: {b['reasoning']}")
            print()

    if not args.apply:
        print("\nDry run complete. Use --apply to write results to DB.")
    else:
        print(f"\nDone. {len(results)} items tagged in DB.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Export per-item motif ground truth for the CLIP pre-tagging pipeline.

Reads the live DB (read-only) plus sarkin-clip/clip_api/motif_vocab.json and writes:

  sarkin-clip/clip_api/motif_out/motif_data.json   consumed by motif_pretag.py
  scripts/motif_cleanup_map.csv                     review artifact for Mark

For every resource_template_id=2 item it computes the GENUINE per-piece visual
motifs (canonical), excluding box-derived placeholders, junk, and non-visual
tags, and records the box prior (cold-start hint) and year.

Run from repo root:  python3 scripts/export_motif_data.py
Read-only: issues only SELECTs via `docker compose exec db mariadb`.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOCAB_PATH = ROOT / "sarkin-clip" / "clip_api" / "motif_vocab.json"
OUT_DIR = ROOT / "sarkin-clip" / "clip_api" / "motif_out"
DATA_PATH = OUT_DIR / "motif_data.json"
CSV_PATH = ROOT / "scripts" / "motif_cleanup_map.csv"

HAIKU_MAX_ID = 8824  # items with id <= this were Haiku-enriched (per CLAUDE.md)

PROP_SUBJECT = 3
PROP_YEAR = 7
PROP_BOX = 1424


def db_query(sql: str) -> list[list[str]]:
    """Run a SELECT via the dockerised mariadb client; return rows of fields."""
    cmd = [
        "docker", "compose", "exec", "-T", "db",
        "mariadb", "-u", "root", "-proot", "omeka",
        "--batch", "--skip-column-names", "-e", sql,
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"DB query failed:\n{res.stderr}")
    rows = []
    for line in res.stdout.splitlines():
        if line == "":
            continue
        rows.append(line.split("\t"))
    return rows


def box_category(box_value: str) -> str:
    """Normalise a schema:box value to its bare category (mirrors seed SQL)."""
    s = re.sub(r"\(box\s*\d+\)", "", box_value)
    s = re.sub(r"\d+\s*$", "", s)
    return s.strip().lower()


def parse_year(value: str) -> int | None:
    m = re.search(r"(18|19|20)\d{2}", value)
    return int(m.group(0)) if m else None


def main() -> None:
    vocab = json.loads(VOCAB_PATH.read_text())
    value_map: dict = vocab["value_map"]
    value_map_ci: dict = {k.lower(): v for k, v in value_map.items()}
    box_seed_map: dict = {k.lower(): v for k, v in vocab["box_seed_map"].items()}
    box_prior_map: dict = {k.lower(): v for k, v in vocab["box_prior"].items()}

    def lookup(val: str) -> dict | None:
        """Exact match, then case-insensitive fallback (catches typos like 'MEsa')."""
        return value_map.get(val) or value_map_ci.get(val.lower())

    # ── pull rows ──────────────────────────────────────────────────────────
    item_rows = db_query(
        "SELECT r.id FROM resource r JOIN item i ON i.id=r.id "
        "WHERE r.resource_template_id=2"
    )
    item_ids = [int(r[0]) for r in item_rows]
    item_set = set(item_ids)

    def values_for(prop: int) -> list[tuple[int, str]]:
        rows = db_query(
            f"SELECT v.resource_id, v.value FROM value v "
            f"JOIN resource r ON r.id=v.resource_id AND r.resource_template_id=2 "
            f"WHERE v.property_id={prop} AND v.value IS NOT NULL"
        )
        out = []
        for r in rows:
            if len(r) < 2:
                continue
            out.append((int(r[0]), r[1]))
        return out

    subjects = defaultdict(list)
    for rid, val in values_for(PROP_SUBJECT):
        subjects[rid].append(val)

    years: dict[int, int] = {}
    for rid, val in values_for(PROP_YEAR):
        y = parse_year(val)
        if y and rid not in years:
            years[rid] = y

    box_cat: dict[int, str] = {}
    for rid, val in values_for(PROP_BOX):
        if rid in box_cat:
            continue
        cat = box_category(val)
        if cat:
            box_cat[rid] = cat

    # ── classify per item ──────────────────────────────────────────────────
    items_out: dict[str, dict] = {}
    genuine_counter: Counter = Counter()
    box_derived_rows = 0
    junk_rows = 0
    nonvisual_rows = 0

    for rid in item_ids:
        raws = subjects.get(rid, [])
        cat = box_cat.get(rid)
        box_seed_motif = box_seed_map.get(cat) if cat else None
        prior = box_prior_map.get(cat) if cat else None

        genuine: set[str] = set()
        for val in raws:
            action = lookup(val)
            if action is None:
                continue  # unmapped (should not happen; validated)
            # box-derived placeholder: exact original motif string for this item's box
            if box_seed_motif is not None and val == box_seed_motif:
                box_derived_rows += 1
                continue
            if action.get("drop"):
                junk_rows += 1
                continue
            if action.get("nonvisual"):
                nonvisual_rows += 1
                continue
            if "canon" in action:
                genuine.add(action["canon"])
            elif "split" in action:
                genuine.update(action["split"])

        for m in genuine:
            genuine_counter[m] += 1

        items_out[str(rid)] = {
            "genuine": sorted(genuine),
            "box_prior": prior,
            "year": years.get(rid),
            "haiku": rid <= HAIKU_MAX_ID,
        }

    # ── write motif_data.json ──────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "haiku_max_id": HAIKU_MAX_ID,
        "canonical_visual": vocab["canonical_visual"],
        "items": items_out,
    }, ensure_ascii=False))

    # ── write cleanup review CSV ────────────────────────────────────────────
    value_counts: Counter = Counter()
    for vals in subjects.values():
        for v in vals:
            value_counts[v] += 1
    with CSV_PATH.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["value", "count", "action", "target"])
        for val, n in value_counts.most_common():
            action = lookup(val) or {}
            if action.get("drop"):
                kind, target = "drop", ""
            elif action.get("nonvisual"):
                kind, target = "nonvisual (keep)", val
            elif "split" in action:
                kind, target = "split", " + ".join(action["split"])
            elif "canon" in action:
                kind = "rename" if action["canon"] != val else "keep"
                target = action["canon"]
            else:
                kind, target = "UNMAPPED", ""
            w.writerow([val, n, kind, target])

    # ── summary ─────────────────────────────────────────────────────────────
    tagged = sum(1 for it in items_out.values() if it["genuine"])
    total = len(items_out)
    print(f"items (template 2):       {total}")
    print(f"genuine-tagged:           {tagged}  ({tagged/total*100:.1f}%)")
    print(f"untagged (target set):    {total - tagged}  ({(total-tagged)/total*100:.1f}%)")
    print(f"box-derived rows skipped: {box_derived_rows}")
    print(f"junk rows skipped:        {junk_rows}")
    print(f"non-visual rows kept:     {nonvisual_rows}")
    print(f"items with a box prior:   {sum(1 for it in items_out.values() if it['box_prior'])}")
    print(f"\nwrote {DATA_PATH.relative_to(ROOT)}")
    print(f"wrote {CSV_PATH.relative_to(ROOT)}")
    print("\ngenuine exemplars per canonical motif (post-cleanup ground truth):")
    for m in vocab["canonical_visual"]:
        print(f"  {m:20s} {genuine_counter.get(m, 0)}")


if __name__ == "__main__":
    main()

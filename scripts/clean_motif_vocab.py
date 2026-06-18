#!/usr/bin/env python3
"""Clean the motif vocabulary on already-tagged works (dcterms:subject, property 3).

Three steps, driven by sarkin-clip/clip_api/motif_vocab.json:
  1. Remove box-derived placeholder tags (reversible: dumped to CSV first).
  2. Drop junk values (_, ??, typos, one-off noise).
  3. Merge duplicates / variants to the canonical visual term (and split GlyphsMoon).
     Non-visual tags are left untouched.

DRY RUN by default (no writes). To apply:

    make backup-db                       # ALWAYS first
    python3 scripts/clean_motif_vocab.py --apply

Box membership lives in schema:box (property 1424) and is NOT touched, so the
box signal is preserved for the pipeline's cold-start prior.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOCAB_PATH = ROOT / "sarkin-clip" / "clip_api" / "motif_vocab.json"
BACKUP_CSV = ROOT / "scripts" / "box_derived_backup.csv"

PROP_SUBJECT = 3
PROP_BOX = 1424


def db(sql: str, write: bool = False) -> str:
    cmd = ["docker", "compose", "exec", "-T", "db",
           "mariadb", "-u", "root", "-proot", "omeka", "--batch", "--skip-column-names"]
    res = subprocess.run(cmd, cwd=ROOT, input=sql, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"SQL failed:\n{res.stderr}\n---\n{sql[:400]}")
    return res.stdout


def box_category(v: str) -> str:
    v = re.sub(r"\(box\s*\d+\)", "", v)
    v = re.sub(r"\d+\s*$", "", v)
    return v.strip().lower()


def main() -> None:
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute writes (default: dry run)")
    args = ap.parse_args()
    apply = args.apply
    mode = "APPLY" if apply else "DRY RUN"

    vocab = json.loads(VOCAB_PATH.read_text())
    value_map = vocab["value_map"]
    value_map_ci = {k.lower(): v for k, v in value_map.items()}
    box_seed = {k.lower(): v for k, v in vocab["box_seed_map"].items()}

    def lookup(val):
        return value_map.get(val) or value_map_ci.get(val.lower())

    print(f"=== motif vocab cleanup [{mode}] ===\n")

    # current distinct values
    rows = db(f"SELECT v.value, COUNT(*) FROM value v "
              f"JOIN resource r ON r.id=v.resource_id AND r.resource_template_id=2 "
              f"WHERE v.property_id={PROP_SUBJECT} GROUP BY v.value")
    counts = {}
    for line in rows.splitlines():
        if "\t" in line:
            val, n = line.rsplit("\t", 1)
            counts[val] = int(n)
    print(f"distinct values now: {len(counts)} ({sum(counts.values())} rows)\n")

    # ── Step 1: box-derived placeholder rows (value.id list via box join) ────
    # Build the id list in Python from box memberships to keep it explicit.
    box_rows = db(f"SELECT resource_id, value FROM value v "
                  f"JOIN resource r ON r.id=v.resource_id AND r.resource_template_id=2 "
                  f"WHERE v.property_id={PROP_BOX} AND v.value IS NOT NULL")
    item_box_motif = {}
    for line in box_rows.splitlines():
        if "\t" not in line:
            continue
        rid, bval = line.split("\t", 1)
        cat = box_category(bval)
        if cat in box_seed and int(rid) not in item_box_motif:
            item_box_motif[int(rid)] = box_seed[cat]

    # value rows that are box-derived: subject value == the item's box motif
    subj_rows = db(f"SELECT v.id, v.resource_id, v.value FROM value v "
                   f"JOIN resource r ON r.id=v.resource_id AND r.resource_template_id=2 "
                   f"WHERE v.property_id={PROP_SUBJECT}")
    box_derived = []  # (value_id, resource_id, value)
    for line in subj_rows.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        vid, rid, val = int(parts[0]), int(parts[1]), parts[2]
        if item_box_motif.get(rid) == val:
            box_derived.append((vid, rid, val))
    bd_by_motif = Counter(v for _, _, v in box_derived)
    print(f"STEP 1 box-derived removal: {len(box_derived)} rows")
    for m, n in bd_by_motif.most_common():
        print(f"    {m:18s} {n}")

    # ── Step 2: junk ─────────────────────────────────────────────────────────
    junk = {val: n for val, n in counts.items()
            if (lookup(val) or {}).get("drop")}
    print(f"\nSTEP 2 junk drop: {sum(junk.values())} rows across {len(junk)} values")
    for val, n in sorted(junk.items(), key=lambda x: -x[1]):
        print(f"    {repr(val):14s} {n}")

    # ── Step 3: renames + splits ────────────────────────────────────────────
    renames = defaultdict(int)   # (old -> new)
    splits = {}
    for val, n in counts.items():
        act = lookup(val) or {}
        if act.get("drop"):
            continue
        if act.get("split"):
            splits[val] = act["split"]
        elif "canon" in act and act["canon"] != val:
            renames[(val, act["canon"])] += n
    print(f"\nSTEP 3 rename/merge to canonical: {len(renames)} mappings")
    for (old, new), n in sorted(renames.items(), key=lambda x: -x[1]):
        print(f"    {old:22s} -> {new:18s} ({n})")
    if splits:
        print(f"  splits: {splits}")

    if not apply:
        kept_nonvisual = sum(n for val, n in counts.items()
                             if (lookup(val) or {}).get("nonvisual"))
        print(f"\nnon-visual values kept untouched: {kept_nonvisual} rows")
        print("\nDRY RUN — no changes written. Re-run with --apply after `make backup-db`.")
        return

    # ════════════════════════ APPLY ════════════════════════
    print("\nAPPLYING ...")
    # 1. dump + delete box-derived
    with BACKUP_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["value_id", "resource_id", "value"])
        w.writerows(box_derived)
    print(f"  dumped {len(box_derived)} box-derived rows -> {BACKUP_CSV.relative_to(ROOT)}")
    ids = [str(vid) for vid, _, _ in box_derived]
    for i in range(0, len(ids), 5000):
        chunk = ",".join(ids[i:i + 5000])
        db(f"DELETE FROM value WHERE id IN ({chunk})", write=True)
    # 2. junk
    for val in junk:
        db(f"DELETE FROM value WHERE property_id={PROP_SUBJECT} "
           f"AND value='{val.replace(chr(39), chr(39)*2)}'", write=True)
    # 3a. splits (insert parts, delete original)
    for val, parts in splits.items():
        esc = val.replace("'", "''")
        rids = db(f"SELECT resource_id FROM value WHERE property_id={PROP_SUBJECT} "
                  f"AND value='{esc}'").split()
        for rid in rids:
            for term in parts:
                te = term.replace("'", "''")
                db(f"INSERT INTO value (resource_id, property_id, type, value, is_public) "
                   f"SELECT {int(rid)},{PROP_SUBJECT},'literal','{te}',1 FROM DUAL "
                   f"WHERE NOT EXISTS (SELECT 1 FROM value WHERE resource_id={int(rid)} "
                   f"AND property_id={PROP_SUBJECT} AND value='{te}')", write=True)
        db(f"DELETE FROM value WHERE property_id={PROP_SUBJECT} AND value='{esc}'", write=True)
    # 3b. renames
    for (old, new) in renames:
        db(f"UPDATE value SET value='{new.replace(chr(39),chr(39)*2)}' "
           f"WHERE property_id={PROP_SUBJECT} AND value='{old.replace(chr(39),chr(39)*2)}'",
           write=True)
    # 3c. dedup (item, value) pairs created by merges
    db(f"DELETE v1 FROM value v1 JOIN value v2 "
       f"WHERE v1.property_id={PROP_SUBJECT} AND v2.property_id={PROP_SUBJECT} "
       f"AND v1.resource_id=v2.resource_id AND v1.value=v2.value AND v1.id>v2.id", write=True)

    after = db(f"SELECT COUNT(DISTINCT value) FROM value v "
               f"JOIN resource r ON r.id=v.resource_id AND r.resource_template_id=2 "
               f"WHERE v.property_id={PROP_SUBJECT}").strip()
    print(f"  done. distinct values now: {after}")
    print(f"  box-derived removal is reversible via {BACKUP_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

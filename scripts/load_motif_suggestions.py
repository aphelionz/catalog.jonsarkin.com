#!/usr/bin/env python3
"""Load CLIP motif suggestions into the `motif_suggestions` staging table.

Reads sarkin-clip/clip_api/motif_out/motif_suggestions.csv (written by
motif_pretag.py) and upserts it into MariaDB. This is STAGING data only: nothing
public reads it, and tags do not enter the catalog until a human accepts them in
the RapidEditor. Re-running refreshes only the items present in the CSV (the
current target set), leaving suggestions for other items intact.

Run from repo root:  python3 scripts/load_motif_suggestions.py
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "sarkin-clip" / "clip_api" / "motif_out" / "motif_suggestions.csv"

DDL = """
CREATE TABLE IF NOT EXISTS motif_suggestions (
  item_id INT NOT NULL,
  motif VARCHAR(64) NOT NULL,
  score FLOAT NOT NULL,
  method VARCHAR(16) NOT NULL,
  band ENUM('high','medium','low') NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (item_id, motif),
  KEY idx_band (band),
  KEY idx_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def run_sql(sql: str) -> str:
    cmd = ["docker", "compose", "exec", "-T", "db",
           "mariadb", "-u", "root", "-proot", "omeka"]
    res = subprocess.run(cmd, cwd=ROOT, input=sql, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"SQL failed:\n{res.stderr}\n---\n{sql[:500]}")
    return res.stdout


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "''")


def main() -> None:
    if not CSV_PATH.exists():
        sys.exit(f"Not found: {CSV_PATH}. Run motif_pretag.py first.")

    ap = argparse.ArgumentParser()
    ap.add_argument("--truncate", action="store_true",
                    help="replace the whole table (use for full-corpus runs)")
    args = ap.parse_args()

    rows = list(csv.DictReader(CSV_PATH.open()))
    run_sql(DDL)
    if not rows:
        print("No suggestion rows in CSV; nothing to load.")
        return

    item_ids = sorted({int(r["item_id"]) for r in rows})

    if args.truncate:
        run_sql("TRUNCATE TABLE motif_suggestions;")
    else:
        # refresh only the items in this run
        for i in range(0, len(item_ids), 5000):
            chunk = ",".join(str(x) for x in item_ids[i:i + 5000])
            run_sql(f"DELETE FROM motif_suggestions WHERE item_id IN ({chunk});")

    # bulk insert in batches
    BATCH = 1000
    for i in range(0, len(rows), BATCH):
        vals = []
        for r in rows[i:i + BATCH]:
            band = r["band"] if r["band"] in ("high", "medium", "low") else "low"
            vals.append(
                f"({int(r['item_id'])},'{esc(r['motif'])}',{float(r['score'])},"
                f"'{esc(r['method'])}','{band}')"
            )
        run_sql(
            "INSERT INTO motif_suggestions (item_id,motif,score,method,band) VALUES "
            + ",".join(vals)
            + " ON DUPLICATE KEY UPDATE score=VALUES(score),method=VALUES(method),"
              "band=VALUES(band),created_at=CURRENT_TIMESTAMP;"
        )

    # summary
    print(f"loaded {len(rows)} suggestions for {len(item_ids)} items")
    print(run_sql(
        "SELECT band, COUNT(*) AS rows_, COUNT(DISTINCT item_id) AS items "
        "FROM motif_suggestions GROUP BY band ORDER BY FIELD(band,'high','medium','low');"
    ))


if __name__ == "__main__":
    main()

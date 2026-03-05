"""
Post-ingest rarity indexer.

Queries subjects directly from the Omeka MariaDB (source of truth),
builds corpus-wide motif frequency stats, computes per-item iconographic
rarity scores, and writes them back as payload fields on each Qdrant point.

Usage:
  python rarity_index.py             # compute and write rarity data
  python rarity_index.py --dry-run   # preview without writing

Designed to run after fetch_omeka.py completes ingestion.
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List

import pymysql
from qdrant_client import QdrantClient

from clip_api.rarity import build_corpus_stats, compute_item_rarity

# MariaDB (source of truth for subjects)
MYSQL_HOST = os.getenv("MYSQL_HOST", "db")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "omeka")
MYSQL_USER = os.getenv("MYSQL_USER", "omeka")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "omeka")
RESOURCE_TEMPLATE_ID = int(os.getenv("OMEKA_TEMPLATE_ID", "2"))

# Qdrant (write rarity scores back)
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "omeka_items")
PAYLOAD_BATCH_SIZE = 100

# SQL: fetch all dcterms:subject values for items matching the resource template.
# Omeka S stores linked-data property values in the `value` table;
# `property` + `vocabulary` identify dcterms:subject.
SUBJECTS_SQL = """
SELECT r.id AS item_id, v.value AS subject
FROM resource r
JOIN item i ON i.id = r.id
JOIN value v ON v.resource_id = r.id
JOIN property p ON v.property_id = p.id
JOIN vocabulary voc ON p.vocabulary_id = voc.id
WHERE voc.prefix = 'dcterms'
  AND p.local_name = 'subject'
  AND r.resource_template_id = %s
ORDER BY r.id
"""

# SQL: fetch all item IDs for the template (including those with no subjects).
ITEMS_SQL = """
SELECT r.id AS item_id
FROM resource r
JOIN item i ON i.id = r.id
WHERE r.resource_template_id = %s
"""


def fetch_all_subjects() -> Dict[int, List[str]]:
    """Query MariaDB for all items and their dcterms:subject values."""
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        database=MYSQL_DATABASE,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4",
    )
    try:
        items_subjects: Dict[int, List[str]] = {}

        # Get all item IDs first (so items with no subjects still get scored)
        with conn.cursor() as cur:
            cur.execute(ITEMS_SQL, (RESOURCE_TEMPLATE_ID,))
            for (item_id,) in cur.fetchall():
                items_subjects[item_id] = []

        # Fill in subjects
        with conn.cursor() as cur:
            cur.execute(SUBJECTS_SQL, (RESOURCE_TEMPLATE_ID,))
            for item_id, subject in cur.fetchall():
                items_subjects[item_id].append(subject)

    finally:
        conn.close()

    return items_subjects


def build_rarity_payloads(
    items_subjects: Dict[int, List[str]],
) -> Dict[int, Dict[str, Any]]:
    """Compute rarity for all items and return payload updates."""
    stats = build_corpus_stats(items_subjects)
    payloads: Dict[int, Dict[str, Any]] = {}

    for item_id, subjects in items_subjects.items():
        rarity = compute_item_rarity(subjects, stats)
        payloads[item_id] = {
            "rarity_score": rarity.score,
            "rarity_class_number": rarity.class_number,
            "rarity_motif_details": [
                {
                    "motif": d.motif,
                    "corpus_frequency": d.corpus_frequency,
                    "corpus_percentage": d.corpus_percentage,
                }
                for d in rarity.motif_details
            ],
            "rarity_corpus_size": rarity.corpus_size,
        }

    return payloads


def write_rarity_payloads(
    client: QdrantClient,
    collection: str,
    payloads: Dict[int, Dict[str, Any]],
) -> int:
    """Batch-update Qdrant point payloads with rarity data. Returns count."""
    point_ids = list(payloads.keys())
    written = 0

    for i in range(0, len(point_ids), PAYLOAD_BATCH_SIZE):
        batch_ids = point_ids[i : i + PAYLOAD_BATCH_SIZE]
        for pid in batch_ids:
            client.set_payload(
                collection_name=collection,
                payload=payloads[pid],
                points=[pid],
            )
            written += 1

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute and store rarity scores")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    client = QdrantClient(url=QDRANT_URL)

    print(f"Fetching subjects from MariaDB ({MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE})...")
    items_subjects = fetch_all_subjects()
    print(f"  Found {len(items_subjects)} items")

    if not items_subjects:
        print("No items found. Exiting.")
        return

    stats = build_corpus_stats(items_subjects)
    print(f"  Corpus stats: {len(stats.motif_counts)} unique motifs")

    # Show top/bottom motifs
    sorted_motifs = sorted(stats.motif_counts.items(), key=lambda x: -x[1])
    if sorted_motifs:
        print("  Most common:")
        for motif, count in sorted_motifs[:5]:
            pct = count / stats.total_items * 100
            print(f"    {motif}: {count} ({pct:.1f}%)")
        print("  Rarest:")
        for motif, count in sorted_motifs[-5:]:
            pct = count / stats.total_items * 100
            print(f"    {motif}: {count} ({pct:.1f}%)")

    print("Computing rarity scores...")
    payloads = build_rarity_payloads(items_subjects)

    # Distribution summary
    class_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for p in payloads.values():
        class_counts[p["rarity_class_number"]] += 1
    print("  Distribution classes:")
    labels = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
    for cls in sorted(class_counts):
        print(f"    Class {labels[cls]}: {class_counts[cls]} items")

    if args.dry_run:
        print("Dry run — no changes written.")
        return

    print(f"Writing rarity payloads to {QDRANT_COLLECTION}...")
    written = write_rarity_payloads(client, QDRANT_COLLECTION, payloads)
    print(f"  Updated {written} points.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
doctor_catalog.py — Diagnostic report for Jon Sarkin catalog completeness.

Checks every catalog item for missing or invalid metadata and outputs a
plain-text report to stdout. Redirect to a file with:

    python scripts/doctor_catalog.py > reports/doctor-2026-03-04.txt

Or via Make:

    make doctor-catalog > reports/doctor-2026-03-04.txt

Progress messages go to stderr so they don't pollute the report.
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(line_buffering=True)

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Import reusable pieces from the enrichment script
sys.path.insert(0, str(Path(__file__).parent))
from enrich_metadata import (
    OMEKA_BASE,
    RESOURCE_TEMPLATE_ID,
    WORK_TYPES,
    SUPPORTS,
    SIGNATURE_ARROWS,
    get_items_page,
    extract_value,
    extract_all_values,
)

# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class Issue:
    field: str
    severity: str   # "WARN" or "ERROR"
    message: str

@dataclass
class ItemResult:
    item_id: int
    identifier: str
    title: str
    admin_url: str
    issues: List[Issue] = field(default_factory=list)
    has_transcription: bool = False


# ── Check functions ──────────────────────────────────────────────────────

TEMP_ID_RE = re.compile(r'^JS-\d{4}-T\d+$')


def check_title(item: dict) -> List[Issue]:
    val = extract_value(item, "dcterms:title")
    if not val or val == "Untitled":
        return [Issue("Title", "WARN", "missing")]
    return []


def check_identifier(item: dict) -> List[Issue]:
    val = extract_value(item, "dcterms:identifier")
    issues = []
    if not val:
        issues.append(Issue("Catalog Number", "WARN", "missing"))
    elif TEMP_ID_RE.match(val):
        issues.append(Issue("Catalog Number", "WARN", f"temporary placeholder ({val})"))
    return issues


def check_creator(item: dict) -> List[Issue]:
    refs = item.get("schema:creator", [])
    has_ref = any(v.get("value_resource_id") for v in refs)
    if not has_ref:
        return [Issue("Creator", "ERROR", "no resource reference found")]
    return []


def check_work_type(item: dict) -> List[Issue]:
    val = extract_value(item, "dcterms:type")
    if not val:
        return [Issue("Work Type", "WARN", "missing")]
    if val not in WORK_TYPES:
        return [Issue("Work Type", "ERROR", f"invalid value \"{val}\"")]
    return []


def check_medium(item: dict) -> List[Issue]:
    if not extract_value(item, "dcterms:medium"):
        return [Issue("Medium", "WARN", "missing")]
    return []


def check_support(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:artworkSurface")
    if not val:
        return [Issue("Support", "WARN", "missing")]
    if val not in SUPPORTS:
        return [Issue("Support", "ERROR", f"invalid value \"{val}\"")]
    return []


def check_height(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:height")
    if not val:
        return [Issue("Height", "WARN", "missing")]
    try:
        float(val)
    except ValueError:
        return [Issue("Height", "ERROR", f"non-numeric value \"{val}\"")]
    return []


def check_width(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:width")
    if not val:
        return [Issue("Width", "WARN", "missing")]
    try:
        float(val)
    except ValueError:
        return [Issue("Width", "ERROR", f"non-numeric value \"{val}\"")]
    return []


def check_signature(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:distinguishingSign")
    if not val:
        return [Issue("Signature", "WARN", "missing")]
    if not (len(val) == 1 and val in SIGNATURE_ARROWS):
        return [Issue("Signature", "ERROR", f"invalid \"{val}\" (expected single arrow)")]
    return []


def check_framing(item: dict) -> List[Issue]:
    if not extract_value(item, "dcterms:format"):
        return [Issue("Framing", "WARN", "missing")]
    return []


def check_owner(item: dict) -> List[Issue]:
    if not extract_value(item, "bibo:owner"):
        return [Issue("Owner", "WARN", "missing")]
    return []


def check_location(item: dict) -> List[Issue]:
    if not extract_value(item, "dcterms:spatial"):
        return [Issue("Location", "WARN", "missing")]
    return []


def check_subject(item: dict) -> List[Issue]:
    if not extract_all_values(item, "dcterms:subject"):
        return [Issue("Subject / Motif", "WARN", "missing")]
    return []


def check_date(item: dict) -> List[Issue]:
    if not extract_value(item, "dcterms:date"):
        return [Issue("Date", "WARN", "missing")]
    return []


def check_media(item: dict) -> List[Issue]:
    if not item.get("o:media"):
        return [Issue("Media", "ERROR", "no media attachments")]
    return []


def check_box(item: dict) -> List[Issue]:
    if not extract_value(item, "schema:box"):
        return [Issue("Box", "WARN", "missing")]
    return []


ALL_CHECKS = [
    check_title, check_identifier, check_creator, check_work_type,
    check_medium, check_support, check_height, check_width,
    check_signature, check_framing, check_owner, check_location,
    check_subject, check_date, check_media, check_box,
]


# ── Fetching ─────────────────────────────────────────────────────────────

def fetch_all_items(limit: int = 0) -> list:
    """Fetch all catalog items (no filtering). Progress to stderr."""
    print(f"Fetching items from {OMEKA_BASE}...", file=sys.stderr)
    items = []
    page = 1
    target = limit or float("inf")

    while len(items) < target:
        batch, total = get_items_page(page)
        if page == 1:
            print(f"Total items in catalog: {total}", file=sys.stderr)
        if not batch:
            break
        items.extend(batch)
        print(f"  Page {page}: fetched {len(items)} items...", file=sys.stderr)
        page += 1

    if limit:
        items = items[:limit]
    print(f"Fetched {len(items)} items.", file=sys.stderr)
    return items


# ── Report ───────────────────────────────────────────────────────────────

FIELD_ORDER = [
    "Title", "Catalog Number", "Creator", "Work Type", "Medium",
    "Support", "Height", "Width", "Signature", "Framing", "Owner",
    "Location", "Subject / Motif", "Date", "Media", "Box",
]

SEP = "=" * 64
THIN_SEP = "-" * 55


def print_report(results: List[ItemResult], total_items: int,
                 field_counts: Counter, temp_id_count: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_issues = sum(len(r.issues) for r in results)

    print(SEP)
    print("  Jon Sarkin Catalog \u2014 Doctor Report")
    print(f"  Generated: {now}")
    print(f"  Source: {OMEKA_BASE}")
    print(SEP)
    print()
    print(f"  Total items scanned:  {total_items:>6,}")
    print(f"  Items with issues:    {len(results):>6,}")
    print(f"  Total issues found:   {total_issues:>6,}")
    print()

    # ── Per-item details ──
    print(SEP)
    print("  ITEMS WITH ISSUES")
    print(SEP)

    for r in results:
        title_part = f"  \"{r.title}\"" if r.title else "  (no title)"
        print()
        print(f"  {r.identifier}{title_part}")
        print(f"  {r.admin_url}")
        print(f"  {THIN_SEP}")
        for issue in r.issues:
            print(f"  {issue.severity:<7}{issue.field} \u2014 {issue.message}")
        tx = "present" if r.has_transcription else "absent"
        print(f"  (transcription: {tx})")

    # ── Field summary ──
    print()
    print(SEP)
    print("  FIELD SUMMARY")
    print(SEP)
    print()
    print(f"  {'Field':<24}{'Missing':>8}    {'% of total':>10}")
    print(f"  {THIN_SEP}")

    for f in FIELD_ORDER:
        count = field_counts.get(f, 0)
        pct = (count / total_items * 100) if total_items else 0
        print(f"  {f:<24}{count:>8,}    {pct:>9.1f}%")

    print()
    print(f"  Temporary catalog numbers:  {temp_id_count:,}")
    print()
    print(SEP)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Check catalog items for completeness issues.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only check the first N items (0 = all)")
    args = parser.parse_args()

    items = fetch_all_items(args.limit)

    results: List[ItemResult] = []
    field_counts: Counter = Counter()
    temp_id_count = 0

    for item in items:
        item_id = item["o:id"]
        identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
        title = extract_value(item, "dcterms:title") or ""
        admin_url = f"{OMEKA_BASE}/admin/item/{item_id}"
        has_tx = bool(extract_value(item, "bibo:content"))

        issues: List[Issue] = []
        for check in ALL_CHECKS:
            issues.extend(check(item))

        # Count missing fields
        for issue in issues:
            if "missing" in issue.message or "no resource" in issue.message or "no media" in issue.message:
                field_counts[issue.field] += 1

        # Count temporary IDs
        if any(i.field == "Catalog Number" and "placeholder" in i.message for i in issues):
            temp_id_count += 1

        if issues:
            results.append(ItemResult(
                item_id=item_id,
                identifier=identifier,
                title=title if title != "Untitled" else "",
                admin_url=admin_url,
                issues=issues,
                has_transcription=has_tx,
            ))

    print_report(results, len(items), field_counts, temp_id_count)


if __name__ == "__main__":
    main()

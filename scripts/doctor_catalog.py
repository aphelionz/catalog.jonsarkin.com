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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

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


# ── Check functions ──────────────────────────────────────────────────────

TEMP_ID_RE = re.compile(r'^JS-\d{4}-T\d+$')


def check_identifier(item: dict) -> List[Issue]:
    val = extract_value(item, "dcterms:identifier")
    issues = []
    if not val:
        issues.append(Issue("Catalog Number", "ERROR", "missing"))
    elif TEMP_ID_RE.match(val):
        issues.append(Issue("Catalog Number", "WARN", "temporary placeholder"))
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
        return [Issue("Work Type", "ERROR", "missing")]
    if val not in WORK_TYPES:
        return [Issue("Work Type", "ERROR", f"invalid value \"{val}\"")]
    return []


def check_medium(item: dict) -> List[Issue]:
    if not extract_value(item, "dcterms:medium"):
        return [Issue("Medium", "ERROR", "missing")]
    return []


def check_support(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:artworkSurface")
    if not val:
        return [Issue("Support", "ERROR", "missing")]
    if val not in SUPPORTS:
        return [Issue("Support", "ERROR", f"invalid value \"{val}\"")]
    return []


def check_height(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:height")
    if not val:
        return [Issue("Height", "ERROR", "missing")]
    try:
        float(val)
    except ValueError:
        return [Issue("Height", "ERROR", f"non-numeric value \"{val}\"")]
    return []


def check_width(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:width")
    if not val:
        return [Issue("Width", "ERROR", "missing")]
    try:
        float(val)
    except ValueError:
        return [Issue("Width", "ERROR", f"non-numeric value \"{val}\"")]
    return []


def check_signature(item: dict) -> List[Issue]:
    val = extract_value(item, "schema:distinguishingSign")
    if not val:
        return [Issue("Signature", "ERROR", "missing")]
    if not (len(val) == 1 and val in SIGNATURE_ARROWS):
        return [Issue("Signature", "ERROR", f"invalid \"{val}\" (expected single arrow)")]
    return []


def check_framing(item: dict) -> List[Issue]:
    if not extract_value(item, "dcterms:format"):
        return [Issue("Framing", "ERROR", "missing")]
    return []


def check_owner(item: dict) -> List[Issue]:
    if not extract_value(item, "bibo:owner"):
        return [Issue("Owner", "ERROR", "missing")]
    return []


def check_location(item: dict) -> List[Issue]:
    if not extract_value(item, "dcterms:spatial"):
        return [Issue("Location", "ERROR", "missing")]
    return []


def check_subject(item: dict) -> List[Issue]:
    if not extract_all_values(item, "dcterms:subject"):
        return [Issue("Subject / Motif", "ERROR", "missing")]
    return []


EXIF_TS_RE = re.compile(r'^\d{4}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}$')
ISO_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]')
YEAR_RE = re.compile(r'\d{4}')
APPROX_RE = re.compile(r'^c\.\s')

# Jon's art career began after his 1987 surgery; he died in 2024.
EARLIEST_VALID_YEAR = 1989
DEATH_YEAR = 2024


def check_date(item: dict) -> List[Issue]:
    val = extract_value(item, "dcterms:date")
    if not val:
        return [Issue("Date", "ERROR", "missing")]

    issues = []

    # EXIF timestamp (e.g. "2025:11:12 09:48:13")
    if EXIF_TS_RE.match(val):
        return [Issue("Date", "ERROR", "EXIF timestamp")]

    # ISO timestamp (e.g. "2025-11-12T09:48:13")
    if ISO_TS_RE.match(val):
        return [Issue("Date", "ERROR", "ISO timestamp")]

    # Approximate date — warn (often means unsigned, date is a guess)
    if APPROX_RE.match(val):
        issues.append(Issue("Date", "WARN", "approximate — may indicate unsigned piece"))

    # Extract the first four-digit year for range checks
    m = YEAR_RE.search(val)
    if m:
        year = int(m.group())
        if year < EARLIEST_VALID_YEAR:
            issues.append(Issue("Date", "ERROR", f"pre-{EARLIEST_VALID_YEAR}"))
        elif year > DEATH_YEAR:
            issues.append(Issue("Date", "ERROR", f"posthumous (Jon died {DEATH_YEAR})"))

    return issues


def check_media(item: dict) -> List[Issue]:
    if not item.get("o:media"):
        return [Issue("Media", "ERROR", "no media attachments")]
    return []


def check_box(item: dict) -> List[Issue]:
    if not extract_value(item, "schema:box"):
        return [Issue("Box", "ERROR", "missing")]
    return []


def check_transcription(item: dict) -> List[Issue]:
    if not extract_value(item, "bibo:content"):
        return [Issue("Transcription", "ERROR", "missing")]
    return []


ALL_CHECKS = [
    check_identifier, check_creator, check_work_type,
    check_medium, check_support, check_height, check_width,
    check_signature, check_framing, check_owner, check_location,
    check_subject, check_date, check_media, check_box,
    check_transcription,
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

SEP = "=" * 64
THIN_SEP = "-" * 55


def print_report(findings: dict, total_items: int, items_with_issues: int,
                 total_issues: int, *, show_warnings: bool = False):
    """Print report grouped by finding, with admin URLs listed under each."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(SEP)
    print("  Jon Sarkin Catalog \u2014 Doctor Report")
    print(f"  Generated: {now}")
    print(f"  Source: {OMEKA_BASE}")
    print(SEP)
    print()
    print(f"  Total items scanned:  {total_items:>6,}")
    print(f"  Items with issues:    {items_with_issues:>6,}")
    print(f"  Total issues found:   {total_issues:>6,}")

    # Split findings by severity
    errors = {k: v for k, v in findings.items() if k.startswith("ERROR")}
    warns = {k: v for k, v in findings.items() if k.startswith("WARN")}

    if errors:
        print()
        print(SEP)
        print("  ERRORS")
        print(SEP)
        for key, urls in errors.items():
            print()
            print(f"  [{key}]")
            print(f"  {THIN_SEP}")
            for url in urls:
                print(f"  {url}")
            print(f"  ({len(urls)} items)")

    if warns and show_warnings:
        print()
        print(SEP)
        print("  WARNINGS")
        print(SEP)
        for key, urls in warns.items():
            print()
            print(f"  [{key}]")
            print(f"  {THIN_SEP}")
            for url in urls:
                print(f"  {url}")
            print(f"  ({len(urls)} items)")
    elif warns:
        total_warns = sum(len(v) for v in warns.values())
        print()
        print(f"  ({total_warns} warnings hidden — use --warn to show)")

    print()
    print(SEP)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Check catalog items for completeness issues.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only check the first N items (0 = all)")
    parser.add_argument("--warn", action="store_true",
                        help="Include warnings in the report (default: errors only)")
    args = parser.parse_args()

    items = fetch_all_items(args.limit)

    # findings: "SEVERITY Field — message" → [admin_url, ...]
    findings: dict[str, list[str]] = {}
    items_with_issues = 0
    total_issues = 0

    for item in items:
        item_id = item["o:id"]
        admin_url = f"{OMEKA_BASE}/admin/item/{item_id}/edit"

        issues: List[Issue] = []
        for check in ALL_CHECKS:
            issues.extend(check(item))

        if issues:
            items_with_issues += 1
            total_issues += len(issues)
            for issue in issues:
                key = f"{issue.severity} {issue.field} \u2014 {issue.message}"
                findings.setdefault(key, []).append(admin_url)

    print_report(findings, len(items), items_with_issues, total_issues,
                 show_warnings=args.warn)


if __name__ == "__main__":
    main()

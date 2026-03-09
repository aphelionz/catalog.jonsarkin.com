#!/usr/bin/env python3
"""
import_writings.py — Import harvested jsarkin.com writings into Omeka S.

Reads the harvest JSON and creates Omeka items using the "Writing (Jon Sarkin)"
resource template. Each item gets: title, identifier, creator link, date,
work type, full text, literary form, description (excerpt), source URL, rights,
and credit line.

Usage:
  python scripts/import_writings.py --dry-run --limit 5   # Preview
  python scripts/import_writings.py --limit 50             # Import first 50
  python scripts/import_writings.py                        # Import all 511

Environment variables:
  OMEKA_BASE_URL       — Omeka S base URL (default: http://localhost:8888)
  OMEKA_KEY_IDENTITY   — API key identity (default: catalog_api)
  OMEKA_KEY_CREDENTIAL — API key credential (default: sarkin2024)
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(line_buffering=True)

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests

# ── Configuration ────────────────────────────────────────────────────────

OMEKA_BASE = os.getenv("OMEKA_BASE_URL", "http://localhost:8888")
OMEKA_KEY_ID = os.getenv("OMEKA_KEY_IDENTITY")
OMEKA_KEY_CRED = os.getenv("OMEKA_KEY_CREDENTIAL")
if not OMEKA_KEY_ID or not OMEKA_KEY_CRED:
    raise SystemExit("OMEKA_KEY_IDENTITY and OMEKA_KEY_CREDENTIAL env vars are required")

HARVEST_JSON = Path(__file__).resolve().parent.parent / "harvest" / "sarkin_jsarkin_complete.json"
IMAGES_DIR = Path(__file__).resolve().parent.parent / "harvest" / "images"

# IDs from the Omeka instance
RESOURCE_TEMPLATE_ID = 3    # "Writing (Jon Sarkin)"
RESOURCE_CLASS_ID = 118     # schema:CreativeWork
ITEM_SET_ID = 7502          # "jsarkin.com Writings, 1997–2019"
CREATOR_ITEM_ID = 3         # Jon Sarkin Person item

# Property IDs
PROP = {
    "dcterms:title":                  1,
    "dcterms:identifier":            10,
    "dcterms:date":                   7,
    "dcterms:type":                   8,
    "dcterms:description":            4,
    "dcterms:source":                11,
    "dcterms:rights":                15,
    "dcterms:bibliographicCitation": 48,
    "dcterms:subject":                3,
    "schema:creator":               921,
    "schema:creditText":           1343,
    "schema:genre":                1610,
    "bibo:content":                  91,
}

COPYRIGHT = "\u00a9 The Jon Sarkin Estate. All rights reserved. Rights managed by Artist Rights Society (ARS), New York."

# Map harvest content_type → Literary Form custom vocab term
LITERARY_FORM_MAP = {
    "poetry": "Poetry",
    "prose": "Prose",
    "prose_poem": "Prose Poem",
    "essay": "Essay",
    "gallery": "Photo Essay",
    "other": "Prose Poem",  # default for ambiguous
}

log = logging.getLogger(__name__)


# ── Omeka API helpers ────────────────────────────────────────────────────

def omeka_session() -> requests.Session:
    """Create a requests session with auth params."""
    s = requests.Session()
    s.params = {"key_identity": OMEKA_KEY_ID, "key_credential": OMEKA_KEY_CRED}
    s.headers["Content-Type"] = "application/json"
    return s


def _literal(prop_id: int, value: str) -> dict:
    """Build a literal property value."""
    return {
        "type": "literal",
        "property_id": prop_id,
        "@value": value,
    }


def _custom_vocab(prop_id: int, value: str, vocab_id: int) -> dict:
    """Build a custom vocab property value."""
    return {
        "type": f"customvocab:{vocab_id}",
        "property_id": prop_id,
        "@value": value,
    }


def _resource_link(prop_id: int, resource_id: int) -> dict:
    """Build a resource link property value."""
    return {
        "type": "resource:item",
        "property_id": prop_id,
        "value_resource_id": resource_id,
    }


def build_item_payload(item: dict) -> dict:
    """Build an Omeka item creation payload from a harvest item."""
    title = item["title"] or "Untitled"
    date = item.get("date_iso", "")
    content_type = item.get("content_type", "other")
    literary_form = LITERARY_FORM_MAP.get(content_type, "Prose Poem")
    body = item.get("body", "")
    excerpt = body[:200].rstrip() + "..." if len(body) > 200 else body
    # Use Wayback URL as source (jsarkin.com is gone)
    all_urls = item.get("all_source_urls", [])
    source_url = all_urls[0]["wayback_url"] if all_urls else ""

    # Credit line
    date_part = f", {date}" if date else ""
    credit = f"Jon Sarkin, \u201c{title}\u201d{date_part}. Published on jsarkin.com."

    payload = {
        "o:resource_template": {"o:id": RESOURCE_TEMPLATE_ID},
        "o:resource_class": {"o:id": RESOURCE_CLASS_ID},
        "o:item_set": [{"o:id": ITEM_SET_ID}],
        "o:is_public": True,
        "dcterms:title": [_literal(PROP["dcterms:title"], title)],
        "dcterms:identifier": [_literal(PROP["dcterms:identifier"], item["id"])],
        "dcterms:date": [_literal(PROP["dcterms:date"], date)] if date else [],
        "dcterms:type": [_custom_vocab(PROP["dcterms:type"], "Writing", 2)],
        "dcterms:description": [_literal(PROP["dcterms:description"], excerpt)] if excerpt else [],
        "dcterms:source": [_literal(PROP["dcterms:source"], source_url)] if source_url else [],
        "dcterms:rights": [_literal(PROP["dcterms:rights"], COPYRIGHT)],
        "schema:creator": [_resource_link(PROP["schema:creator"], CREATOR_ITEM_ID)],
        "schema:creditText": [_literal(PROP["schema:creditText"], credit)],
        "schema:genre": [_custom_vocab(PROP["schema:genre"], literary_form, 8)],
        "bibo:content": [_literal(PROP["bibo:content"], body)] if body else [],
    }

    # Remove empty property arrays
    payload = {k: v for k, v in payload.items() if v or not isinstance(v, list)}

    return payload


def create_item(session: requests.Session, payload: dict) -> dict:
    """Create an Omeka item via POST."""
    resp = session.post(f"{OMEKA_BASE}/api/items", json=payload)
    resp.raise_for_status()
    return resp.json()


def upload_media(session: requests.Session, item_id: int, filepath: Path, alt_text: str = "") -> dict | None:
    """Upload an image file as media on an Omeka item."""
    if not filepath.exists():
        return None

    # Media upload uses multipart form, not JSON
    data = {
        "o:ingester": "upload",
        "o:item": json.dumps({"o:id": item_id}),
        "dcterms:title": json.dumps([{
            "type": "literal",
            "property_id": PROP["dcterms:title"],
            "@value": alt_text or filepath.stem,
        }]),
    }
    files = {"file[0]": (filepath.name, filepath.open("rb"))}

    # Remove Content-Type header for multipart
    headers = dict(session.headers)
    headers.pop("Content-Type", None)

    resp = session.post(
        f"{OMEKA_BASE}/api/media",
        params=session.params,
        data=data,
        files=files,
        headers=headers,
    )
    if resp.status_code == 200:
        return resp.json()
    else:
        log.warning("  Failed to upload media %s: %s %s", filepath.name, resp.status_code, resp.text[:200])
        return None


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import harvested writings into Omeka S.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating items")
    parser.add_argument("--limit", type=int, help="Import only first N items")
    parser.add_argument("--with-media", action="store_true", help="Upload images as media")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    if not HARVEST_JSON.exists():
        print(f"ERROR: Harvest JSON not found at {HARVEST_JSON}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(HARVEST_JSON.read_text())
    items = data["items"]

    if args.limit:
        items = items[:args.limit]

    print(f"{'DRY RUN: ' if args.dry_run else ''}Importing {len(items)} writings into Omeka S")
    print(f"  Template: Writing (Jon Sarkin) (id={RESOURCE_TEMPLATE_ID})")
    print(f"  Item Set: jsarkin.com Writings (id={ITEM_SET_ID})")
    print()

    session = omeka_session()
    created = 0
    errors = 0
    media_uploaded = 0

    for i, item in enumerate(items, 1):
        payload = build_item_payload(item)
        title = item["title"] or "Untitled"
        harvest_id = item["id"]

        if args.dry_run:
            literary_form = LITERARY_FORM_MAP.get(item.get("content_type", "other"), "Prose Poem")
            img_count = len(item.get("images", []))
            print(f"  [{harvest_id}] {title[:60]}  ({literary_form}, {item.get('word_count', 0)} words, {img_count} images)")
            created += 1
            continue

        try:
            result = create_item(session, payload)
            omeka_id = result["o:id"]
            created += 1

            # Upload images if requested
            if args.with_media and item.get("images"):
                for img in item["images"]:
                    img_path = IMAGES_DIR / img["filename"]
                    alt = img.get("alt_text", "") or img.get("caption", "") or img.get("title_attr", "")
                    media = upload_media(session, omeka_id, img_path, alt)
                    if media:
                        media_uploaded += 1

            if i % 25 == 0 or i == len(items):
                log.info("Progress: %d/%d created (%d errors)", created, len(items), errors)

            # Brief pause to avoid hammering the API
            time.sleep(0.1)

        except requests.HTTPError as e:
            errors += 1
            log.error("Failed to create [%s] %s: %s", harvest_id, title[:40], e)
            if errors > 10:
                log.error("Too many errors, aborting.")
                break

    print()
    print(f"{'DRY RUN ' if args.dry_run else ''}Import complete:")
    print(f"  Created: {created}")
    if errors:
        print(f"  Errors:  {errors}")
    if media_uploaded:
        print(f"  Media:   {media_uploaded}")


if __name__ == "__main__":
    main()

"""
Fetch Omeka-S items and ingest each into Qdrant via embed_image_to_qdrant.embed_and_upsert.

Usage:
  ANTHROPIC_API_KEY=... python fetch_omeka.py          # incremental (default)
  ANTHROPIC_API_KEY=... python fetch_omeka.py --force   # full re-ingest
  ANTHROPIC_API_KEY=... python fetch_omeka.py --dry-run  # preview only

Optional auth:
  export OMEKA_ID=...
  export OMEKA_KEY=...

Catalog v2 rule:
  - Only ingest items whose resource template title is "Artwork (Jon Sarkin)" (configurable via OMEKA_TEMPLATE_TITLE).
  - Log (print) Title and Image URL for ingested items (identifier logged only if present).

Notes:
  - Downloads the primary media image for each item (skips items with no media).
  - Maps basic fields: title, description, subjects, year (from dcterms:date or created year).
  - Uses a temporary image file per item.
  - Parallelized with a thread pool; set INGEST_WORKERS (default 3) to tune.
  - Incremental by default: compares Omeka o:modified against Qdrant updated_at
    to skip unchanged items. Use --force for a full re-ingest.
"""
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

import requests
from qdrant_client import QdrantClient

from embed_image_to_qdrant import embed_and_upsert

BASE = "https://catalog.jonsarkin.com"
ITEMS_URL = f"{BASE}/api/items"
TEMPLATES_URL = f"{BASE}/api/resource_templates"
TARGET_TEMPLATE_TITLE = os.getenv("OMEKA_TEMPLATE_TITLE", "Artwork (Jon Sarkin)")
TARGET_TEMPLATE_ID = os.getenv("OMEKA_TEMPLATE_ID")
DEFAULT_TEMPLATE_ID = 2  # catalog v1: Artwork (Jon Sarkin)
MEDIA_CACHE_DIR = Path(".omeka_media_cache")
MEDIA_CACHE_DIR.mkdir(exist_ok=True)
MAX_WORKERS = int(os.getenv("INGEST_WORKERS", "3"))

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "omeka_items")

KEYS = {}
if os.getenv("OMEKA_ID") and os.getenv("OMEKA_KEY"):
    KEYS = {"key_identity": os.getenv("OMEKA_ID"), "key_credential": os.getenv("OMEKA_KEY")}


def get_json(url, params=None):
    params = params or {}
    resp = requests.get(url, params={**params, **KEYS}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def first_value(obj, key):
    vals = obj.get(key, [])
    return vals[0]["@value"] if vals else ""


def list_values(obj, key):
    return [v["@value"] for v in obj.get(key, [])]


template_cache = {}

def resource_template_title(obj):
    tpl = obj.get("o:resource_template")
    if not tpl:
        return ""
    tpl_id = tpl["@id"]
    if tpl_id in template_cache:
        return template_cache[tpl_id]
    try:
        tpl_obj = get_json(tpl_id)
        title = tpl_obj.get("o:title", "")
        template_cache[tpl_id] = title
        return title
    except Exception:
        return ""


def find_template_id_by_title(title):
    if TARGET_TEMPLATE_ID:
        print(f"Using template id from env OMEKA_TEMPLATE_ID={TARGET_TEMPLATE_ID}")
        return int(TARGET_TEMPLATE_ID)
    # fallback: known template id for catalog v1
    if title == "Artwork (Jon Sarkin)":
        return DEFAULT_TEMPLATE_ID

    page = 1
    per_page = 100
    found_any = False
    while True:
        templates = get_json(TEMPLATES_URL, params={"page": page, "per_page": per_page})
        if not templates:
            break
        found_any = True
        for tpl in templates:
            # Omeka-S exposes template label as o:label (sometimes also o:title)
            tpl_title = tpl.get("o:title") or tpl.get("o:label")
            if tpl_title == title:
                return tpl["o:id"]
        page += 1
    if not found_any:
        print("No resource templates returned; check API auth (OMEKA_ID/OMEKA_KEY).")
    else:
        print("Template not found. Available template titles:")
        page = 1
        while True:
            templates = get_json(TEMPLATES_URL, params={"page": page, "per_page": per_page})
            if not templates:
                break
            for tpl in templates:
                print(f"  - {tpl.get('o:title')}")
            page += 1
    return None


def first_year(obj):
    # Try dcterms:date first
    dates = list_values(obj, "dcterms:date")
    for d in dates:
        if d and d[:4].isdigit():
            return int(d[:4])
    # fallback to created year
    created = obj.get("o:created", {}).get("@value", "")
    if created and created[:4].isdigit():
        return int(created[:4])
    return 0


def download_image(url: str, dest: Path):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)


def fetch_items_page(page: int, per_page: int, tpl_id: int):
    """Fetch a single page of items plus total count from headers."""
    params = {"page": page, "per_page": per_page, "resource_template_id": tpl_id}
    resp = requests.get(ITEMS_URL, params={**params, **KEYS}, timeout=30)
    resp.raise_for_status()
    items = resp.json()
    total = int(resp.headers.get("Omeka-S-Total-Results") or 0)
    return items, total


def parse_omeka_modified(item):
    """Extract o:modified (or o:created) from an Omeka item as a Unix timestamp."""
    raw = ""
    for field in ("o:modified", "o:created"):
        val = item.get(field)
        if isinstance(val, dict):
            raw = val.get("@value", "")
        elif isinstance(val, str):
            raw = val
        if raw:
            break
    if not raw:
        return 0  # unknown → always re-ingest
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return 0


def load_qdrant_timestamps():
    """Scroll Qdrant and return {point_id: updated_at} for all points."""
    client = QdrantClient(url=QDRANT_URL)
    result = {}
    offset = None
    batch_size = 250

    while True:
        points, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=batch_size,
            offset=offset,
            with_payload=["updated_at"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            result[point.id] = payload.get("updated_at", 0)

        if next_offset is None:
            break
        offset = next_offset

    return result


def classify_items(omeka_items, qdrant_timestamps):
    """Split items into (new, updated, up_to_date) based on Qdrant state."""
    new_items = []
    updated_items = []
    up_to_date_items = []

    for item in omeka_items:
        item_id = item["o:id"]
        qdrant_ts = qdrant_timestamps.get(item_id)

        if qdrant_ts is None:
            new_items.append(item)
        else:
            omeka_modified = parse_omeka_modified(item)
            if omeka_modified > qdrant_ts:
                updated_items.append(item)
            else:
                up_to_date_items.append(item)

    return new_items, updated_items, up_to_date_items


def process_item(item):
    if not item.get("o:media"):
        print(f"Skip item {item['o:id']} (no media)")
        return

    media_link = item["o:media"][0]["@id"]
    media = get_json(media_link)
    thumb_urls = media.get("o:thumbnail_urls", {})
    # Prefer largest available thumbnail to preserve embedding quality.
    img_url = thumb_urls.get("large") or media.get("o:original_url") or thumb_urls.get("medium")
    if not img_url:
        print(f"Skip item {item['o:id']} (no original image url)")
        return

    # Download image to temp file
    with tempfile.NamedTemporaryFile(dir=MEDIA_CACHE_DIR, suffix=".jpg", delete=False) as tmp:
        download_image(img_url, Path(tmp.name))
        image_path = Path(tmp.name)

    title = item.get("o:title", "")
    identifier = first_value(item, "dcterms:identifier")
    desc = "\n".join(list_values(item, "dcterms:description"))
    subjects = list_values(item, "dcterms:subject")
    year = first_year(item)
    omeka_url = f"{BASE}/s/main/item/{item['o:id']}"

    # Log required fields
    id_part = f" | Identifier={identifier}" if identifier else ""
    print(f"Ingesting ItemID={item['o:id']} | Title={title}{id_part} | Image={img_url}")

    embed_and_upsert(
        image_path=image_path,
        omeka_item_id=item["o:id"],
        title=title,
        omeka_description=desc,
        collection="omeka",
        year=year,
        subjects=subjects,
        curator_notes=[],
        dominant_color="unknown",
        omeka_url=omeka_url,
        thumb_url=img_url,
    )

    try:
        image_path.unlink(missing_ok=True)
    except Exception:
        pass


def process_item_safe(item):
    try:
        process_item(item)
        return True
    except Exception as e:
        print(f"Error ingesting item {item.get('o:id')}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Ingest Omeka items into Qdrant.")
    parser.add_argument("--force", action="store_true", help="Full re-ingest, ignoring Qdrant state.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be ingested without doing it.")
    args = parser.parse_args()

    tpl_id = find_template_id_by_title(TARGET_TEMPLATE_TITLE)
    if tpl_id is None:
        print(f"Template '{TARGET_TEMPLATE_TITLE}' not found. Exiting.")
        return
    print(f"Using resource_template_id={tpl_id} for title '{TARGET_TEMPLATE_TITLE}'")

    # Phase 1: Fetch all Omeka items
    print("Fetching items from Omeka API...")
    all_items = []
    page = 1
    per_page = 100
    total_expected = None
    while True:
        items, header_total = fetch_items_page(page, per_page, tpl_id)
        if total_expected is None:
            total_expected = header_total
        if not items:
            break
        all_items.extend(items)
        print(f"  Page {page}: {len(all_items)}/{total_expected} items")
        page += 1
    print(f"Fetched {len(all_items)} items from Omeka")

    # Phase 2: Determine what needs processing
    if args.force:
        print("Force mode: will re-ingest all items")
        items_to_process = all_items
        skipped_count = 0
    else:
        print("Loading Qdrant state for incremental comparison...")
        try:
            qdrant_timestamps = load_qdrant_timestamps()
            print(f"Found {len(qdrant_timestamps)} existing points in Qdrant")
        except Exception as e:
            print(f"Warning: could not load Qdrant state ({e}). Falling back to full ingest.")
            qdrant_timestamps = {}

        new_items, updated_items, up_to_date = classify_items(all_items, qdrant_timestamps)
        items_to_process = new_items + updated_items
        skipped_count = len(up_to_date)

        print(f"Incremental ingest: {len(new_items)} new, {len(updated_items)} updated, {skipped_count} up-to-date (skipped)")

    if args.dry_run:
        print("\n--- DRY RUN ---")
        for item in items_to_process:
            title = item.get("o:title", "(untitled)")
            print(f"  Would ingest: ItemID={item['o:id']} | {title}")
        print(f"Total: {len(items_to_process)} items would be processed, {skipped_count} skipped")
        return

    if not items_to_process:
        print("Nothing to ingest. All items are up-to-date.")
        return

    # Phase 3: Process items
    ingested = 0
    futures = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for item in items_to_process:
            futures.append(pool.submit(process_item_safe, item))

        for idx, fut in enumerate(as_completed(futures), 1):
            ok = fut.result()
            if ok:
                ingested += 1
            if idx % 25 == 0 or idx == len(futures):
                print(f"Progress: {idx}/{len(futures)} processed; successes={ingested}")

    print(f"Done. Processed {len(items_to_process)} items ({ingested} succeeded), skipped {skipped_count} up-to-date items")


if __name__ == "__main__":
    main()

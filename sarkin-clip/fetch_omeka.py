"""
Fetch Omeka-S items and ingest each into Qdrant via embed_image_to_qdrant.embed_and_upsert.

Usage:
  ANTHROPIC_API_KEY=... python fetch_omeka.py

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
"""
import json
import os
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

import requests

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
ENV_FORCE_OCR = os.getenv("FORCE_OCR", "1") == "1"

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


def process_item(item, force_ocr=False):
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
        force_ocr=force_ocr,
    )

    try:
        image_path.unlink(missing_ok=True)
    except Exception:
        pass


def process_item_safe(item, force_ocr=False):
    try:
        process_item(item, force_ocr=force_ocr)
        return True
    except Exception as e:
        print(f"Error ingesting item {item.get('o:id')}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Ingest Omeka items into Qdrant.")
    parser.add_argument("--force-ocr", action="store_true", help="Re-run OCR even if cached.")
    args = parser.parse_args()
    force_ocr = args.force_ocr or ENV_FORCE_OCR

    tpl_id = find_template_id_by_title(TARGET_TEMPLATE_TITLE)
    if tpl_id is None:
        print(f"Template '{TARGET_TEMPLATE_TITLE}' not found. Exiting.")
        return
    print(f"Using resource_template_id={tpl_id} for title '{TARGET_TEMPLATE_TITLE}'")

    page = 1
    per_page = 100
    total = ingested = 0
    total_expected = None
    futures = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        while True:
            items, header_total = fetch_items_page(page, per_page, tpl_id)
            if total_expected is None:
                total_expected = header_total
            if not items:
                break
            print(f"Page {page}: got {len(items)} items (expected total {total_expected})")
            for it in items:
                total += 1
                futures.append(pool.submit(process_item_safe, it, force_ocr))
            page += 1

        for idx, fut in enumerate(as_completed(futures), 1):
            ok = fut.result()
            if ok:
                ingested += 1
            if idx % 25 == 0 or idx == len(futures):
                print(f"Progress: {idx}/{len(futures)} processed; successes={ingested}")

    print(f"Done. Total items seen (filtered): {total}, attempted ingest: {ingested}, expected {total_expected}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Export Omeka item set to Shopify-compatible CSV.

Re-runnable / idempotent: existing Shopify prices are preserved by fetching
them from the Shopify Admin API before writing the CSV. Shopify CSV import
matches existing products by handle and updates their fields.

METAFIELDS NOTE: Shopify silently ignores metafield columns on CSV import.
After import, set metafields via the MCP workflow (see session history).

Requires SHOPIFY_ADMIN_TOKEN in shopify/.env for price preservation.
Create one at: Shopify Admin > Settings > Apps > Develop apps.
"""

import csv
import json
import os
import re
import urllib.request
import urllib.error

API_BASE = "https://catalog.jonsarkin.com/api"
SHOPIFY_STORE = "jonsarkin.myshopify.com"
ITEM_SET_ID = 9841
SITE_SLUG = "catalog"
OUTPUT = "exports/shopify-selects.csv"
CONTENT_EXCERPT_MAX = 200


def api_get(path):
    url = f"{API_BASE}/{path}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def get_values(item, prop):
    """Extract all @value entries for a property."""
    vals = item.get(prop, [])
    if not isinstance(vals, list):
        vals = [vals]
    return [v["@value"] for v in vals if isinstance(v, dict) and "@value" in v]


def get_value(item, prop):
    vals = get_values(item, prop)
    return vals[0] if vals else ""


def slugify(title):
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def truncate(text, max_chars):
    """Trim to max_chars at a word boundary."""
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rsplit(None, 1)[0]
    return trimmed + "…"


def load_shopify_token():
    """Load SHOPIFY_ADMIN_TOKEN from shopify/.env."""
    env_path = os.path.join(os.path.dirname(__file__), "..", "shopify", ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("SHOPIFY_ADMIN_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return os.environ.get("SHOPIFY_ADMIN_TOKEN", "")


def fetch_shopify_prices(token):
    """Return dict of {sku: price_string} for all Shopify products."""
    if not token:
        print("  ⚠  No SHOPIFY_ADMIN_TOKEN — Variant Price will be blank for new products.")
        return {}

    prices = {}
    url = f"https://{SHOPIFY_STORE}/admin/api/2025-01/products.json?fields=variants&limit=250"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        for product in data.get("products", []):
            for variant in product.get("variants", []):
                sku = variant.get("sku", "")
                price = variant.get("price", "")
                if sku and price:
                    prices[sku] = price
        print(f"  Fetched prices for {len(prices)} Shopify variants.")
    except urllib.error.HTTPError as e:
        print(f"  ⚠  Shopify API error {e.code} — prices will be blank.")
    return prices


def get_original_url(item_id):
    media = api_get(f"media?item_id={item_id}")
    if media:
        return media[0].get("o:original_url", "")
    return ""


def main():
    token = load_shopify_token()
    shopify_prices = fetch_shopify_prices(token)

    items = api_get(f"items?item_set_id={ITEM_SET_ID}&per_page=100")
    print(f"Fetched {len(items)} items from Omeka set {ITEM_SET_ID}")

    headers = [
        "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags",
        "Published", "Image Src", "Image Alt Text",
        "Variant SKU", "Variant Grams", "Variant Inventory Tracker",
        "Variant Inventory Qty", "Variant Inventory Policy",
        "Variant Price", "Variant Requires Shipping", "Status",
        # Metafields (applied via MCP after import — Shopify ignores these on CSV import)
        "Metafield: artwork.catalog_number [single_line_text_field]",
        "Metafield: artwork.catalog_url [url]",
        "Metafield: artwork.medium [single_line_text_field]",
        "Metafield: artwork.dimensions [single_line_text_field]",
        "Metafield: artwork.year [single_line_text_field]",
        "Metafield: artwork.original_image_url [url]",
        "Metafield: artwork.signed [single_line_text_field]",
        "Metafield: artwork.condition [single_line_text_field]",
        "Metafield: artwork.provenance [single_line_text_field]",
        "Metafield: artwork.content_excerpt [single_line_text_field]",
    ]

    rows = []
    for item in items:
        item_id = item["o:id"]
        title = get_value(item, "dcterms:title") or item.get("o:title", "")
        identifier = get_value(item, "dcterms:identifier")
        item_type = get_value(item, "dcterms:type")
        medium = get_value(item, "dcterms:medium")
        height = get_value(item, "schema:height")
        width = get_value(item, "schema:width")
        date = get_value(item, "dcterms:date")
        subjects = get_values(item, "dcterms:subject")
        signed = get_value(item, "schema:distinguishingSign")
        condition = get_value(item, "schema:itemCondition")
        provenance = get_value(item, "dcterms:provenance")
        content = get_value(item, "bibo:content")

        dimensions = ""
        if height and width:
            dimensions = f'{height}" x {width}"'

        content_excerpt = truncate(content, CONTENT_EXCERPT_MAX).replace("\n", " ") if content else ""

        image_url = get_original_url(item_id)
        catalog_url = f"https://catalog.jonsarkin.com/s/{SITE_SLUG}/item/{item_id}"
        price = shopify_prices.get(identifier, "")

        print(f"  {identifier}: ${price or '—':>8}  {title}")

        rows.append({
            "Handle": slugify(identifier),
            "Title": identifier,
            "Body (HTML)": "",
            "Vendor": "Jon Sarkin",
            "Type": item_type,
            "Tags": ", ".join(subjects),
            "Published": "TRUE",
            "Image Src": image_url,
            "Image Alt Text": title,
            "Variant SKU": identifier,
            "Variant Grams": "0",
            "Variant Inventory Tracker": "",
            "Variant Inventory Qty": "1",
            "Variant Inventory Policy": "deny",
            "Variant Price": price,
            "Variant Requires Shipping": "TRUE",
            "Status": "draft",
            "Metafield: artwork.catalog_number [single_line_text_field]": identifier,
            "Metafield: artwork.catalog_url [url]": catalog_url,
            "Metafield: artwork.medium [single_line_text_field]": medium,
            "Metafield: artwork.dimensions [single_line_text_field]": dimensions,
            "Metafield: artwork.year [single_line_text_field]": date,
            "Metafield: artwork.original_image_url [url]": image_url,
            "Metafield: artwork.signed [single_line_text_field]": signed,
            "Metafield: artwork.condition [single_line_text_field]": condition,
            "Metafield: artwork.provenance [single_line_text_field]": provenance,
            "Metafield: artwork.content_excerpt [single_line_text_field]": content_excerpt,
        })

    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} products to {OUTPUT}")


if __name__ == "__main__":
    main()

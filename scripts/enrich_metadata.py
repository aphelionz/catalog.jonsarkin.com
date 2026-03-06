#!/usr/bin/env python3
"""
enrich_metadata.py — Automated OCR + metadata enrichment for Jon Sarkin catalog items.

Sends artwork images to Claude for structured analysis, then writes enriched
metadata back to Omeka S via API. Supports both real-time and Batch API modes.

CRITICAL: Omeka S PATCH replaces the ENTIRE property set. This script always
reads all existing properties before writing, merging new values into the
existing set to prevent data loss.

Usage:
  # ── Real-time (one at a time) ──
  python scripts/enrich_metadata.py --item-id 6886
  python scripts/enrich_metadata.py --dry-run --limit 10

  # ── Batch API (50% cheaper, ~1 hour turnaround) ──
  python scripts/enrich_metadata.py --batch --limit 100      # Submit batch
  python scripts/enrich_metadata.py --batch-status            # Check progress
  python scripts/enrich_metadata.py --batch-collect           # Collect & apply
  python scripts/enrich_metadata.py --batch-collect --dry-run # Preview only

  # ── Model selection ──
  python scripts/enrich_metadata.py --model haiku   --batch   # Cheapest ($7 total)
  python scripts/enrich_metadata.py --model sonnet  --batch   # Balanced ($21 total)
  python scripts/enrich_metadata.py --model opus    --batch   # Best ($42 total)

Environment variables:
  ANTHROPIC_API_KEY    — Required. Claude API key.
  OMEKA_BASE_URL       — Omeka S base URL (default: http://localhost:8888)
  OMEKA_KEY_IDENTITY   — API key identity (default: catalog_api)
  OMEKA_KEY_CREDENTIAL — API key credential (default: sarkin2024)
"""
from __future__ import annotations

# Force unbuffered stdout so print() output appears immediately,
# even when running under make or piped through other processes.
import sys
sys.stdout.reconfigure(line_buffering=True)

import argparse
import base64
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anthropic
import requests
from PIL import Image

# ── Configuration ──────────────────────────────────────────────────────────

OMEKA_BASE = os.getenv("OMEKA_BASE_URL", "http://localhost:8888")
OMEKA_KEY_ID = os.getenv("OMEKA_KEY_IDENTITY", "catalog_api")
OMEKA_KEY_CRED = os.getenv("OMEKA_KEY_CREDENTIAL", "sarkin2024")

MODEL_ALIASES = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-5-20250929",
    "opus":   "claude-opus-4-5-20251101",
}
DEFAULT_MODEL_ALIAS = "sonnet"

CACHE_PATH = Path("scripts/.enrich_cache.json")
BATCH_DIR = Path("scripts/.enrich_batches")

RESOURCE_TEMPLATE_ID = 2   # "Artwork (Jon Sarkin)"
CREATOR_ITEM_ID = 3        # Jon Sarkin Person item

# Batch API: 256 MB max request body. Each image at 1024px + JPEG q85 is
# ~150 KB raw → ~200 KB base64 → ~250 KB with JSON overhead per request.
# 500 items × 250 KB ≈ 125 MB per batch (safe margin under 256 MB limit).
BATCH_MAX_ITEMS = 500   # Items per batch (keeps payload well under 256 MB)
IMAGE_MAX_DIM = 1024    # Max dimension for batch mode (px)
IMAGE_QUALITY = 85      # JPEG quality for batch mode
DOWNLOAD_WORKERS = 20   # Parallel image downloads for batch mode
APPLY_WORKERS = 10      # Parallel PATCH requests for apply-cache

# ── Property IDs (from Omeka S) ───────────────────────────────────────────

PROP = {
    "dcterms:title":                  1,
    "dcterms:identifier":            10,
    "dcterms:date":                   7,
    "dcterms:type":                   8,
    "dcterms:medium":                26,
    "dcterms:format":                 9,
    "dcterms:description":            4,
    "dcterms:subject":                3,
    "dcterms:rights":                15,
    "dcterms:provenance":            51,
    "dcterms:spatial":               40,
    "dcterms:bibliographicCitation": 48,
    "schema:artworkSurface":        931,
    "schema:height":                603,
    "schema:width":                1129,
    "schema:distinguishingSign":    476,
    "schema:itemCondition":        1579,
    "schema:creditText":           1343,
    "schema:creator":               921,
    "schema:box":                  1424,
    "bibo:owner":                    72,
    "bibo:annotates":                57,
    "bibo:content":                  91,
    "bibo:presentedAt":              74,
    "curation:note":               1710,
}

# ── Controlled vocabularies ───────────────────────────────────────────────

WORK_TYPES = [
    "Drawing", "Painting", "Collage", "Mixed Media",
    "Sculpture", "Print", "Other",
]

SUPPORTS = [
    "Paper", "Cardboard", "Cardboard album sleeve", "Canvas", "Board", "Wood",
    "Found Object", "Envelope", "Album Sleeve", "Other",
]

MOTIFS = [
    "Eyes", "Fish", "Faces", "Hands", "Text Fragments",
    "Grids", "Circles", "Patterns", "Animals", "Names/Words",
    "Maps", "Numbers",
]

CONDITIONS = ["Excellent", "Good", "Fair", "Poor", "Not Examined"]

# ── Claude prompt ─────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """You are cataloging artworks by Jon Sarkin (1953–2024) for a catalog raisonné.

Analyze this artwork image and return a JSON object with the following fields.
Be precise and conservative — only report what you can clearly see.

{
  "transcription": "Complete transcription of ALL visible text in the artwork.
                     Preserve line breaks, capitalization, and punctuation.
                     Include title text, marginal text, labels — everything
                     legible. For repeated words/phrases, transcribe once then
                     note the count (e.g. 'AUM ×47'). Do NOT write out every
                     repetition. Omit text you cannot read clearly.
                     Return null if no text is visible.",

  "signature": "Describe the signature using this format:
                - Arrow indicating position on the artwork + initials/date
                - Arrows: ↖ ↑ ↗ ← → ↙ ↓ ↘ (e.g. '↘ JMS 17' means signed
                  lower-right with initials JMS and year 17)
                - Use '∅' if unsigned or no signature visible.
                - Common Sarkin signatures: JS, JMS, Jon Sarkin, sarkin
                - If a date appears in the signature, include it (e.g. '↘ JS 05')",

  "date": "Year the work was created, if determinable from the signature or
           text in the artwork. Return as a string: '2005', 'c. 2005', etc.
           Return null if not determinable.",

  "medium": "Materials/media used, described in standard art catalog format.
             Examples: 'Marker on paper', 'Ink and marker on paper',
             'Acrylic and collage on cardboard', 'Mixed media on album sleeve'.
             Return null if uncertain.",

  "support": "The surface/substrate. Must be one of: Paper, Cardboard, Canvas,
              Board, Wood, Found Object, Envelope, Album Sleeve, Other.
              Return null if uncertain.",

  "work_type": "Must be one of: Drawing, Painting, Collage, Mixed Media,
                Sculpture, Print, Other. Return null if uncertain.",

  "motifs": ["Array of visual motifs present. Choose from: Eyes, Fish, Faces,
              Hands, Text Fragments, Grids, Circles, Patterns, Animals,
              Names/Words, Maps, Numbers. Only include motifs clearly present.
              Return empty array if none match."],

  "condition_notes": "Brief note on visible condition issues (tears, staining,
                      foxing, fading). Return null if the work appears to be
                      in good condition or if condition cannot be assessed
                      from the image."
}

Return ONLY valid JSON. No markdown fences, no explanation."""

ANALYSIS_PROMPT_VERSION = 2  # Bump to invalidate cache


# ── Image helpers ─────────────────────────────────────────────────────────

def download_and_encode_image(url: str, max_dim: int = 0) -> Tuple[str, str]:
    """Download image, optionally resize, return (base64_data, media_type)."""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "image/jpeg")
    if "png" in content_type:
        media_type = "image/png"
    elif "gif" in content_type:
        media_type = "image/gif"
    elif "webp" in content_type:
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"

    if max_dim > 0:
        # Resize to keep batch payload small
        img = Image.open(io.BytesIO(resp.content))
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=IMAGE_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return b64, "image/jpeg"
    else:
        b64 = base64.b64encode(resp.content).decode("utf-8")
        return b64, media_type


# ── Omeka S API helpers ───────────────────────────────────────────────────

_session = requests.Session()


def omeka_auth() -> dict:
    return {"key_identity": OMEKA_KEY_ID, "key_credential": OMEKA_KEY_CRED}


def omeka_get(endpoint: str, params: dict = None) -> Any:
    url = f"{OMEKA_BASE}/api/{endpoint}"
    p = {**(params or {}), **omeka_auth()}
    resp = _session.get(url, params=p, timeout=30)
    resp.raise_for_status()
    return resp.json(), resp.headers


def omeka_patch(item_id: int, payload: dict) -> dict:
    url = f"{OMEKA_BASE}/api/items/{item_id}"
    resp = _session.patch(
        url,
        params=omeka_auth(),
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_item(item_id: int) -> dict:
    data, _ = omeka_get(f"items/{item_id}")
    return data


def get_items_page(page: int, per_page: int = 500) -> Tuple[list, int]:
    """Fetch a page of items filtered by resource template."""
    data, headers = omeka_get("items", {
        "resource_template_id": RESOURCE_TEMPLATE_ID,
        "page": page,
        "per_page": per_page,
    })
    total = int(headers.get("Omeka-S-Total-Results", 0))
    return data, total


def get_primary_media_url(item: dict) -> Optional[str]:
    """Get the original image URL for the item's primary media."""
    media_list = item.get("o:media", [])
    if not media_list:
        return None

    media_ref = media_list[0]
    media_id = media_ref.get("o:id")
    if not media_id:
        media_link = media_ref.get("@id")
        if not media_link:
            return None
        media_data = requests.get(media_link, params=omeka_auth(), timeout=30).json()
    else:
        media_data, _ = omeka_get(f"media/{media_id}")

    return media_data.get("o:original_url")


def extract_value(item: dict, term: str) -> str:
    """Extract first value for a property term from an Omeka item."""
    vals = item.get(term, [])
    if not vals:
        return ""
    v = vals[0]
    return v.get("@value", "") or v.get("o:label", "") or ""


def extract_all_values(item: dict, term: str) -> list:
    """Extract all values for a repeatable property."""
    vals = item.get(term, [])
    return [v.get("@value", "") or v.get("o:label", "") for v in vals if v]


# ── Property builder (for PATCH payloads) ─────────────────────────────────

def literal_value(property_id: int, value: str) -> dict:
    return {"type": "literal", "property_id": property_id, "@value": value}


def resource_value(property_id: int, resource_id: int) -> dict:
    return {"type": "resource:item", "property_id": property_id, "value_resource_id": resource_id}


def _clean_value(v: dict) -> dict:
    """Strip read-only fields from a property value for PATCH.

    Omeka S API responses include fields like @id, @type, property_label,
    is_public etc. that can cause 422 errors when sent back in a PATCH.
    Keep only the fields Omeka S accepts on write.
    """
    # Write-safe keys for Omeka S property values
    WRITE_KEYS = {"type", "property_id", "@value", "@id", "@language",
                  "o:label", "value_resource_id", "uri", "o:is_public"}
    return {k: v[k] for k in WRITE_KEYS if k in v}


SIGNATURE_ARROWS = set("↖↑↗←→↙↓↘∅")


def _parse_signature(raw: str) -> tuple[str, str | None]:
    """Parse signature like '↘ JMS 17' → ('↘', '2017').

    Returns (arrow_char, year_str_or_None).  The arrow is a single Unicode
    character indicating position on the artwork.  The year (if present) is
    normalised to four digits.
    """
    if not raw:
        return ("∅", None)
    raw = raw.strip()
    # First character should be a directional arrow or ∅
    arrow = raw[0] if raw and raw[0] in SIGNATURE_ARROWS else "∅"
    # Extract year: prefer 4-digit, fall back to 2-digit
    year = None
    m = re.search(r'\b((?:19|20)\d{2})\b', raw)
    if m:
        year = m.group(1)
    else:
        m = re.search(r'\b(\d{2})\b', raw)
        if m:
            yy = int(m.group(1))
            year = f"20{m.group(1)}" if yy < 50 else f"19{m.group(1)}"
    return (arrow, year)


def build_patch_payload(item: dict, enrichment: dict) -> dict:
    """
    Build a PATCH payload that merges enrichment into existing item data.

    CRITICAL: Omeka S PATCH replaces ALL properties. We must include every
    existing property in the payload, only overwriting fields that the
    enrichment provides AND that are currently empty.
    """
    payload = {}

    for key, val in item.items():
        if ":" in key and isinstance(val, list):
            payload[key] = [_clean_value(v) for v in val if isinstance(v, dict)]

    # Include system keys EXCEPT o:resource_template — sending it triggers
    # template validation (e.g. requiring Catalog Number) which fails on
    # items missing required fields. Omitting it preserves the existing
    # template without re-validating.
    for sys_key in ["o:resource_class", "o:item_set", "o:media", "o:is_public"]:
        if sys_key in item:
            payload[sys_key] = item[sys_key]

    def set_if_empty(term, value):
        if not value:
            return
        existing = payload.get(term, [])
        for v in existing:
            if v.get("@value", "").strip():
                return
        payload[term] = [literal_value(PROP[term], value)]

    def set_repeatable_if_empty(term, values):
        if not values:
            return
        existing = payload.get(term, [])
        if existing:
            return
        payload[term] = [literal_value(PROP[term], v) for v in values]

    # Ensure dcterms:identifier exists (required by template).
    # Generate a temporary catalog number if missing, using the enrichment
    # date when available. Format: JS-{year}-T{item_id} (T = temporary).
    has_identifier = any(
        v.get("@value", "").strip() for v in payload.get("dcterms:identifier", [])
    )
    if not has_identifier:
        item_id = item["o:id"]
        year = enrichment.get("date", "")[:4] if enrichment.get("date") else "0000"
        if not year.isdigit():
            year = "0000"
        temp_id = f"JS-{year}-T{item_id}"
        payload["dcterms:identifier"] = [literal_value(PROP["dcterms:identifier"], temp_id)]

    set_if_empty("bibo:content", enrichment.get("transcription"))

    # Signature → always store as single arrow character; year feeds into date
    sig_arrow, sig_year = _parse_signature(enrichment.get("signature", ""))
    payload["schema:distinguishingSign"] = [literal_value(PROP["schema:distinguishingSign"], sig_arrow)]

    # Date: force-set from enrichment or signature year
    date_val = enrichment.get("date")
    if date_val and "T" not in date_val and ":" not in date_val:
        payload["dcterms:date"] = [literal_value(PROP["dcterms:date"], date_val)]
    elif sig_year:
        payload["dcterms:date"] = [literal_value(PROP["dcterms:date"], sig_year)]

    set_if_empty("dcterms:medium", enrichment.get("medium"))

    # Hardcoded defaults — always overwrite (not set_if_empty)
    payload["schema:artworkSurface"] = [literal_value(PROP["schema:artworkSurface"], "Album Sleeve")]
    payload["schema:height"] = [literal_value(PROP["schema:height"], "12.5")]
    payload["schema:width"] = [literal_value(PROP["schema:width"], "12.5")]

    work_type = enrichment.get("work_type")
    if work_type and work_type in WORK_TYPES:
        set_if_empty("dcterms:type", work_type)

    motifs = enrichment.get("motifs", [])
    valid_motifs = [m for m in motifs if m in MOTIFS]
    set_repeatable_if_empty("dcterms:subject", valid_motifs)

    has_creator = any(
        v.get("value_resource_id") == CREATOR_ITEM_ID
        for v in payload.get("schema:creator", [])
    )
    if not has_creator:
        payload["schema:creator"] = [resource_value(PROP["schema:creator"], CREATOR_ITEM_ID)]

    payload["bibo:owner"] = [literal_value(PROP["bibo:owner"], "The Jon Sarkin Estate")]
    payload["dcterms:format"] = [literal_value(PROP["dcterms:format"], "∅")]

    return payload


# ── Claude analysis (real-time) ───────────────────────────────────────────

def analyze_artwork(image_url: str, model: str) -> dict:
    """Send artwork image to Claude for structured analysis (real-time)."""
    client = anthropic.Anthropic()
    b64_image, media_type = download_and_encode_image(image_url)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_image}},
                {"type": "text", "text": ANALYSIS_PROMPT},
            ],
        }],
    )
    return _parse_claude_response(response.content[0].text)


def _repair_truncated_json(text: str) -> Optional[dict]:
    """Attempt to repair JSON truncated by max_tokens.

    When Claude hits the token limit, the JSON is cut off mid-value.
    Strategy: close any open strings, arrays, and objects to make it parseable.
    We may lose the last field but salvage everything before it.
    """
    # Strip trailing incomplete escape sequences
    text = text.rstrip("\\")
    # Close any open string
    if text.count('"') % 2 == 1:
        text += '"'
    # Close open brackets/braces from inside out
    stack = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ("{", "["):
            stack.append("}" if ch == "{" else "]")
        elif ch in ("}", "]"):
            if stack:
                stack.pop()
    # Close everything that's still open
    text += "".join(reversed(stack))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_claude_response(raw_text: str) -> dict:
    """Parse Claude's JSON response, stripping markdown fences if present."""
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines)
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        # Attempt to salvage truncated JSON (from hitting max_tokens)
        repaired = _repair_truncated_json(raw_text)
        if repaired:
            print(f"  WARNING: Repaired truncated JSON (max_tokens likely hit)")
            return repaired
        print(f"  WARNING: Invalid JSON from Claude: {e}")
        print(f"  Raw: {raw_text[:300]}")
        return {}


# ── Batch API ─────────────────────────────────────────────────────────────

def batch_submit(candidates: list, model: str, force: bool) -> list:
    """
    Submit items to the Anthropic Batch API.

    Downloads and resizes images, builds batch requests in chunks of
    BATCH_MAX_ITEMS, submits each chunk, and saves batch metadata.

    Returns list of batch IDs.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    cache = load_cache()
    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    # Quick filter: skip cached items (no HTTP needed)
    to_process = []
    skipped_cached = 0

    for item in candidates:
        item_id = item["o:id"]
        cache_key = f"{item_id}:{ANALYSIS_PROMPT_VERSION}"
        if cache.get(cache_key) and not force:
            skipped_cached += 1
            continue
        # Only check that the item has media refs — don't resolve URLs yet
        if not item.get("o:media"):
            continue
        to_process.append(item)

    if skipped_cached:
        print(f"  Skipped {skipped_cached} items (already cached). Use --force to re-analyze.")

    # Resolve media URLs + download + resize images in parallel
    batch_requests = []
    download_errors = 0
    no_media = 0
    counter_lock = threading.Lock()
    counters = {"done": 0, "no_media": 0}

    def _download_one(item):
        """Resolve media URL, download, and resize. Returns (item_id, b64, media_type) or None."""
        item_id = item["o:id"]
        identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
        try:
            image_url = get_primary_media_url(item)
            if not image_url:
                with counter_lock:
                    counters["done"] += 1
                    counters["no_media"] += 1
                return None
            b64_image, media_type = download_and_encode_image(image_url, max_dim=IMAGE_MAX_DIM)
            with counter_lock:
                counters["done"] += 1
                n = counters["done"]
            print(f"  [{n}/{len(to_process)}] Downloaded {identifier}")
            return (item_id, b64_image, media_type)
        except Exception as e:
            with counter_lock:
                counters["done"] += 1
                n = counters["done"]
            print(f"  [{n}/{len(to_process)}] FAILED {identifier}: {e}")
            return None

    if to_process:
        workers = min(DOWNLOAD_WORKERS, len(to_process))
        print(f"\nDownloading {len(to_process)} images ({workers} parallel workers)...")
        t_start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_download_one, item): item for item in to_process}
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    download_errors += 1
                    continue
                item_id, b64_image, media_type = result
                batch_requests.append(Request(
                    custom_id=str(item_id),
                    params=MessageCreateParamsNonStreaming(
                        model=model,
                        max_tokens=4096,
                        messages=[{
                            "role": "user",
                            "content": [
                                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_image}},
                                {"type": "text", "text": ANALYSIS_PROMPT},
                            ],
                        }],
                    ),
                ))

        no_media = counters["no_media"]
        elapsed = time.perf_counter() - t_start
        print(f"  Done: {len(batch_requests)} downloaded, {download_errors - no_media} failed, {no_media} no media ({elapsed:.1f}s)")

    if not batch_requests:
        print("\nNo requests to submit.")
        return []

    # Submit in chunks
    batch_ids = []
    chunks = [batch_requests[i:i + BATCH_MAX_ITEMS]
              for i in range(0, len(batch_requests), BATCH_MAX_ITEMS)]

    # Track custom_ids per chunk index for metadata (extract before submit
    # since Request objects may be consumed/modified by the SDK)
    chunk_item_ids = []
    for chunk in chunks:
        ids = []
        for r in chunk:
            cid = getattr(r, "custom_id", None) or (r.get("custom_id") if isinstance(r, dict) else None)
            if cid is not None:
                ids.append(int(cid))
        chunk_item_ids.append(ids)

    for chunk_idx, chunk in enumerate(chunks):
        print(f"\nSubmitting batch {chunk_idx + 1}/{len(chunks)} ({len(chunk)} requests, model={model})...")
        try:
            batch = client.messages.batches.create(requests=chunk)
        except Exception as e:
            print(f"  ERROR submitting batch: {e}")
            continue

        batch_ids.append(batch.id)
        print(f"  Batch ID: {batch.id}")
        print(f"  Status:   {batch.processing_status}")

        # Save batch metadata
        meta = {
            "batch_id": batch.id,
            "model": model,
            "item_count": len(chunk),
            "item_ids": chunk_item_ids[chunk_idx],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": batch.processing_status,
        }
        meta_path = BATCH_DIR / f"{batch.id}.json"
        meta_path.write_text(json.dumps(meta, indent=2))

    print(f"\n{'='*60}")
    print(f"  Submitted {len(batch_ids)} batch(es)")
    print(f"  Total requests: {len(batch_requests)}")
    print(f"  Model: {model}")
    print(f"{'='*60}")
    print(f"\nRun this to check status:")
    print(f"  python scripts/enrich_metadata.py --batch-status")
    print(f"\nWhen complete, collect results with:")
    print(f"  python scripts/enrich_metadata.py --batch-collect")

    return batch_ids


def batch_status() -> None:
    """Check status of all pending batches."""
    client = anthropic.Anthropic()

    if not BATCH_DIR.exists():
        print("No batches found. Submit one with --batch first.")
        return

    meta_files = sorted(BATCH_DIR.glob("msgbatch_*.json"))
    if not meta_files:
        print("No batches found. Submit one with --batch first.")
        return

    print(f"{'='*60}")
    print(f"  Batch Status")
    print(f"{'='*60}")

    for meta_path in meta_files:
        meta = json.loads(meta_path.read_text())
        batch_id = meta["batch_id"]

        try:
            batch = client.messages.batches.retrieve(batch_id)
            counts = batch.request_counts
            status = batch.processing_status
            meta["status"] = status
            meta_path.write_text(json.dumps(meta, indent=2))

            print(f"\n  {batch_id}")
            print(f"    Model:      {meta.get('model', '?')}")
            print(f"    Status:     {status}")
            print(f"    Submitted:  {meta.get('created_at', '?')}")
            print(f"    Items:      {meta.get('item_count', '?')}")
            print(f"    Succeeded:  {counts.succeeded}")
            print(f"    Processing: {counts.processing}")
            print(f"    Errored:    {counts.errored}")
            print(f"    Expired:    {counts.expired}")
        except Exception as e:
            print(f"\n  {batch_id}: ERROR checking status — {e}")


def batch_collect(dry_run: bool, force: bool = False) -> None:
    """Collect results from completed batches, cache them, and apply to Omeka."""
    client = anthropic.Anthropic()
    cache = load_cache()

    if not BATCH_DIR.exists():
        print("No batches found.")
        return

    meta_files = sorted(BATCH_DIR.glob("msgbatch_*.json"))
    if not meta_files:
        print("No batches found.")
        return

    total_succeeded = 0
    total_applied = 0
    total_errors = 0

    for meta_path in meta_files:
        meta = json.loads(meta_path.read_text())
        batch_id = meta["batch_id"]

        # Check if already collected
        if meta.get("collected") and not force:
            print(f"\n  {batch_id}: Already collected. Skipping. (use --force to re-collect)")
            continue

        # Check status
        try:
            batch = client.messages.batches.retrieve(batch_id)
        except Exception as e:
            print(f"\n  {batch_id}: ERROR — {e}")
            continue

        if batch.processing_status != "ended":
            counts = batch.request_counts
            print(f"\n  {batch_id}: Still processing ({counts.processing} remaining). Skipping.")
            continue

        print(f"\n{'='*60}")
        print(f"  Collecting: {batch_id} ({meta.get('item_count', '?')} items)")
        print(f"{'='*60}")

        # Stream results
        succeeded = 0
        errors = 0

        for result in client.messages.batches.results(batch_id):
            item_id = int(result.custom_id)
            cache_key = f"{item_id}:{ANALYSIS_PROMPT_VERSION}"

            if result.result.type == "succeeded":
                raw_text = result.result.message.content[0].text
                enrichment = _parse_claude_response(raw_text)

                if enrichment:
                    cache[cache_key] = enrichment
                    succeeded += 1
                else:
                    errors += 1
                    print(f"  Item {item_id}: Failed to parse response")

            elif result.result.type == "errored":
                errors += 1
                err = result.result.error
                print(f"  Item {item_id}: Error — {err}")

            elif result.result.type == "expired":
                errors += 1
                print(f"  Item {item_id}: Expired (24h timeout)")

            elif result.result.type == "canceled":
                print(f"  Item {item_id}: Canceled")

        # Save cache with all results
        save_cache(cache)

        total_succeeded += succeeded
        total_errors += errors

        print(f"\n  Cached: {succeeded} results, {errors} errors")

        # Mark as collected
        meta["collected"] = True
        meta["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta["results_succeeded"] = succeeded
        meta["results_errors"] = errors
        meta_path.write_text(json.dumps(meta, indent=2))

    # Now apply cached results to Omeka
    if total_succeeded == 0:
        print("\nNo new results to apply.")
        return

    applied = apply_from_cache(cache, dry_run)
    total_applied += applied

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Results collected:  {total_succeeded}")
    print(f"  Errors:             {total_errors}")
    print(f"  Applied to Omeka:   {total_applied}")
    if dry_run:
        print(f"  (dry run — nothing was written)")


def _apply_one(item_id: int, enrichment: dict, dry_run: bool,
               print_lock: threading.Lock) -> Tuple[int, int]:
    """Worker: fetch item, diff, PATCH. Returns (applied, skipped)."""
    try:
        item = get_item(item_id)
    except Exception as e:
        with print_lock:
            print(f"  Item {item_id}: Could not fetch — {e}")
        return (0, 0)

    if not needs_enrichment(item):
        return (0, 1)

    with print_lock:
        changes = show_diff(item, enrichment, item_id)
    if changes == 0:
        return (0, 1)

    if dry_run:
        with print_lock:
            print(f"  (dry run — no changes written)")
        return (0, 0)

    payload = build_patch_payload(item, enrichment)
    try:
        omeka_patch(item_id, payload)
        identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
        with print_lock:
            print(f"  [{identifier}] Updated successfully")
        return (1, 0)
    except requests.HTTPError as e:
        with print_lock:
            print(f"  Item {item_id}: PATCH failed — {e}")
            if e.response is not None:
                print(f"    Response: {e.response.text[:500]}")
        return (0, 0)


def apply_from_cache(cache: dict | None = None, dry_run: bool = False) -> int:
    """Apply cached enrichment results to Omeka. No Claude API calls.

    Uses parallel workers for speed (~10x faster than sequential).
    Returns number of items successfully updated.
    """
    if cache is None:
        cache = load_cache()

    entries = [
        (key, enrichment)
        for key, enrichment in cache.items()
        if ":" in key and key.rsplit(":", 1)[1] == str(ANALYSIS_PROMPT_VERSION)
    ]

    if not entries:
        print("No cached results to apply.")
        return 0

    print(f"\n{'='*60}")
    print(f"  Applying up to {len(entries)} cached results to Omeka")
    if dry_run:
        print(f"  (DRY RUN — no changes will be written)")
    print(f"{'='*60}")

    applied = 0
    skipped = 0
    print_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=APPLY_WORKERS) as pool:
        futures = {}
        for key, enrichment in entries:
            item_id_str, _ = key.rsplit(":", 1)
            item_id = int(item_id_str)
            futures[pool.submit(_apply_one, item_id, enrichment,
                                dry_run, print_lock)] = item_id

        for future in as_completed(futures):
            a, s = future.result()
            applied += a
            skipped += s

    print(f"\n  Applied: {applied}, Skipped: {skipped}")
    return applied


# ── Cache ─────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


# ── Diff display ──────────────────────────────────────────────────────────

def show_diff(item: dict, enrichment: dict, item_id: int) -> int:
    """Print a human-readable diff of what would change. Returns change count."""
    identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"

    print(f"\n{'='*60}")
    print(f"  Item {item_id}: {identifier}")
    print(f"{'='*60}")

    fields = [
        ("Transcription", "bibo:content",               "transcription"),
        ("Medium",        "dcterms:medium",             "medium"),
        ("Work Type",     "dcterms:type",               "work_type"),
    ]

    changes = 0
    for label, term, key in fields:
        current = extract_value(item, term)
        proposed = enrichment.get(key)

        if not proposed:
            continue
        if current:
            continue

        print(f"  + {label}: {proposed}")
        changes += 1

    current_motifs = extract_all_values(item, "dcterms:subject")
    proposed_motifs = [m for m in enrichment.get("motifs", []) if m in MOTIFS]
    if proposed_motifs and not current_motifs:
        print(f"  + Motifs: {', '.join(proposed_motifs)}")
        changes += 1

    # Signature reformatting: old "↘ JMS 17" → "↘"
    cur_sig = extract_value(item, "schema:distinguishingSign") or ""
    if not (len(cur_sig) == 1 and cur_sig in SIGNATURE_ARROWS):
        sig_arrow, _ = _parse_signature(enrichment.get("signature", ""))
        print(f"  ~ Signature: {cur_sig!r} → {sig_arrow}")
        changes += 1

    # Date: enrichment date or signature year
    cur_date = extract_value(item, "dcterms:date") or ""
    _, sig_year_diff = _parse_signature(enrichment.get("signature", ""))
    effective_date = enrichment.get("date") or sig_year_diff
    if effective_date and cur_date != effective_date:
        if cur_date:
            print(f"  ~ Date: {cur_date} → {effective_date}")
        else:
            print(f"  + Date: {effective_date}")
        changes += 1

    # Hardcoded defaults
    if extract_value(item, "schema:artworkSurface") != "Album Sleeve":
        print(f"  + Support: Album Sleeve")
        changes += 1
    if not extract_value(item, "schema:height"):
        print(f"  + Dimensions: 12.5 × 12.5")
        changes += 1
    if not extract_value(item, "bibo:owner"):
        print(f"  + Owner: The Jon Sarkin Estate")
        changes += 1
    if not extract_value(item, "dcterms:format"):
        print(f"  + Framed: ∅")
        changes += 1

    if changes == 0:
        print(f"  (no changes needed)")

    return changes


# ── Item filtering ────────────────────────────────────────────────────────

def needs_enrichment(item: dict) -> bool:
    """Check if an item would benefit from enrichment."""
    if not item.get("o:media"):
        return False
    has_transcription = bool(extract_value(item, "bibo:content"))
    has_medium = bool(extract_value(item, "dcterms:medium"))
    has_motifs = bool(extract_all_values(item, "dcterms:subject"))
    # Signature must be a single arrow character (not old "↘ JMS 17" format)
    sig = extract_value(item, "schema:distinguishingSign") or ""
    has_clean_signature = len(sig) == 1 and sig in SIGNATURE_ARROWS
    # Hardcoded defaults must be present
    has_dimensions = bool(extract_value(item, "schema:height")) and bool(extract_value(item, "schema:width"))
    has_support = extract_value(item, "schema:artworkSurface") == "Album Sleeve"
    has_owner = bool(extract_value(item, "bibo:owner"))
    has_framed = bool(extract_value(item, "dcterms:format"))
    # Note: date is NOT checked here — needs_enrichment() can't see the
    # enrichment data to know if a date is available. Date is handled in
    # show_diff() and build_patch_payload() instead.
    return not (has_transcription and has_clean_signature and has_medium
                and has_motifs and has_dimensions and has_support
                and has_owner and has_framed)


# ── Candidate fetching ───────────────────────────────────────────────────

def fetch_candidates(limit: int = 0, skip_filter: bool = False) -> list:
    """Fetch items needing enrichment, with optional limit."""
    print(f"Fetching items from {OMEKA_BASE}...")
    print(f"Resource template: {RESOURCE_TEMPLATE_ID} (Artwork (Jon Sarkin))")

    candidates = []
    page = 1
    total_expected = None
    total_seen = 0
    target = limit or float("inf")

    while len(candidates) < target:
        items, total = get_items_page(page)
        if total_expected is None:
            total_expected = total
            print(f"Total items in catalog: {total}")
        if not items:
            break
        total_seen += len(items)

        for it in items:
            if len(candidates) >= target:
                break
            if skip_filter:
                if it.get("o:media"):
                    candidates.append(it)
            else:
                if needs_enrichment(it):
                    candidates.append(it)

        print(f"  Page {page}: scanned {total_seen}, {len(candidates)} candidates")
        page += 1

    label = "with media" if skip_filter else "need enrichment"
    print(f"\n{len(candidates)} items {label} (of {total_seen} scanned)")
    if limit:
        print(f"Limited to {limit} items")

    return candidates


# ── Real-time processing ─────────────────────────────────────────────────

def process_item(item_id: int, cache: dict, dry_run: bool, force: bool,
                 model: str) -> bool:
    """Process a single item in real-time. Returns True if changes were made."""
    item = get_item(item_id)
    identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"

    cache_key = f"{item_id}:{ANALYSIS_PROMPT_VERSION}"
    cached = cache.get(cache_key)

    if cached and not force:
        enrichment = cached
        print(f"  [{identifier}] Using cached analysis")
    else:
        image_url = get_primary_media_url(item)
        if not image_url:
            print(f"  [{identifier}] No media — skipping")
            return False

        print(f"  [{identifier}] Analyzing with Claude ({model})...")
        t_start = time.perf_counter()
        enrichment = analyze_artwork(image_url, model)
        t_elapsed = time.perf_counter() - t_start
        print(f"  [{identifier}] Analysis complete ({t_elapsed:.1f}s)")

        if not enrichment:
            return False

        cache[cache_key] = enrichment
        save_cache(cache)

    changes = show_diff(item, enrichment, item_id)
    if changes == 0:
        return False

    if dry_run:
        print(f"  (dry run — no changes written)")
        return False

    item = get_item(item_id)
    payload = build_patch_payload(item, enrichment)

    try:
        omeka_patch(item_id, payload)
        print(f"  [{identifier}] Updated successfully")
        return True
    except requests.HTTPError as e:
        print(f"  [{identifier}] PATCH failed: {e}")
        if e.response is not None:
            print(f"  Response: {e.response.text[:500]}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Enrich Jon Sarkin catalog items with Claude-powered analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Real-time (one item at a time)
  python scripts/enrich_metadata.py --item-id 6886
  python scripts/enrich_metadata.py --dry-run --limit 10

  # Batch API (50%% cheaper, ~1 hour turnaround)
  python scripts/enrich_metadata.py --batch --limit 100
  python scripts/enrich_metadata.py --batch-status
  python scripts/enrich_metadata.py --batch-collect
  python scripts/enrich_metadata.py --batch-collect --dry-run

  # Apply cached results after a DB wipe (no API key needed)
  python scripts/enrich_metadata.py --apply-cache
  python scripts/enrich_metadata.py --apply-cache --dry-run

  # Model selection
  python scripts/enrich_metadata.py --model haiku --batch
  python scripts/enrich_metadata.py --model opus  --item-id 6886
        """,
    )

    # Mode
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--item-id", type=int,
                      help="Process a single item by ID (real-time)")
    mode.add_argument("--batch", action="store_true",
                      help="Submit items to the Batch API (50%% discount)")
    mode.add_argument("--batch-status", action="store_true",
                      help="Check status of pending batches")
    mode.add_argument("--batch-collect", action="store_true",
                      help="Collect batch results and apply to Omeka")
    mode.add_argument("--apply-cache", action="store_true",
                      help="Apply cached results to Omeka (no Claude API call)")

    # Options
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to Omeka")
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum number of items to process (0 = all)")
    parser.add_argument("--force", action="store_true",
                        help="Re-analyze even if cached")
    parser.add_argument("--skip-filter", action="store_true",
                        help="Process all items, not just those needing enrichment")
    parser.add_argument("--model", choices=["haiku", "sonnet", "opus"],
                        default=DEFAULT_MODEL_ALIAS,
                        help="Claude model to use (default: sonnet)")

    args = parser.parse_args()
    model = MODEL_ALIASES[args.model]

    # ── Apply from cache (no API key needed) ──
    if args.apply_cache:
        apply_from_cache(dry_run=args.dry_run)
        return

    # Verify API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY in environment.")
        sys.exit(1)

    # ── Batch status ──
    if args.batch_status:
        batch_status()
        return

    # ── Batch collect ──
    if args.batch_collect:
        batch_collect(args.dry_run, force=args.force)
        return

    # ── Batch submit ──
    if args.batch:
        candidates = fetch_candidates(args.limit, args.skip_filter)
        if not candidates:
            print("No items to process.")
            return
        batch_submit(candidates, model, args.force)
        return

    # ── Real-time: single item ──
    if args.item_id:
        cache = load_cache()
        print(f"Processing item {args.item_id} ({args.model})...")
        success = process_item(args.item_id, cache, args.dry_run, args.force, model)
        print(f"\nDone. {'Updated' if success else 'No changes'}.")
        return

    # ── Real-time: batch of items ──
    candidates = fetch_candidates(args.limit, args.skip_filter)
    if not candidates:
        print("No items to process.")
        return

    cache = load_cache()
    updated = errors = skipped = 0

    for i, item in enumerate(candidates, 1):
        item_id = item["o:id"]
        identifier = extract_value(item, "dcterms:identifier") or f"item-{item_id}"
        print(f"\n[{i}/{len(candidates)}] {identifier}")

        try:
            if process_item(item_id, cache, args.dry_run, args.force, model):
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

        if i < len(candidates) and not cache.get(f"{item_id}:{ANALYSIS_PROMPT_VERSION}"):
            time.sleep(1)

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Total processed: {len(candidates)}")
    print(f"  Updated:         {updated}")
    print(f"  Skipped:         {skipped}")
    print(f"  Errors:          {errors}")
    if args.dry_run:
        print(f"  (dry run — nothing was written)")


if __name__ == "__main__":
    main()

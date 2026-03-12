"""Claude-based artwork enrichment for the catalog raisonné.

Ported from scripts/enrich_metadata.py — prompt, controlled vocabularies,
image handling, and response parsing.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Optional

import anthropic
import httpx
from PIL import Image

logger = logging.getLogger(__name__)

# ── Controlled vocabularies ──────────────────────────────────────────────────

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

# ── Claude prompt ────────────────────────────────────────────────────────────

ANALYSIS_PROMPT_TEMPLATE = """You are cataloging artworks by Jon Sarkin (1953–2024) for a catalog raisonné.

Analyze this artwork image and return a JSON object with the following fields.
Be precise and conservative — only report what you can clearly see.

{{
  "transcription": "Complete transcription of ALL visible text in the artwork.
                     Preserve line breaks, capitalization, and punctuation.
                     Include title text, marginal text, labels — everything
                     legible. For repeated words/phrases, transcribe once then
                     note the count (e.g. 'AUM ×47'). Do NOT write out every
                     repetition. Omit text you cannot read clearly.
                     Return null if no text is visible.",

  "signature": "Return a SINGLE character indicating where the signature
                appears on the artwork. Must be exactly one of:
                ↖ ↑ ↗ ← → ↙ ↓ ↘ ∅
                Use ∅ if unsigned or no signature visible.
                Do NOT include initials, dates, or any other text — just
                the one arrow character or ∅.",

  "date": "Year the work was created, if determinable from the signature or
           text in the artwork. Return as a string: '2005', 'c. 2005', etc.
           Return null if not determinable.",

  "medium": "Materials/media ONLY — do NOT include the support surface.
             Examples: 'Marker', 'Ink and marker', 'Acrylic and collage',
             'Mixed media', 'Graphite', 'Oil paint'.
             The support (paper, cardboard, etc.) is captured separately.
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
}}
{field_guidance}
Return ONLY valid JSON. No markdown fences, no explanation."""


def build_prompt(field_guidance: dict[str, str] | None = None) -> str:
    """Build the analysis prompt, optionally injecting per-field guidance."""
    guidance_lines = ""
    if field_guidance:
        parts = []
        for field, text in field_guidance.items():
            if text:
                parts.append(f"  {field}: {text}")
        if parts:
            guidance_lines = "\nAdditional cataloguer guidance:\n" + "\n".join(parts) + "\n"
    return ANALYSIS_PROMPT_TEMPLATE.format(field_guidance=guidance_lines)

IMAGE_MAX_DIM = 1024
IMAGE_QUALITY = 85

MODEL_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}


# ── Image helpers ────────────────────────────────────────────────────────────

async def fetch_and_encode_image(image_url: str) -> tuple[str, str]:
    """Download image, resize to max 1024px, return (base64_data, media_type)."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()

    content_type = resp.headers.get("content-type", "image/jpeg")
    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    w, h = img.size
    if max(w, h) > IMAGE_MAX_DIM:
        scale = IMAGE_MAX_DIM / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=IMAGE_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return b64, "image/jpeg"


# ── Response parsing ─────────────────────────────────────────────────────────

def _repair_truncated_json(text: str) -> Optional[dict]:
    """Attempt to repair JSON truncated by max_tokens."""
    text = text.rstrip("\\")
    if text.count('"') % 2 == 1:
        text += '"'
    stack: list[str] = []
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
        elif ch in ("}", "]") and stack:
            stack.pop()
    text += "".join(reversed(stack))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_claude_response(raw_text: str) -> dict:
    """Parse Claude's JSON response, stripping markdown fences if present."""
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines)
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        repaired = _repair_truncated_json(raw_text)
        if repaired:
            logger.warning("Repaired truncated JSON (max_tokens likely hit)")
            return repaired
        logger.warning("Invalid JSON from Claude: %s", raw_text[:300])
        return {}


def validate_enrichment(data: dict) -> dict:
    """Validate and clean enrichment fields against controlled vocabularies."""
    result = {}
    for key in ("transcription", "signature", "date", "medium", "condition_notes"):
        val = data.get(key)
        if val and isinstance(val, str):
            result[key] = val.strip()
        else:
            result[key] = None

    work_type = data.get("work_type")
    result["work_type"] = work_type if work_type in WORK_TYPES else None

    support = data.get("support")
    result["support"] = support if support in SUPPORTS else None

    motifs = data.get("motifs", [])
    result["motifs"] = [m for m in motifs if m in MOTIFS] if isinstance(motifs, list) else []

    return result


# ── Claude analysis ──────────────────────────────────────────────────────────

async def analyze_artwork(
    image_url: str,
    model: str = "sonnet",
    field_guidance: dict[str, str] | None = None,
) -> dict:
    """Send artwork image to Claude for structured analysis.

    Returns a dict with enrichment fields plus a ``usage`` key containing
    input_tokens, output_tokens, model, and estimated cost in USD.

    ``field_guidance`` is an optional dict of field_name → guidance text
    (sourced from resource template alternate_comment fields).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    model_id = MODEL_MAP.get(model, model)
    b64_image, media_type = await fetch_and_encode_image(image_url)
    prompt_text = build_prompt(field_guidance)

    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model_id,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_image}},
                {"type": "text", "text": prompt_text},
            ],
        }],
    )

    raw = parse_claude_response(response.content[0].text)
    result = validate_enrichment(raw)

    # Attach usage info
    usage = response.usage
    result["usage"] = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "model": model_id,
        "cost_usd": _estimate_cost(model_id, usage.input_tokens, usage.output_tokens),
    }
    return result


# Per-million-token pricing (USD)
_PRICING = {
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":            {"input": 15.00, "output": 75.00},
}


def _estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    prices = _PRICING.get(model_id, {"input": 3.0, "output": 15.0})
    return round(
        input_tokens * prices["input"] / 1_000_000
        + output_tokens * prices["output"] / 1_000_000,
        6,
    )

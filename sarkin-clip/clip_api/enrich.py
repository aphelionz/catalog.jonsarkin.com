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

ANALYSIS_PROMPT = """\
You are cataloging artworks by Jon Sarkin (1953–2024) for a catalog raisonné.
Analyze this artwork image and return a JSON object with the following fields.
{
  "transcription": "Transcribe ALL visible text exactly as written by the artist.
                     Do not correct spelling, grammar, punctuation, or capitalization.
                     Do not normalize or interpret — reproduce what is on the surface.
                     Include all words, phrases, letter sequences, and isolated characters.
                     Transcribe every instance of repeated sequences individually
                     (e.g., 'eee eee eee eee eee' not 'eee ×5').
                     Include text fragments, cultural references, and symbols that
                     function as text (describe symbols in brackets: [circle with cross]).
                     Do NOT include the artist's signature or date — these are captured
                     in separate fields. The signature is usually 'JMS' followed by a
                     two-digit year, typically in the lower right.
                     Organize spatially: top to bottom, left to right. Use line breaks
                     to separate distinct text areas.
                     Use [illegible] for unreadable portions.
                     Return null if no text is visible.",
  "signature": "Return a SINGLE character indicating where the signature appears.
                Must be exactly one of: ↖ ↑ ↗ ← → ↙ ↓ ↘ ∅
                Use ∅ if unsigned or no signature visible.
                Return ONLY the one arrow character or ∅ — no other text.",
  "date": "Year the work was created, if determinable from the signature or
           text in the artwork. Return as a string: '2005', 'c. 2005', etc.
           Return null if not determinable.",
  "medium": "Materials/media ONLY — do NOT include the support surface.
             Examples: 'Marker', 'Ink and marker', 'Acrylic and collage',
             'Mixed media', 'Graphite', 'Oil paint'.
             Return null if uncertain.",
  "support": "The surface/substrate. Must be one of:
              Cardboard album sleeve, Paper, Canvas, Board, Wood,
              Found Object, Envelope, Other.
              If the work is square (approximately 12.5 × 12.5 inches),
              the support is almost certainly 'Cardboard album sleeve.'
              Do not override to 'Paper' or 'Cardboard' unless clearly
              not an album sleeve.
              Return null if uncertain.",
  "work_type": "Must be one of: Drawing, Painting, Collage, Mixed Media,
                Sculpture, Print, Other. Return null if uncertain.",
  "motifs": ["Visual motifs present. Choose from: Eyes, Fish, Faces, Hands,
              Text Fragments, Grids, Circles, Patterns, Animals, Names/Words,
              Maps, Numbers, Desert, Boats, Creatures.
              ERR ON THE SIDE OF INCLUSION. If a motif is arguably present,
              include it. This field is additive — more tags are better than
              fewer tags.
              Return empty array if none match."],
  "condition_notes": "Brief note on visible condition issues (tears, staining,
                      foxing, fading). When describing condition, note that
                      edge wear, tearing, creasing, and staining are typically
                      inherent to the artist's process, not post-creation damage.
                      Sarkin did not treat his works as precious objects. Use the
                      phrase 'inherent to the artist's process' to distinguish
                      process-related wear from external damage. Return null if
                      the work appears to be in good condition."
}
Return ONLY valid JSON. No markdown fences, no explanation."""

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
) -> dict:
    """Send artwork image to Claude for structured analysis.

    Returns a dict with enrichment fields plus a ``usage`` key containing
    input_tokens, output_tokens, model, and estimated cost in USD.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    model_id = MODEL_MAP.get(model, model)
    b64_image, media_type = await fetch_and_encode_image(image_url)
    print(f"[enrich] prompt:\n{ANALYSIS_PROMPT}", flush=True)

    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model_id,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_image}},
                {"type": "text", "text": ANALYSIS_PROMPT},
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

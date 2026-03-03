Here’s an updated AGENTS.md with the GPT-4.1-mini transcription step wired in.

```markdown
# Agent: clip-indexer

## 1. Mission

Maintain a GPU-backed semantic index of the Jon Sarkin Omeka-S catalog by:

- Pulling items and media from `https://catalog.jonsarkin.com`.
- Computing CLIP/OpenCLIP embeddings for images (and selected text).
- Using GPT-4.1-mini (vision) to transcribe legible text from artworks.
- Writing vectors + minimal metadata into Qdrant (`omeka_items` collection).
- Using web search:
  - to find the up-to-date Omeka API documentation.
  - to locate `https://catalog.jonsarkin.com/s/about-sarkin-catalog/page/technical-spec`
    for the current Omeka <> Qdrant sync spec.

Omeka-S is the canonical store. Qdrant is a semantic sidecar only.

## 2. Responsibilities

### Catalog v1 (baseline, MUST)

- Model: open_clip ViT-B/32, `laion2b_s34b_b79k`.
- Discover Omeka items that:
  - Use the “Artwork (Jon Sarkin)” template.
  - Have a Title and at least one image.
- For each item:
  - Fetch `omeka_item_id`, `Title`, item URL, and primary image URL.
  - Encode the image via CLIP → `visual_vec` (512-dim, cosine).
  - Upsert into Qdrant with:
    - `point_id = omeka_item_id`
    - Payload:
      - `omeka_item_id` (int)
      - `title` (string)
      - `omeka_url` (string)
      - `thumb_url` (string)
      - `catalog_version = 1`

### Catalog v2 (metadata + text embeddings + API, SHOULD)

For each existing v1 point:

- Fetch extended metadata from Omeka:
  - Description, Subject(s), Medium(s), Year(s), dimensions, collection.
- Generate `ocr_text`:
  - Use a vision-capable model on every image to transcribe clearly
    legible text from the artwork image.
- Build normalized payload fields:
  - `year` (int or array)
  - `subjects[]`
  - `mediums[]`
  - `dimensions_cm = {height, width}`
  - `omeka_description` (string)
  - `collection` (string)
  - `curator_notes[]`
  - `dominant_color` (string, `"unknown"` allowed)
  - `ocr_text` (string)
  - `text_blob` (string; concatenation of title, description, subjects,
    mediums, years, curator notes, and `ocr_text`)
  - `thumb_url`, `omeka_url`
  - `catalog_version = 2`

- Build `TEXT_INPUT = text_blob` and encode with CLIP text encoder
  → `text_vec_clip` (512-dim).
- Upsert payload + `text_vec_clip` into Qdrant for the same `point_id`.

#### clip-api (read-only search shim)

Expose a minimal HTTP API in front of CLIP + Qdrant:

- `GET /healthz`
  - Returns JSON status for the CLIP model and Qdrant.

- `POST /search/text`
  - Request JSON:
    - `query` (string, required)
    - `limit` (int, optional, default 20, max 100)
    - `filters` (object, optional; Qdrant-style payload filters)
  - Behavior:
    - Encode `query` with CLIP text encoder.
    - Search `omeka_items` using `visual_vec` (and optionally combine with
      `text_vec_clip`).
  - Response JSON:
    - `results`: list of `{ omeka_item_id, score, payload? }` where `score` is
      similarity in [0, 1].

- `POST /search/similar`
  - Request JSON:
    - `omeka_item_id` (int, required)
    - `limit` and `filters` as above.
  - Behavior:
    - Uses Qdrant `recommend` over `visual_vec` with the given item as the
      positive anchor.
  - Response JSON:
    - `anchor`: the input `omeka_item_id`
    - `results`: list of `{ omeka_item_id, score, payload? }`.

clip-api is read-only and only ever returns Omeka item IDs and a subset of
payload; it never mutates Qdrant or Omeka and should not be exposed directly
to the public internet without an Omeka-side wrapper.

## Env vars / knobs
- `ANTHROPIC_API_KEY` (required for OCR via Claude)
- `OMEKA_TEMPLATE_ID` (default 2)
- `QDRANT_URL` (default `http://hyphae:6333`)
- `QDRANT_COLLECTION` (default `omeka_items`)
- `INGEST_WORKERS` (thread pool size for parallel ingestion; default 3)
- `FORCE_OCR=1` (ignore OCR cache); or CLI flag `--force-ocr`
- `SEARCH_DB_PATH` (default `.search_index.sqlite`)
- Mount caches in docker/compose for reuse across runs:
  - `./.hf_cache:/app/.hf_cache`
  - `./.ocr_cache.json:/app/.ocr_cache.json`

# sarkin-clip

Minimal CLIP/Qdrant tooling for the Jon Sarkin catalog, plus a read-only API for similar-item lookup.

## clip-api (MVP)

### Run locally

```bash
QDRANT_URL=http://localhost:6333 venv/bin/python -m clip_api.main
```

Example request:

```bash
curl "http://localhost:8000/v1/omeka/items/740/similar?limit=10&catalog_version=2&score_threshold=0.2"
```

Notes:
- `catalog_version` defaults to `2` if omitted.
- `limit` defaults to `30` if omitted (max 200).
- `score_threshold` is optional (0-1) and filters out low similarity matches.
- `GET /healthz` returns `status` of `ok`, `disabled`, or `degraded`.
- Response fields are stable and limited to: `omeka_item_id`, `title`, `omeka_url`, `thumb_url`, `catalog_version`, plus `score` on matches.

### Text search

Endpoint:

```bash
GET /v1/omeka/search
```

Query params:
- `q` (required): text query.
- `catalog_version` (optional, default 2)
- `limit` (optional, default 10, max 50)
- `offset` (optional, default 0)
- `min_score` (optional, 0-1)
- `mode` (optional, default `hybrid`; `hybrid`, `semantic`, `exact`)

Example request:

```bash
curl "http://localhost:8000/v1/omeka/search?q=blue+face&limit=5&min_score=0.2"
```

Notes:
- Search defaults to hybrid (SQLite FTS + CLIP text encoder).
- SQLite index is built during ingestion and stored at `SEARCH_DB_PATH`.
- `min_score` only applies to the semantic (CLIP/Qdrant) portion of hybrid search.
- `mode=exact` uses SQLite FTS only. `mode=semantic` uses CLIP/Qdrant only.
- Response includes `snippet`, derived from `text_blob`/`ocr_text` (semantic) or the FTS index (exact).
- Set `DISABLE_TEXT_SEARCH=1` to return 503 for this endpoint.

### Docker (API + Qdrant)

```bash
docker compose up --build
```

API-only compose file (same services, separate file):

```bash
docker compose -f docker-compose.api.yml up --build
```

### OpenAPI

See `openapi.yaml` for the formal schema.

## Env vars

- `QDRANT_URL` (default `http://hyphae:6333`)
- `QDRANT_COLLECTION` (default `omeka_items`)
- `VECTOR_NAME` (default `visual_vec`)
- `TEXT_VECTOR_NAME` (default `text_vec_clip`)
- `SEARCH_DB_PATH` (default `.search_index.sqlite`)
- `SEARCH_MODE` (default `hybrid`)
- `SEARCH_SEMANTIC_WEIGHT` (default `0.6`)
- `SEARCH_RRF_K` (default `60`)
- `QDRANT_API_KEY` (optional)
- `DEFAULT_LIMIT` (optional)
- `DEFAULT_SCORE_THRESHOLD` (optional)
- `SIMILAR_ENABLED` (optional, default `true`)
  - When `false`, `/v1/omeka/items/{id}/similar` returns 503 and `/healthz` reports `disabled`.
- `DISABLE_TEXT_SEARCH` (optional, default `false`)
  - When `true`, `/v1/omeka/search` returns 503.

## Catalog versions

Catalog v2 (current) payload adds text/metadata for hybrid search and OCR:
- `ocr_text`, `ocr_text_raw`, `ocr_text_norm`, `text_blob`
- `subjects`, `year`, `collection`, `curator_notes`, `dominant_color`
- `catalog_version = 2`

Catalog v1 (legacy) payload fields:
- `omeka_item_id`, `title`, `omeka_url`, `thumb_url`
- `catalog_version = 1`

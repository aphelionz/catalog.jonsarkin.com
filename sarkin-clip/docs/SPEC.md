# sarkin-clip spec

## Purpose
- Provide CLIP/Qdrant ingestion and a read-only API for similar-item and text search on the Jon Sarkin catalog.

## In scope
- `clip_api/` HTTP service and OpenAPI spec.
- Ingestion scripts (`fetch_omeka.py`, `embed_image_to_qdrant.py`) and Makefile tasks.
- Docker Compose stacks for local Qdrant + API (and ingest).
- Qdrant schema (`qdrant-schema.json`) and search index storage.

## Out of scope
- Omeka as the canonical data store (managed elsewhere).
- Model training or fine-tuning.
- Production orchestration outside Docker Compose.

## Dependencies
- Qdrant (local or remote) reachable via `QDRANT_URL`.
- Omeka API as the source of items for ingestion.
- `ANTHROPIC_API_KEY` for OCR during ingest (Claude Sonnet).
- Docker (for compose flows) and/or a Python virtualenv.

## Interfaces
- HTTP endpoints: `GET /healthz`, `GET /v1/omeka/items/{id}/similar`, `GET /v1/omeka/search`.
- Env vars documented in `README.md` (`QDRANT_URL`, `SIMILAR_ENABLED`, `DISABLE_TEXT_SEARCH`, etc.).
- Compose files: `docker-compose.yml`, `docker-compose.api.yml`.
- Makefile tasks: `make ingest`, `make docker-build-gpu`, `make docker-run-gpu`, `make docker-build-cpu`, `make docker-run-cpu`.

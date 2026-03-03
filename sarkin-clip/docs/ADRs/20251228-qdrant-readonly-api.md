# Use Qdrant with a read-only CLIP API

Status: accepted

## Context
- The catalog needs fast similarity and text search without making Qdrant the source of truth.
- Omeka is the canonical data store; Qdrant is a semantic sidecar.

## Decision
- Use Qdrant to store CLIP vectors and related payloads.
- Expose a read-only HTTP API (`clip_api`) for similarity and text search.
- Run services via Docker Compose for local/integration workflows.

## Consequences
- Qdrant availability is required for API responses.
- All writes flow through the ingest process; the API remains read-only.
- Endpoints can be disabled via environment variables when needed.

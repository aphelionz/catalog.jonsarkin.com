# sarkin-clip runbook

## Deploy
- Local API (venv): `QDRANT_URL=http://localhost:6333 venv/bin/python -m clip_api.main`.
- Full local stack (Qdrant + ingest + API): `docker compose up --build`.
- API-only stack: `docker compose -f docker-compose.api.yml up --build`.
- Ingest (local): `make ingest` (runs `fetch_omeka.py`).
- Ingest (containerized):
  - GPU: `make docker-build-gpu` then `make docker-run-gpu`.
  - CPU: `make docker-build-cpu` then `make docker-run-cpu`.

## Rollback
- Check out a previous git commit and rebuild/restart the service.
- For compose, `docker compose down`, roll back, then `docker compose up --build`.

## Kill switches
- Disable similar endpoint: set `SIMILAR_ENABLED=false`.
- Disable text search: set `DISABLE_TEXT_SEARCH=true`.
- Stop the API container/process.

## Smoke tests
- `curl http://localhost:8000/healthz`
- `curl "http://localhost:8000/v1/omeka/items/740/similar?limit=10&catalog_version=2&score_threshold=0.2"`
- `curl "http://localhost:8000/v1/omeka/search?q=blue+face&limit=5&min_score=0.2"`

## Monitoring / alerts
- Not defined here; use `docker compose logs` and Qdrant `/healthz`.

## Notes
- The API is read-only; ingestion is the only writer to Qdrant.

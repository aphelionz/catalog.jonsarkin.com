# CLAUDE.md
## What this is
Catalog raisonné for artist Jon Sarkin (catalog.jonsarkin.com). A monorepo combining a PHP catalog CMS with a Python-powered visual/semantic search service.

## Architecture
- **Omeka S** (PHP) — catalog CMS, MariaDB backend, serves the public site
- **FastAPI** (Python) — CLIP-based similarity search + hybrid text search API
- **Qdrant** — vector database storing 512-dim CLIP embeddings (visual + text)
- **Claude API** — OCR and metadata enrichment for catalog items
- **SQLite FTS** — full-text search index, built during ingestion

## Directory map
- `omeka/` — Omeka S backend: themes, modules, config, Ansible deploy
  - `omeka/volume/themes/sarkin-jeppesen/` — custom theme (the only one we edit)
  - `omeka/volume/modules/FacetedBrowse/` — forked faceted browse module (customized controller + GROUP BY counts)
  - `omeka/volume/modules/SimilarPieces/` — custom similarity UI module
- `sarkin-clip/` — Python CLIP service: FastAPI app, embeddings, tests
  - `sarkin-clip/clip_api/` — FastAPI application code
  - `sarkin-clip/tests/` — pytest suite
- `scripts/` — enrichment pipeline (Claude-based OCR + metadata)
- `ansible-hyphae/` — production provisioning (separate concern)

## Ports
- `8888` — Omeka S (public catalog)
- `8000` — clip-api (search/similarity)
- `6333` — Qdrant (vector DB)

---

## Development commands
```
make doctor       # check prerequisites (docker, rsync, ports)
make local        # start omeka + qdrant + clip-api
make down         # stop all containers
make logs         # tail all service logs
make ingest       # one-shot: index Omeka items into Qdrant (CPU)
```

## Testing
- **Python:** `docker compose exec clip-api pytest` — never run pytest locally from venv
- **PHP/theme:** reload browser at localhost:8888 — changes are live-mounted

## Database
- For data retrieval, favor making direct database calls rather than using the API
- See [docs/omeka-invariants.md](docs/omeka-invariants.md) for Omeka data model, property IDs, API patterns, and theme conventions

---

## Guardrails
### Never without my explicit approval
- `make enrich`, `make enrich-batch`, `make enrich-batch-collect` — hit Claude API, cost money (always run `make enrich-dry` first to preview)
- `make deploy`, `make pull`, or anything touching production data
- Modifying Docker Compose files
- Modifying Ansible/deploy configurations
- Installing third-party Omeka modules (only edit sarkin-jeppesen theme, FacetedBrowse, and SimilarPieces)

### Credentials
- Never include Omeka API credentials or `ANTHROPIC_API_KEY` in commits or output

### Bugs found while working
- Fix trivial one-liners in place; flag anything larger and stay focused on the current task

---

## Commits
- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
- Small and frequent — one logical change per commit

---

## Communication
- What changed and why — skip the obvious, don't restate my instructions

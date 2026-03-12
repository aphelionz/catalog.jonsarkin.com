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
- `docker-compose.prod.yml` — production Docker Compose (Traefik + MariaDB + Omeka + Qdrant + clip-api)

## Ports
- `8888` — Omeka S (public catalog)
- `8000` — clip-api (search/similarity)
- `6333` — Qdrant (vector DB)

---

## Development commands
Run `make` with no args to see all targets. Key ones:
```
make local                              # start omeka + qdrant + clip-api
make down / make logs                   # stop / tail logs
make enrich ARGS="--dry-run"            # preview enrichment
make enrich ARGS="--model haiku"        # run enrichment (costs money)
make process-new ARGS="--dry-run"       # enrich + ingest (preview)
make process-new ARGS="--model haiku"   # enrich + ingest (full run)
make sync                               # pull new items from prod + ingest locally
make ingest                             # re-index Qdrant (incremental)
```

## Testing
- **Python:** `docker compose exec clip-api pytest` — never run pytest locally from venv
- **PHP/theme:** reload browser at localhost:8888 — changes are live-mounted

## Database
- For data retrieval, favor making direct database calls rather than using the API
- DB shell: `docker compose exec -T db mariadb -u root -proot omeka` (root access) or `-uomeka -pomeka` (app user)
- See [docs/omeka-invariants.md](docs/omeka-invariants.md) for Omeka data model, property IDs, API patterns, and theme conventions

## Data flow (prod ↔ dev)
- **Code + schema**: dev → prod only (`make deploy`, `make push-schema`)
- **New items**: prod → dev (`make pull-new`, `make pull-files`)
- **Enrichment**: local laptop → prod API (`make enrich ARGS="--target prod"`); requires confirmation to write
- **Full DB reset**: prod → dev (`make pull`)
- **Files**: prod → dev (`make pull-files`); dev → prod only for dev-created media via rsync

---

## Guardrails
### Never without my explicit approval
- `make enrich` / `make process-new` (without `--dry-run`) — hit Claude API, cost money (always preview with `ARGS="--dry-run"` first)
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

## Preview best practices
- Use `preview_inspect` for CSS/computed style checks — not `preview_eval` + `getComputedStyle`
- Use `preview_snapshot` for text content checks — not `preview_eval` + `textContent`
- Always wrap `preview_eval` in an IIFE: `(async () => { ... })()`
- Always `preview_resize` to `desktop` preset before first screenshot (default viewport is ~650px, which triggers mobile breakpoints)
- Before `preview_start`: run `docker compose down` if containers are up on port 8888

## Known footguns
- **JSON in SQL heredocs:** use `\\n` (double backslash), not `\n`. Single backslash produces literal newlines in stored JSON → 500 errors on faceted browse pages.
- **Site slug:** the public site slug is `catalog` → URLs are `/s/catalog/item/{id}`. Not `main`, not `sarkin`.
- **API returns HTML after `make pull`:** the DB migration page is showing. Visit `localhost:8888/admin` and click "Update database" to clear it.
- **Doctrine cache:** DB changes (privacy, nav) may not appear in frontend until the Omeka container is restarted: `docker compose restart omeka`.
- **Docker symlinks:** Docker volumes don't follow host symlinks. Copy module files rather than symlinking them.
- **BulkImportFiles needs smalot/pdfparser:** the module eagerly loads its PDF extractor even for JPEG imports. Run `composer require smalot/pdfparser` inside `omeka/volume/modules/BulkImportFiles/` (or in the container at that path). Without it, all bulk imports 500.
- **PHP max_file_uploads:** default is 20. For bulk uploads >20 files, add `max_file_uploads = 100` to `/usr/local/etc/php/conf.d/uploads.ini` inside the Omeka container and restart.

## Communication
- What changed and why — skip the obvious, don't restate my instructions

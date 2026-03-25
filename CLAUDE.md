# CLAUDE.md
## What this is
Catalog raisonné for artist Jon Sarkin (catalog.jonsarkin.com). A monorepo combining a PHP catalog CMS with a Python-powered visual/semantic search service.

## Architecture
- **Omeka S** (PHP) — catalog CMS, MariaDB backend, serves the public site
- **FastAPI** (Python) — CLIP-based similarity search, hybrid text search, and Qdrant ingest API
- **Qdrant** — vector database storing 512-dim CLIP embeddings (visual + text)
- **Claude API** — OCR and metadata enrichment (called directly from PHP via EnrichItem module)
- **SQLite FTS** — full-text search index, built during ingestion

## Directory map
- `omeka/` — Omeka S backend: themes, modules, config, Ansible deploy
  - `omeka/volume/themes/sarkin-jeppesen/` — custom theme (the only one we edit)
  - `omeka/volume/modules/FacetedBrowse/` — forked faceted browse module (customized controller + GROUP BY counts)
  - `omeka/volume/modules/SimilarPieces/` — custom similarity UI module
  - `omeka/volume/modules/EnrichItem/` — Claude-based enrichment module (direct Anthropic API calls, batch API, cache)
  - `omeka/volume/modules/IccThumbnailer/` — ICC-preserving thumbnailer with HDR gain map re-embedding
  - `omeka/volume/modules/RapidEditor/` — sprint-mode metadata editor for bulk cataloging
- `sarkin-clip/` — Python CLIP service: FastAPI app, embeddings, tests
  - `sarkin-clip/clip_api/` — FastAPI application code (search, similarity, ingest only — no enrichment)
  - `sarkin-clip/tests/` — pytest suite
- `scripts/` — utility scripts
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
make sync                               # pull new items from prod + ingest locally
make ingest                             # re-index Qdrant (incremental)
```

### Enrichment
Enrichment is now in the Omeka admin UI: **Admin > Enrich Queue**.
- **Single item:** Item show page > Enrich tab > Analyze > Apply
- **Batch (real-time):** Enrich Queue > Enrich All
- **Batch API (50% cheaper):** Enrich Queue > Submit Batch > (wait ~1hr) > Collect
- **Re-apply cache:** Enrich Queue > Apply Cached Results (zero API cost, use after `make pull`)

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
- **Enrichment**: Omeka admin UI (EnrichItem module calls Claude API directly); cached results survive DB resets
- **Full DB reset**: prod → dev (`make pull`)
- **Files**: prod → dev (`make pull-files`); dev → prod only for dev-created media via rsync

---

## Guardrails
### Never without my explicit approval
- Enrichment actions in Omeka admin (hit Claude API, cost money)
- `make deploy`, `make pull`, or anything touching production data
- Modifying Docker Compose files
- Modifying Ansible/deploy configurations
- Installing third-party Omeka modules (only edit sarkin-jeppesen theme, FacetedBrowse, SimilarPieces, IccThumbnailer, and RapidEditor)

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
- **IccThumbnailer wiring:** Omeka's module manager does NOT merge third-party `service_manager` configs into the global config. The factory + alias must live in `local.config.php` with a `require_once` for lazy class loading. Do not try to set them in the module's `module.config.php` alone.
- **Prod files path:** prod files are at `/var/www/omeka-s/files/` (Docker volume), NOT `/opt/catalog/omeka/volume/files/`.
- **Omeka API PATCH considered harmful:** `$this->api()->update('items', $id, $body)` does a **full replacement**, not a merge. Sending `{ 'o:item_set': [...] }` without every other field **deletes all metadata, media, and values**. Never PATCH items to change just one thing. For item set membership, use direct SQL (`INSERT INTO item_item_set`). For field edits, always include the full payload from `readAction` or `buildBasePayload`.

## Communication
- What changed and why — skip the obvious, don't restate my instructions

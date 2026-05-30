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
- **Code**: dev → prod only (`make deploy`)
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
- **Prod files path:** prod files are at `/var/www/omeka-s/files/` (Docker volume), NOT `/opt/catalog/omeka/volume/files/`. Inside the Omeka container the path is `/var/www/html/files/`.
- **Never use `mogrify` on originals:** it re-encodes and strips all JPEG markers, destroying MPF-embedded secondary images (Apple HDR gain maps, Portrait mode depth maps). Use `scripts/rotate_hdr.py` instead — it splits primary + secondary, rotates both with `jpegtran`, and rebuilds the MPF with correct offsets (img[0].size, img[1].offset, img[1].size). `jpegtran` alone with `-copy all` also silently drops secondary images (everything after the primary EOI).
- **Omeka API PATCH considered harmful:** `$this->api()->update('items', $id, $body)` does a **full replacement**, not a merge. Sending `{ 'o:item_set': [...] }` without every other field **deletes all metadata, media, and values**. Never PATCH items to change just one thing. For item set membership, use direct SQL (`INSERT INTO item_item_set`). For field edits, always include the full payload from `readAction` or `buildBasePayload`.

---

## Shopify (jonsarkin.com)
The Shopify store runs at jonsarkin.com. Theme source lives in `shopify/`.

### Theme push/pull
- **ALWAYS `cd shopify/` before running `npx shopify theme push/pull`**. Running from the project root pushes the wrong directory and creates junk files on the remote theme.
- Theme ID: `157306650854` ("Sarkin Estate v2")
- Push to live: `cd shopify && npx shopify theme push --theme 157306650854 --allow-live --nodelete`
- Push specific files: `cd shopify && npx shopify theme push --theme 157306650854 --only sections/header.liquid --allow-live`
- `--nodelete` prevents removing remote files not in local (safe default). Omit it only for full sync, but beware it will try to delete required files (harmless errors).
- `settings_data.json` may not update via `--nodelete` push if the theme editor has already "owned" the settings. Use `--only config/settings_data.json` to force.
- Shopify CLI config is in `shopify/config.yml` (theme access password, not Admin API token).

### Shopify Admin API
- Access token is in `shopify/.env` (never commit)
- GraphQL endpoint: `https://jonsarkin.myshopify.com/admin/api/2025-01/graphql.json`
- Use for: metafield definitions, collection sort order, menu reads, anything the MCP can't do
- MCP covers: products, customers, orders (with scopes), variants, metafields on products, Online Store pages

### MCP server (vendored)
- The `sarkin-shopify` MCP is now a **vendored fork** of `shopify-mcp` at `sarkin-shopify-mcp/` in this repo (was upstream `npx shopify-mcp`). Desktop config runs `node sarkin-shopify-mcp/dist/index.js`.
- To add/edit tools: edit `sarkin-shopify-mcp/dist/tools/*.js`, wire into `dist/index.js` (import + `.initialize` + `server.tool`), then fully quit/reopen Claude Desktop. Auth (client-credentials token exchange) lives in `dist/lib/shopifyAuth.js`; creds are passed as CLI args in the Desktop config.
- Covers: products, customers, orders, variants, product metafields, **and Online Store pages** (`get-pages`, `update-page`, `create-page` — added 2026-05-30; requires the app's `read/write_online_store_pages` scopes).
- Still NOT covered (use Admin API): navigation menus, themes, collections CRUD, metafield definitions.
- Orders scope requires `read_orders` — currently may not be enabled.

### Metafields
- Artwork metafields (pinned to product admin): `artwork.catalog_number`, `artwork.catalog_url`, `artwork.medium`, `artwork.dimensions`, `artwork.year`
- Old namespaces (`sarkin.*`, `custom.*`) have been deleted
- `shopify.*` system metafields are auto-generated and can't be deleted — they're harmless

### Footguns
- **Theme push from wrong directory:** `npx shopify theme push` uses CWD as the theme root. Pushing from project root uploads CLAUDE.md, docker-compose.yml, etc. as theme files. Always `cd shopify/` first.
- **Liquid `sort` filter on collections:** `collection.products | sort: 'price'` does NOT work — it silently returns empty, rendering a blank page. Set collection sort order via Admin API (`collectionUpdate` mutation with `sortOrder: PRICE_ASC`) instead.
- **`settings_data.json` caching:** Shopify's theme editor stores its own copy. Pushing this file may silently fail to update values. Verify with a pull after pushing settings changes.
- **`image_tag` and JS image switching:** Shopify's `image_tag` helper generates `<img srcset="...">` with responsive images. Setting `.src` via JS doesn't work because `srcset` takes priority. Use a plain `<img src="...">` tag when you need JS to swap the image source.
- **SVGs in footer:** Use `stroke="currentColor"` (not `fill`) for Feather-style line icons. The CSS uses `color` inheritance, not `fill`.

## Visual parity — catalog ↔ Shopify
The two sites must be visually indistinguishable to a user navigating between them. This is a standing directive.

- **Any cosmetic change to one site must be applied to the other.**
- Keep CSS variable names and HTML class names identical across both codebases so diffs are easy to audit.
- Shared files:
  - CSS: `omeka/volume/themes/sarkin-jeppesen/asset/css/style.css` ↔ `shopify/assets/theme.css`
  - Layout/header: `omeka/.../view/layout/layout.phtml` ↔ `shopify/sections/header.liquid` + `shopify/layout/theme.liquid`
  - Footer: `omeka/.../view/layout/layout.phtml` ↔ `shopify/sections/footer.liquid`
  - JS: `omeka/.../asset/js/sarkin.js` ↔ inline `<script>` in `shopify/layout/theme.liquid`
- When finishing a cosmetic task, deploy both: `make deploy` + `cd shopify && npx shopify theme push --theme 157306650854 --allow-live`

## MCP tools (sarkin-catalog, sarkin-shopify)

Both MCPs are connected globally in Claude Desktop. Use them — don't default to raw SQL for everything.

**Use the sarkin-catalog MCP for:**
- Discovery: finding items by motif, date range, type, medium, condition
- Similarity search: `find_similar`, `search_by_image` (CLIP embeddings)
- Transcription search: `search_transcriptions`, `fulltext_search`
- Item lookup: `get_item` by catalog number or Omeka ID
- Corpus overview: `corpus_statistics`, `iconographic_profile`

**Use raw SQL for:**
- Mutations (UPDATE, DELETE, INSERT) — the MCP is read-only
- Precise joins or aggregations the MCP doesn't support
- Bulk data operations (the Haiku cleanup, catalog ID regeneration, etc.)
- Checking exact DB state when MCP's Qdrant/FTS index might be stale

**Use the sarkin-shopify MCP for:**
- Product CRUD, variant management, customer/order lookup
- Online Store pages: `get-pages`, `update-page`, `create-page` (body is HTML; theme renders `{{ page.title }}` as H1, so don't repeat the title in the body)
- Anything the MCP supports (products, variants, options, customers, orders, pages)

**Use the Shopify Admin API (curl + GraphQL) for:**
- Navigation menus, themes, collections CRUD, metafield definitions
- Anything not covered by the MCP

## Operational knowledge

These facts get re-explained across sessions. Reference this section instead.

- **Haiku boundary:** Items with Omeka ID ≤ 8824 were enriched by Haiku (lower quality). Items > 8824 were enriched by Opus. Haiku items have known issues: wrong dates, bad transcription formatting, unreliable signatures.
- **Transcription format:** Opus uses `//` as line separator in transcriptions. Haiku used `\n`. The canonical format is `//`.
- **Medium vocabulary:** Normalized values include: Marker, Ink, Oil pastel, Crayon, Colored pencil, Graphite, Paint, Pen, Watercolor, Charcoal, Collage, Mixed media. Compounds use "and" (e.g., "Marker and ink"). Don't invent new medium terms without checking existing vocabulary via `corpus_statistics(breakdown="by_medium")`.
- **Catalog numbers:** Format is `JS-YYYY-NNNNN` (e.g., JS-2016-00042). Generated from item date + sequence. Writing items use `WRT-NNN`.
- **Prod SSH:** `ssh omeka.us-east1-b.folkloric-rite-468520-r2` — but prefer `make` targets over hand-crafted SSH commands.

## Make targets for prod operations

Use these instead of hand-crafting SSH+Docker+MariaDB chains:
- `make deploy` — push code to prod + restart Omeka
- `make pull` — full DB replacement from prod (pull-db + ensure-api-key + pull-files)
- `make pull-new` — additive pull (no wipe)
- `make pull-files` — rsync production file uploads
- `make backup-db` — timestamped local DB backup
- `make restore-db` — restore from backup file
- `make sync` — pull new items + ingest locally
- `make ingest` — re-index Qdrant (incremental)
- `make ingest-full` — full Qdrant re-ingest

When raw SQL on prod is truly needed (no make target covers it), use:
```
ssh omeka.us-east1-b.folkloric-rite-468520-r2 'docker compose -f /opt/catalog/docker-compose.prod.yml exec -T db mariadb -u root -proot omeka -e "YOUR SQL HERE"'
```
Always `make backup-db` before mutations on prod.

## Communication
- What changed and why — skip the obvious, don't restate my instructions

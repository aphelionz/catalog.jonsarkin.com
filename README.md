# catalog.jonsarkin.com

Catalog raisonne for artist Jon Sarkin — a searchable digital archive of artworks with AI-powered metadata enrichment and visual similarity search.

## Table of Contents

- [Background](#background)
- [Architecture](#architecture)
- [Shopify Store](#shopify-store)
- [Install](#install)
- [Usage](#usage)
- [Custom Modules](#custom-modules)
- [CLIP Search Service](#clip-search-service)
- [Data Model](#data-model)
- [Data Flow](#data-flow)
- [Testing](#testing)
- [Maintainers](#maintainers)
- [License](#license)

## Background

Jon Sarkin is a chiropractor-turned-artist whose compulsive creative output began after a 1989 cerebellar stroke. His body of work — thousands of drawings, paintings, collages, and mixed-media pieces — needed a systematic catalog raisonne to track provenance, exhibitions, condition, and iconographic content.

This project combines **Omeka S** (an established digital collections platform) with custom modules and a **CLIP-based visual search service** to create a catalog that supports:

- **Faceted browse** by work type, medium, date, motif, and more
- **Visual similarity search** — find pieces that look alike using CLIP embeddings
- **Hybrid text search** — semantic (CLIP) + lexical (SQLite FTS) with reciprocal rank fusion
- **AI enrichment** — Claude API reads artwork images to extract transcriptions, motifs, materials, signatures, and dates
- **Dark/light mode** — toggle in the header, synced across catalog and Shopify via a shared cookie

### What's stock vs. custom

The catalog runs on **Omeka S v4.2**, a PHP/MySQL digital collections CMS. Stock Omeka provides the item CRUD, media management, resource templates, and REST API. Everything else listed below is custom:

| Layer | Stock Omeka S | Custom |
|-------|--------------|--------|
| CMS core | Item/media CRUD, API, admin UI | — |
| Theme | — | `sarkin-jeppesen` (Jost VF typography, dark/light mode, hi-res hover, citation/share buttons, async similar-pieces loading) |
| Browse | FacetedBrowse module (forked) | GROUP BY count optimization, custom facet renderers |
| Search | Omeka's built-in search | CLIP hybrid search via `SimilarPieces` module + `clip-api` |
| Enrichment | — | `EnrichItem` module (Claude API OCR + metadata extraction) |
| Motif tagging | — | `MotifTagger` module (DINOv2 patch search + CLIP global search) |
| Batch editing | — | `RapidEditor` module (sprint-mode metadata editor with motif autocomplete) |
| Thumbnails | Omeka's ImageMagick thumbnailer | `IccThumbnailer` module (ICC profile preservation + HDR gain map re-embedding) |
| Access control | — | `SiteLockdown` module (password gate with HMAC cookies) |
| Clean URLs | — | `clean-urls.php` rewrite layer (`/item/123` instead of `/s/catalog/item/123`) |
| Vector search | — | `clip-api` FastAPI service + Qdrant |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Browser                                                     │
│  catalog.jonsarkin.com          jonsarkin.com                │
└──────┬─────────────────────────────────┬─────────────────────┘
       │                                 │
 ┌─────▼──────┐     ┌────────────┐  ┌───▼──────────┐
 │  Traefik   │────▶│  Omeka S   │  │   Shopify    │
 │  (proxy)   │     │  :8888     │  │  (hosted)    │
 └────────────┘     └─────┬──────┘  └──────────────┘
                          │ HTTP (internal)       ▲
                    ┌─────▼──────┐                │
                    │  clip-api  │    shared theme, cookie,
                    │  :8000     │    catalog_url metafield
                    └─────┬──────┘
                          │
               ┌──────────┼──────────┐
               │                     │
         ┌─────▼──────┐       ┌──────▼─────┐
         │   Qdrant   │       │ SQLite FTS │
         │  :6333     │       │ (on disk)  │
         └────────────┘       └────────────┘
```

**Services:**

| Service | Image | Purpose |
|---------|-------|---------|
| `omeka` | giocomai/omeka-s-docker:v4.2.0 | Catalog CMS, public site, admin UI |
| `db` | mariadb:10.11 | Omeka's relational store |
| `clip-api` | Custom (Dockerfile.api) | CLIP embeddings, similarity, hybrid search |
| `qdrant` | qdrant/qdrant:v1.16 | Vector database (512-dim CLIP embeddings) |
| `traefik` | traefik:v2.11 (prod only) | Reverse proxy, Let's Encrypt SSL |

The catalog uses **clean URLs** — public paths like `/item/123` and `/faceted-browse/2` are transparently rewritten to Omeka's internal `/s/catalog/...` routes by `omeka/clean-urls.php`.

## Shopify Store

The e-commerce storefront at **jonsarkin.com** runs on Shopify. Theme source lives in `shopify/`.

The two sites are designed to be **visually indistinguishable** — any cosmetic change to one must be mirrored on the other. They share CSS variable names, HTML class names, and a dark/light mode cookie (`sarkin-theme`).

### Catalog ↔ Shopify integration

- Each Shopify product carries an `artwork.catalog_url` metafield linking back to the catalog entry
- Catalog item pages show an "Acquire" link pointing to the Shopify product
- Prices >= $10,000 show "Inquire" instead of a direct purchase button

### Theme management

```sh
cd shopify
npx shopify theme push --theme 157306650854 --allow-live --nodelete
```

See CLAUDE.md for full Shopify workflow details and footguns.

## Install

### Prerequisites

- Docker and Docker Compose
- rsync (for prod data sync)
- SSH access to production server (for `make pull` / `make deploy`)

### Local development

```sh
git clone https://github.com/your-org/catalog.jonsarkin.com.git
cd catalog.jonsarkin.com

# Copy .env.example to .env and fill in credentials
cp .env.example .env

# Start the stack
make local

# Pull production data (requires SSH access)
make pull

# Ingest items into the search index
make ingest
```

The catalog will be available at `http://localhost:8888/`.

### Health check

```sh
make doctor    # checks Docker, rsync, port availability
```

## Usage

### Makefile targets

Run `make` with no arguments to see all targets. Key ones:

```sh
# Development
make local              # Start omeka + qdrant + clip-api
make down               # Stop and remove containers
make logs               # Tail container logs
make doctor             # Check local dev prerequisites

# Data sync (prod -> dev)
make sync               # Pull new items from prod + ingest
make pull-new           # Pull only new items (additive)
make pull               # Full DB reset from prod
make pull-db            # Pull production database into local MariaDB
make pull-files         # Rsync media files from prod

# Search index
make ingest             # Incremental ingest (new/updated items)
make ingest-full        # Full re-ingest all items
make ingest-dry         # Preview what would be ingested
make process-new        # Re-index search after enrichment

# Deployment (dev -> prod)
make deploy             # Rsync code + restart omeka

# Utilities
make backup-db          # Dump local DB (timestamped .sql.gz)
make restore-db BACKUP=path/to/backup.sql.gz
make ensure-api-key     # Create local-only API key
```

### Enrichment

Enrichment runs through the Omeka admin UI at **Admin > Enrich Queue**:

- **Single item:** Item page > Enrich tab > Analyze > Apply
- **Batch (real-time):** Enrich Queue > Enrich All
- **Batch API (50% cheaper):** Enrich Queue > Submit Batch > wait ~1 hr > Collect
- **Re-apply cache:** Enrich Queue > Apply Cached Results (zero API cost)

### Database access

```sh
# Root access
docker compose exec -T db mariadb -u root -proot omeka

# App user
docker compose exec -T db mariadb -uomeka -pomeka omeka
```

## Custom Modules

### EnrichItem

Claude API integration for automated artwork analysis. Sends artwork images to Claude with a structured prompt and receives JSON with:

- **Transcription** of all visible text (OCR)
- **Signature** detection and position (arrow notation)
- **Date** estimation
- **Medium** and **support** identification
- **Work type** classification
- **Motif** tagging from controlled vocabulary
- **Condition** notes

Results are cached in a `enrich_cache` DB table that survives full database resets (`make pull`). Supports three modes: real-time single/batch, Anthropic Batch API (50% cheaper, ~1 hr latency), and cache-only re-application.

### SimilarPieces

Adds visual similarity search to the public site. Provides:

- `/similar/{id}` — page showing visually similar artworks
- `/similar/{id}/json` — API endpoint consumed by the theme for async loading
- Search controller wrapping clip-api's hybrid search

### FacetedBrowse (forked)

Fork of the official Omeka FacetedBrowse module with a GROUP BY optimization in the controller plugin for efficient facet count computation. Custom facet renderers for item sets, resource classes, and values.

### RapidEditor

Sprint-mode metadata editor for bulk cataloging. Features:

- Arrow-key navigation through items with large thumbnail previews
- Motif autocomplete tagger with Claude-powered motif suggestions
- Enter-to-submit for fast tagging workflows
- Restricted to editor and admin roles

### IccThumbnailer

ICC color profile-preserving thumbnail generator. Replaces Omeka's default ImageMagick thumbnailer to:

- Preserve embedded ICC color profiles (uses `-resize` instead of `-thumbnail`)
- Re-embed Apple HDR gain maps into resized thumbnails via MPF format reconstruction
- Admin UI at `/admin/icc-thumbnailer` for bulk thumbnail regeneration

### MotifTagger

Batch motif tagging using visual similarity. Two search modes:

- **Motif (DINOv2)** — patch-level similarity via DINOv2 embeddings
- **Global (CLIP)** — full-image similarity via CLIP embeddings

### SiteLockdown

Password-protected landing page with:

- Curated preview items visible before authentication
- HMAC cookie-based session (bcrypt hash + server secret)
- `noindex` headers and `robots.txt` disallow for search engines

## CLIP Search Service

The `sarkin-clip/` directory contains a FastAPI service that provides visual and text search over the catalog.

### How it works

1. **Ingest:** Each artwork image is encoded into a 512-dimensional vector using OpenCLIP (ViT-B-32, LAION2B). Text metadata is separately encoded. Both vectors are stored in Qdrant.
2. **Visual similarity:** k-NN search on the visual embedding finds artworks that look alike.
3. **Hybrid text search:** Combines CLIP semantic search with SQLite FTS lexical search using reciprocal rank fusion (RRF).

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Service health check |
| GET | `/v1/omeka/items/{id}/similar` | Find visually similar items |
| GET | `/v1/omeka/search?q=...` | Hybrid search (semantic + lexical) |
| POST | `/v1/omeka/images/search` | Visual search by uploaded image (CLIP) |
| POST | `/v1/omeka/images/motif-search` | Motif search by uploaded image (DINOv2) |
| GET | `/v1/omeka/items/{id}/iconography` | Iconographic rarity profile |
| GET | `/v1/omeka/items/iconography/batch` | Batch iconography lookup |
| POST | `/v1/tournament/seed` | Tournament bracket seeding |
| POST | `/v1/ingest/{id}` | Ingest a single item (CLIP) |
| POST | `/v1/dino/ingest/{id}` | Ingest a single item (DINOv2 patches) |

## Data Model

All catalog items use **resource template ID 2** ("Artwork (Jon Sarkin)").

### Key properties

| Property | Term | Notes |
|----------|------|-------|
| Catalog number | `dcterms:identifier` | `JS-YYYY-NNNNN` format |
| Description | `dcterms:description` | AI-generated or manual |
| Date | `dcterms:date` | Year: `YYYY` or `c. YYYY` |
| Work type | `dcterms:type` | Drawing, Painting, Collage, Mixed Media, Sculpture, Print, Video, Other |
| Medium | `dcterms:medium` | Materials (e.g., "Marker on paper") |
| Support | `schema:artworkSurface` | Paper, Cardboard, Canvas, Board, Wood, etc. |
| Motifs | `dcterms:subject` | Repeatable — Eyes, Fish, Faces, Hands, Text Fragments, etc. |
| Transcription | `bibo:content` | OCR of all visible text |
| Signature | `schema:distinguishingSign` | Arrow character indicating position |
| Dimensions | `schema:height` / `schema:width` | Inches |
| Condition | `schema:itemCondition` | Excellent, Good, Fair, Poor, Not Examined |
| Creator | `schema:creator` | Link to Jon Sarkin person item (ID 3) |
| Owner | `bibo:owner` | Default: "The Jon Sarkin Estate" |
| Provenance | `dcterms:provenance` | Ownership history |

See [docs/omeka-invariants.md](docs/omeka-invariants.md) for the complete property map with IDs.

### Controlled vocabularies

Vocabulary terms (Work Type, Support, Motifs, Condition, Signature Position) are stored as plain `literal` values. The CustomVocab module is installed but no longer enforces validation.

## Data Flow

```
Production                          Development
───────────                         ───────────
                make pull-new
  MariaDB  ──────────────────────▶  MariaDB
  /files/  ──────────────────────▶  /files/
                make pull-files

                make deploy
  code     ◀──────────────────────  code

Catalog (catalog.jonsarkin.com)  ◀──── visual parity ────▶  Shopify (jonsarkin.com)
  shared CSS variables, dark/light mode cookie, catalog_url metafield links
```

- **Items and media** flow prod -> dev only (`make pull`, `make pull-new`, `make pull-files`)
- **Code** flows dev -> prod only (`make deploy`)
- **Enrichment** runs in the Omeka admin UI; cached results persist across DB resets
- **Shopify theme** is pushed separately via `cd shopify && npx shopify theme push`

## Testing

### Python (clip-api)

```sh
docker compose exec clip-api pytest
```

Tests cover health checks, text preprocessing, rarity scoring, search (hybrid/semantic/exact modes), and similarity endpoints. Always run inside the container — never from a local venv.

### PHP / Theme

Changes to the theme and modules are live-mounted via Docker volumes. Reload the browser at `http://localhost:8888` to verify.

## Maintainers

[@aphelionz](https://github.com/aphelionz)

## License

All rights reserved. The catalog software and artwork images are not licensed for redistribution.

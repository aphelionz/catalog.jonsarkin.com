# Changelog

Development history for catalog.jonsarkin.com, compiled from git history. Organized by theme.

## AI-Powered Cataloging & Enrichment

- **Claude-powered metadata enrichment** — AI analyzes each artwork image to extract medium, support, dimensions, condition, signature, transcription, and date. Runs from the Omeka admin UI with field-level control, controlled vocabularies, and usage stats.
- **Batch API enrichment** — Submit hundreds of items to Claude's Batch API at 50% cost savings. Cached results survive DB resets for zero-cost re-application.
- **Auto-enrich on upload** — New items are automatically analyzed by Claude and indexed for visual search the moment they're uploaded.
- **Claude-powered motif suggestions** — AI suggests iconographic motifs during RapidEditor sprint sessions.
- **Iconographic profile & rarity scoring** — Each artwork gets a rarity score (1-5) based on how unusual its combination of motifs is across the entire corpus.

## Visual & Semantic Search

- **Visual search (reverse image lookup)** — Upload a photo of a Sarkin work and the system finds it in the catalog using CLIP embeddings in Qdrant.
- **CLIP-based similarity search** — Every item page shows visually similar works, powered by 512-dimensional CLIP embeddings. CPU-only inference on prod.
- **Hybrid transcription search** — Search the visible text on artworks using semantic (meaning-based), lexical (exact match), or hybrid modes.
- **Full-text search** — Header search bar with persistent search terms and clean URL results.

## Cataloging Workflow (RapidEditor)

- **Sprint mode** — Side-by-side layout for rapid metadata entry with arrow key navigation between items.
- **Sticky field persistence** — Owner, Location, and Dimensions values persist across cards during sprint sessions.
- **Auto-suggested catalog IDs** — Format `JS-YYYY-NNNNN`, auto-generated from item date and sequence.
- **Exhibition curation mode** — CLIP-seeded tournament bracket for curating exhibition selections.
- **Category classification pills** — Degrief Classification A-D for sorting works by significance.

## Collector & Commerce Features

- **Shopify integration (jonsarkin.com)** — Custom "Sarkin Estate v2" theme with visual parity to the catalog site.
- **Acquire buttons** — Item pages link directly to Shopify product listings with UTM parameters.
- **Price-based inquiry routing** — Different flows for different price tiers.
- **Collector submission form** — External collectors submit works for inclusion in the catalogue raisonne.
- **Collector notification emails** — Automated Gmail SMTP notifications with item creation and dimensions.
- **Shopify SEO** — Titles from metafields, JSON-LD Organization+Person structured data, blog templates.

## Site Design & UX

- **Visual parity across sites** — Catalog and Shopify are visually indistinguishable: shared CSS variables, HTML classes, dark/light mode synced via shared cookie.
- **Handwritten signature logo** — Jon's actual handwritten signature replaces text site title.
- **Dark mode** — Full dark mode on both sites (#06080e background).
- **Clean URLs** — Removed `/s/catalog/` prefix from all public paths.
- **Mobile-first responsive design** — Fullscreen hamburger nav drawer, mobile carousel, collapsible transcription, responsive footer.
- **Self-hosted Jost typography** — Eliminated Google Fonts dependency and flash of unstyled text.
- **WCAG 2.1 accessibility** — Comprehensive remediation across both sites.
- **Print stylesheet with QR codes** — Item pages print cleanly with a QR code linking back to the digital record.

## Content & Pages

- **About page** — Multi-image gallery with mobile slideshow and desktop lightbox for artist bios.
- **Contact page** — Two-column layout with branded social icons (Substack, X).
- **Collapsible transcription** — Dense artwork text in a collapsible section with `//` line breaks rendered as HTML.
- **Provenance & exhibition history** — Structured display of custody chain and exhibition records.
- **Condition reports** — Process-wear documentation with collector-facing display.
- **Institutional holdings on homepage** — Museums holding Sarkin works (Centre Pompidou, Cape Ann Museum).

## Infrastructure & Performance

- **ICC color-preserving thumbnails** — Custom IccThumbnailer module preserves color profiles and Apple HDR gain maps through the thumbnail pipeline.
- **HDR gain map support** — `rotate_hdr.py` handles EXIF rotation without destroying embedded HDR data.
- **HTTP caching & compression** — Traefik-level gzip + cache headers, WOFF2 font optimization.
- **CPU-only CLIP inference** — Optimized PyTorch install (184MB vs 2GB CUDA) for production.
- **Incremental sync & ingest** — `make sync` pulls only new items; Qdrant ingest skips unchanged items.
- **Faceted browse with GROUP BY counts** — Server-side rendered facets with accurate item counts (forked module).
- **Video support** — HTML5 video player for video media items.

## Security & IP Protection

- **AI crawler blocking** — `X-Robots-Tag` header + `robots.txt` blocks AI/ML training crawlers while allowing search engines.
- **Site lockdown** — Password-protected access with promotional landing page showing preview items.
- **Terms of Use** — Footer link.
- **JSON-LD copyrightHolder** — Estate contact info embedded in structured data on all pages.

## SEO & Discoverability

- **JSON-LD on every page** — Organization + Person structured data across both sites.
- **Meta descriptions & alt text** — Dynamic meta descriptions from artwork metadata, proper alt text for images.
- **Clean URLs** — Human-readable paths for all public pages.
- **Share buttons** — Global share functionality on item pages.

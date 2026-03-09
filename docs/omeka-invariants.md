# Omeka S Invariants

Quick reference for the Jon Sarkin catalog's Omeka S data model, API patterns, and theme conventions.

---

## Data Model

- **Resource template ID:** `2` ("Artwork (Jon Sarkin)") — all catalog items use this
- **Creator item ID:** `3` — Jon Sarkin Person item, linked via `schema:creator`
- **Site slug:** `s/catalog` (used in URL generation — e.g. `/s/catalog/item/123`)

### Core tables

| Table | Purpose |
|---|---|
| `resource` | Base: id, title, resource_type, created, modified, thumbnail_id |
| `item` | Extends resource (FK to resource.id) |
| `media` | Attachments: item_id, storage_id, extension, has_thumbnails, position |
| `value` | Property values: resource_id, property_id, @value (literal), value_resource_id (link) |
| `item_item_set` | Many-to-many junction: item_id ↔ item_set_id |

---

## Property Map

Source of truth: `scripts/enrich_metadata.py` lines 88–114.

| ID | Term | Type | Notes |
|---:|---|---|---|
| 1 | `dcterms:title` | literal | **Deprecated** — all works are untitled |
| 3 | `dcterms:subject` | literal (repeatable) | Motifs from controlled vocab |
| 4 | `dcterms:description` | literal | Description |
| 7 | `dcterms:date` | literal | Year: `YYYY` or `c. YYYY` (range 1989–2024) |
| 8 | `dcterms:type` | literal | Work type from controlled vocab |
| 9 | `dcterms:format` | literal | Framing status (default `∅`) |
| 10 | `dcterms:identifier` | literal | Catalog number: `JS-YYYY-NNNNN` or temp `JS-YYYY-T{id}` |
| 15 | `dcterms:rights` | literal | Rights statement |
| 26 | `dcterms:medium` | literal | Materials (e.g. "Marker on paper") |
| 40 | `dcterms:spatial` | literal | Location (default "Gloucester, MA") |
| 48 | `dcterms:bibliographicCitation` | literal | Citation |
| 51 | `dcterms:provenance` | literal | Provenance |
| 57 | `bibo:annotates` | resource | Link to annotated item |
| 72 | `bibo:owner` | literal | Owner (default "The Jon Sarkin Estate") |
| 74 | `bibo:presentedAt` | resource | Link to exhibition |
| 91 | `bibo:content` | literal | Transcription of all visible text |
| 476 | `schema:distinguishingSign` | literal | Single arrow char for signature position |
| 603 | `schema:height` | literal | Height in inches |
| 921 | `schema:creator` | resource | Link to item ID 3 (Jon Sarkin) |
| 931 | `schema:artworkSurface` | literal | Support from controlled vocab |
| 1129 | `schema:width` | literal | Width in inches |
| 1343 | `schema:creditText` | literal | Credit/attribution |
| 1424 | `schema:box` | literal | Box identifier, parsed for item sets |
| 1579 | `schema:itemCondition` | literal | Condition from controlled vocab |
| 1710 | `curation:note` | literal | Internal curation note |

---

## Controlled Vocabularies

**Work types** (`dcterms:type`): Drawing, Painting, Collage, Mixed Media, Sculpture, Print, Other

**Supports** (`schema:artworkSurface`): Paper, Cardboard, Cardboard album sleeve, Canvas, Board, Wood, Found Object, Envelope, Album Sleeve, Other

**Motifs** (`dcterms:subject`): Eyes, Fish, Faces, Hands, Text Fragments, Grids, Circles, Patterns, Animals, Names/Words, Maps, Numbers

**Condition** (`schema:itemCondition`): Excellent, Good, Fair, Poor, Not Examined

**Signature arrows** (`schema:distinguishingSign`): `↖ ↑ ↗ ← → ↙ ↓ ↘ ∅`
- Arrow = position on artwork, `∅` = unsigned/not visible
- Enrichment format: `↘ JMS 17` → arrow `↘`, year `2017`

---

## API Patterns

### Authentication
```python
params = {"key_identity": "catalog_api", "key_credential": "..."}
```

### Fetching items
```python
GET /api/items?resource_template_id=2&page=1&per_page=500
# Total count in header: Omeka-S-Total-Results
```

### Value construction
```python
# Literal
{"type": "literal", "property_id": 7, "@value": "2005"}

# Resource link
{"type": "resource:item", "property_id": 921, "value_resource_id": 3}
```

### PATCH semantics (critical)

PATCH **replaces ALL properties**. The correct pattern:

1. GET the existing item
2. Copy all vocab properties (keys containing `:` with list values)
3. Copy system keys: `o:resource_class`, `o:item_set`, `o:media`, `o:is_public`
4. Merge enriched values (only overwrite if target is empty)
5. **Never send `o:resource_template`** — triggers re-validation, breaks items missing required fields
6. PATCH with the complete merged payload

### Cleaning values for PATCH
Strip read-only keys from each value dict before sending. Keep only: `type`, `property_id`, `@value`, `@language`, `value_resource_id`, `uri`, `o:is_public`.

---

## Theme Patterns

Theme: `omeka/volume/themes/sarkin-jeppesen/`

### Value access (PHP)
```php
// Single value with null fallback
$v = $item->value('dcterms:date', ['default' => null]);

// All values (repeatable fields like subjects)
$values = $item->value('dcterms:subject', ['all' => true, 'default' => []]);

// Linked resource (creator, exhibitions)
$val = $item->value('schema:creator');
$vr = $val ? $val->valueResource() : null;  // Item object or null
$name = $vr ? $vr->displayTitle() : (string)$val;
```

### EXIF/MIME filtering
Raw property values can contain EXIF timestamps or MIME types from auto-import. Filter at display time:
```php
// Reject EXIF timestamps like "2026:03:02 11:47:51"
preg_match('/^\d{4}:\d{2}:\d{2}/', $str)  → discard

// Reject MIME types like "image/jpeg"
preg_match('#^[\w-]+/[\w.+-]+$#', $str)   → discard
```

### Asset paths
```php
$this->assetUrl('css/style.css')           // Theme asset
$this->assetUrl('js/global.js', 'Omeka')   // Core Omeka asset
```

### Media access
```php
$pm = $item->primaryMedia();
$url = $pm->originalUrl();                  // Full-res image
$thumb = $pm->thumbnailUrl('large');        // Pre-rendered thumbnail
```

### Layout helpers
```php
$this->headTitle('Page') ->setSeparator(' · ');
$this->headLink()->appendStylesheet($url);
$this->inlineScript()->appendFile($url);    // Deferred to </body>
$this->themeSetting('browse_layout');        // Theme config value
```

---

## SimilarPieces Module

Module: `omeka/volume/modules/SimilarPieces/`

### Routes
| Route | Response | Purpose |
|---|---|---|
| `/similar/:item_id` | HTML | Full similar-pieces page |
| `/similar/:item_id/json` | JSON | Async widget data (12 results) |
| `/iconography/:item_id/json` | JSON | Motif frequency table |
| `/iconography/batch/json?ids=1,2,3` | JSON | Batch badge data for browse cards |
| `/similar/search?q=...` | HTML | Visual/text search UI |

### Key patterns
- **Factory injection:** Controller gets `Omeka\HttpClient`, `ApiManager`, `Logger`, merged config
- **Clone HTTP clients:** `$client = clone $this->httpClient` before each request (avoids state leakage)
- **Config resolution:** `Omeka\Settings` overrides `module.config.php` defaults
- **Error handling:** Returns 502 with `{"error": "..."}` on service failure; frontend hides section silently

---

## Gotchas

1. **PATCH replaces everything** — always read-then-merge; never send partial property sets
2. **Never send `o:resource_template` in PATCH** — triggers validation that breaks incomplete items
3. **Properties can be text OR resource links** — always check `valueResource()` before assuming string
4. **EXIF timestamps pollute date fields** — filter with regex at display time
5. **MIME types pollute format/type fields** — same: filter at display
6. **`dcterms:title` is deprecated** — all Jon Sarkin works are untitled; don't set or rely on it
7. **Album sleeve defaults** — when `artworkSurface = "Album Sleeve"`, height/width default to `12.5`
8. **2-digit signature years** — `00–49 → 20xx`, `50–99 → 19xx`
9. **Async JS sections fail silently** — if clip-api is down, similar/iconography sections just stay hidden
10. **No SCSS** — all CSS is hand-written in `asset/css/style.css`; selector specificity matters
11. **`prepend()` loads in reverse order** — head management stack quirk
12. **Fetch API doesn't reject on HTTP errors** — JS must check `r.ok` explicitly

---

## Hot File Structure Maps

These files are modified in almost every session. Use line-range reads instead of full-file reads.

### `asset/css/style.css` (2379 lines)

| Lines | Section |
|---|---|
| 1–137 | **Global** — reset, CSS custom properties (`:root`), `@font-face`, base, utility, user bar |
| 138–338 | **Site layout** — header, search, navigation, mobile nav, footer |
| 339–899 | **Item show (detail page)** — record header, metadata bar, action nav, media/artwork, writing content, context/documentation, iconographic profile, lexical profile, similar pieces |
| 900–1137 | **Item browse (card grid)** — browse header, card grid, individual cards, list view, no-image placeholder, writing cards |
| 1138–1223 | **Item-set browse** |
| 1224–1602 | **Faceted browse** — two-column layout, sidebar facets, browse controls, pagination, sort, loading spinner, mobile sidebar toggle, responsive overrides |
| 1603–1651 | **Search results** |
| 1652–1675 | **Content pages** (Omeka page blocks) |
| 1676–1973 | **Home page** — masthead, intro row, stats, institutions, section headings, quote, body text, studio image, collections, CTA, acknowledgments, responsive |
| 1974–2112 | **Pagination** (shared) |
| 2113–2139 | **Page title** (shared) |
| 2140–2215 | **Responsive** (shared breakpoints) |
| 2216–end | **Print** — optimised for single-page item sheets |

### `view/omeka/site/item/show.phtml` (574 lines)

| Lines | Section |
|---|---|
| 1–47 | **Setup** — property helpers (`$val`, `$allVals`), identity extraction (catalog number, date, etc.) |
| 48–62 | **Identity** — catalog number, creator, date, type, medium |
| 63–69 | **Writing detection** — `$isWriting` flag based on resource class |
| 70–107 | **Physical** — dimensions, support, signature, framing, condition |
| 108–125 | **Custody & Rights** — owner, provenance, location, rights |
| 126–147 | **Documentation, Collector layer, Media** — related items, exhibitions, media list |
| 148–230 | **SEO** — title, meta description, Open Graph, Twitter Card, canonical URL, Schema.org JSON-LD |
| 231–375 | **HTML output** — `<article>` with record header, metadata bar, action nav, artwork/media, context sections |
| 375–574 | **Detail sections** — documentation, collector info, iconographic profile, lexical profile, similar pieces, transcription, rights footer, print QR |

### `asset/js/sarkin.js` (315 lines)

| Lines | Section |
|---|---|
| 1–12 | **Setup** — IIFE, `esc()` helper |
| 14–22 | **Mobile nav toggle** |
| 24–50 | **Cite button** — Chicago-style citation to clipboard |
| 52–68 | **Share button** — Web Share API with fallback |
| 70–106 | **Similar pieces** — async fetch from clip-api, renders card grid |
| 108–154 | **Iconographic profile** — async fetch, renders motif frequency bars |
| 156–208 | **Lexical profile** — async fetch, renders word frequency chart |
| 210–256 | **Iconographic badges** — batch fetch for browse page cards |
| 258–282 | **Zoom follow cursor** — 2× magnification on artwork hover |
| 284–294 | **Live catalog count** — home page API fetch |
| 296–315 | **Print QR code** — renders QR for current item URL |

### `FacetedBrowse/src/Controller/Site/PageController.php` (188 lines)

| Lines | Section |
|---|---|
| 9–103 | **`pageAction()`** — main browse page: resolves category, computes facet counts via custom GROUP BY, handles sort/pagination |
| 104–114 | **`categoriesAction()`** — returns category list for sidebar |
| 115–125 | **`facetsAction()`** — returns facet values with counts for a given category |
| 126–188 | **`browseAction()`** — handles filtered browse with applied facets, sort-by-value options |

Key customization: `computeFacetCounts()` uses GROUP BY queries instead of per-facet iteration. Edit facet behavior in the controller plugin (same directory).

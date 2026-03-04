# Jon Sarkin Catalog Raisonné — Metadata Mapping

Canonical reference for every catalog field: what it maps to in Omeka S,
how it appears in the theme, and what it emits as JSON-LD.

## Vocabularies in Use

| Prefix | Vocabulary | Role |
|--------|-----------|------|
| `dcterms:` | Dublin Core | Archival backbone — title, date, identifier, rights, provenance, subject, medium, etc. |
| `schema:` | Schema.org | Web/SEO + art-specific — VisualArtwork, artworkSurface, height, width, itemCondition, creditText |
| `bibo:` | BIBO | Documentation — owner, inscriptions (annotates), content (transcription), presentedAt, bibliographicCitation |
| `curation:` | Curation | Internal notes — display & care |

## Resource Template: "Artwork (Jon Sarkin)"

Resource class: `schema:VisualArtwork` (id 225)

### Identity

| Field | Property | ID | Type | Required | Admin Label | Entry Guidance |
|-------|----------|----|------|----------|-------------|----------------|
| Title | `dcterms:title` | 1 | Text | Yes | Title | Work title. Use "Untitled" + parenthetical description if none: "Untitled (Fish and Eyes)" |
| Catalog Number | `dcterms:identifier` | 10 | Text | Yes | Identifier | Format: `JS-YYYY-NNNN`. Undated: `JS-ND-NNNN`. Permanent — never reused. |
| Creator | `schema:creator` | 921 | Resource:Item | No | creator | Link to Jon Sarkin Person item (id:3). Set once per item. |
| Date | `dcterms:date` | 7 | Text | No | Date | Free text. Conventions: `2005`, `c. 2005`, `before 2010`, `after 2000`, `2000–2005`, `undated` |
| Work Type | `dcterms:type` | 8 | CustomVocab:"Work Type" | No | Type | Pick from: Drawing, Painting, Collage, Mixed Media, Sculpture, Print, Other |
| Series | *(Item Set membership)* | — | — | — | — | Assign item to appropriate Item Set(s): "12×12s", etc. |

### Physical Description

| Field | Property | ID | Type | Required | Admin Label | Entry Guidance |
|-------|----------|----|------|----------|-------------|----------------|
| Medium | `dcterms:medium` | 26 | Text | No | Medium | Materials: "Marker and ink on paper", "Acrylic on canvas" |
| Support | `schema:artworkSurface` | 931 | CustomVocab:"Support" | No | artworkSurface | Pick from: Paper, Cardboard, Canvas, Board, Wood, Found Object, Envelope, Album Sleeve, Other |
| Height | `schema:height` | 603 | Text | No | height | Dual unit: "12 in (30.5 cm)". Height first by convention. |
| Width | `schema:width` | 1129 | Text | No | width | Dual unit: "12 in (30.5 cm)" |
| Signature | `schema:distinguishingSign` | 476 | Text | No | **Signature** (alt label) | Arrow indicating location + initials/date: "↘ JMS 17", "↙ JS 05". Use `∅` for unsigned. Arrows: ↖ ↑ ↗ ← → ↙ ↓ ↘ |
| Condition | `schema:itemCondition` | 1579 | CustomVocab:"Condition" | No | itemCondition | Pick from: Excellent, Good, Fair, Poor, Not Examined |
| Framing | `dcterms:format` | 9 | Text | No | Format | "Framed", "Unframed", "Mounted on board" |

### Custody & Rights

| Field | Property | ID | Type | Required | Admin Label | Entry Guidance |
|-------|----------|----|------|----------|-------------|----------------|
| Owner | `bibo:owner` | 72 | Text | No | **Owner / Repository** (alt label) | Current owner: "Private collection", "The Jon Sarkin Estate", or named collector/institution |
| Location | `dcterms:spatial` | 40 | Text | No | Spatial Coverage | Current location city: "Gloucester, MA", "New York, NY" |
| Provenance | `dcterms:provenance` | 51 | Text | No | Provenance | Custody chain. Repeatable — add one value per transfer: "Estate of the artist, 2024–present" |
| Rights | `dcterms:rights` | 15 | Text | No | Rights | Default: "© The Jon Sarkin Estate / Artists Rights Society (ARS), New York" |
| Credit Line | `schema:creditText` | 1343 | Text | No | creditText | Full credit: "Jon Sarkin, *Title*, Date. Medium. Dimensions. Collection. © Estate / ARS, NY." |

### Documentation

| Field | Property | ID | Type | Required | Admin Label | Entry Guidance |
|-------|----------|----|------|----------|-------------|----------------|
| Inscriptions | `bibo:annotates` | 57 | Text | No | **Inscriptions** (alt label) | Rare. Only for text where Jon is directly addressing the viewer/recipient (dedications, messages). Most text is part of the art itself → use Transcription. |
| Transcription | `bibo:content` | 91 | Text | No | content | Full text transcription of all words/text in the artwork. Include title text, marginal text, repeated phrases — everything visible. Use `[description]` for non-text elements like `[fish drawing]`. |
| Exhibition History | `bibo:presentedAt` | 74 | Text | No | presented at | Repeatable. One value per show: "Solo Show Title, Gallery Name, City, Year" |
| Bibliography | `dcterms:bibliographicCitation` | 48 | Text | No | Bibliographic Citation | Repeatable. Standard citation format per entry. |
| Description | `dcterms:description` | 4 | Text | No | Description | General notes, context, scholarly commentary about the work. |

### Collector Layer

| Field | Property | ID | Type | Required | Admin Label | Entry Guidance |
|-------|----------|----|------|----------|-------------|----------------|
| Motifs | `dcterms:subject` | 3 | CustomVocab:"Motifs" | No | Subject | Repeatable. Pick from controlled list: Eyes, Fish, Faces, Hands, etc. |
| Display & Care | `curation:note` | 1710 | Text | No | **Display & Care** (alt label) | Conservation notes: "Light-sensitive. Recommend limited light exposure and archival storage." |

## Custom Vocabularies

Create in Admin → Custom Vocab:

### Work Type
```
Drawing
Painting
Collage
Mixed Media
Sculpture
Print
Other
```

### Support
```
Paper
Cardboard
Canvas
Board
Wood
Found Object
Envelope
Album Sleeve
Other
```

### Motifs
```
Eyes
Fish
Faces
Hands
Text Fragments
Grids
Circles
Patterns
Animals
Names/Words
Maps
Numbers
```
*(Expand as corpus analysis reveals more recurring motifs)*

### Condition
```
Excellent
Good
Fair
Poor
Not Examined
```

## JSON-LD Output (Schema.org VisualArtwork)

Emitted in `<script type="application/ld+json">` on item show pages.
Values mapped from Omeka properties → Schema.org:

```json
{
  "@context": "https://schema.org",
  "@type": "VisualArtwork",
  "name": "← dcterms:title",
  "identifier": "← dcterms:identifier",
  "creator": {
    "@type": "Person",
    "name": "Jon Sarkin",
    "birthDate": "1953",
    "deathDate": "2024"
  },
  "dateCreated": "← dcterms:date",
  "artMedium": "← dcterms:medium",
  "artworkSurface": "← schema:artworkSurface",
  "artform": "← dcterms:type",
  "height": "← schema:height",
  "width": "← schema:width",
  "image": "← primaryMedia.originalUrl()",
  "copyrightHolder": {
    "@type": "Organization",
    "name": "The Jon Sarkin Estate"
  },
  "creditText": "← schema:creditText",
  "locationCreated": {
    "@type": "Place",
    "name": "← dcterms:spatial"
  },
  "isPartOf": {
    "@type": "CreativeWork",
    "name": "Jon Sarkin: Catalog Raisonné",
    "url": "https://catalog.jonsarkin.com"
  },
  "mainEntityOfPage": "← canonical page URL"
}
```

Properties only included if they have values (no nulls/empties).

## Theme Template Property Reference

Quick lookup for `$item->value('term')` calls in PHP templates:

```php
// Identity
$item->value('dcterms:title')
$item->value('dcterms:identifier')
$item->value('dcterms:date')
$item->value('dcterms:type')

// Physical
$item->value('dcterms:medium')
$item->value('schema:artworkSurface')
$item->value('schema:height')
$item->value('schema:width')
$item->value('schema:distinguishingSign')
$item->value('schema:itemCondition')
$item->value('dcterms:format')           // framing

// Custody & Rights
$item->value('bibo:owner')
$item->value('dcterms:spatial')
$item->value('dcterms:provenance')        // repeatable
$item->value('dcterms:rights')
$item->value('schema:creditText')

// Documentation
$item->value('bibo:annotates')            // repeatable (inscriptions)
$item->value('bibo:content')              // transcription
$item->value('bibo:presentedAt')          // repeatable
$item->value('dcterms:bibliographicCitation') // repeatable
$item->value('dcterms:description')

// Collector
$item->value('dcterms:subject')           // repeatable (motifs)
$item->value('curation:note')             // display & care

// For all values of a repeatable property:
$item->value('dcterms:subject', ['all' => true])
```

## Automated Enrichment Pipeline

`scripts/enrich_metadata.py` sends artwork images to Claude for structured
analysis, then writes enriched metadata back to Omeka S.

### Title Strategy (Hybrid #5)

Most Sarkin works are untitled. The pipeline uses a hybrid approach:
- If Claude finds prominent, legible text in the artwork, it suggests a
  short evocative title (3–6 words) derived from that text.
- If no text or no dominant subject → title stays empty and the catalog
  number (`dcterms:identifier`) serves as the display identifier.
- Existing titles are never overwritten (only empty/"Untitled" slots are filled).

### What the pipeline extracts

| Field | Omeka Property | Behavior |
|-------|---------------|----------|
| Title | `dcterms:title` | Set only if currently empty or "Untitled" |
| Transcription | `bibo:content` | All visible text, preserving line breaks |
| Signature | `schema:distinguishingSign` | Arrow + initials/date, or ∅ |
| Date | `dcterms:date` | From signature if visible |
| Medium | `dcterms:medium` | Materials description |
| Support | `schema:artworkSurface` | From controlled vocab |
| Work Type | `dcterms:type` | From controlled vocab |
| Motifs | `dcterms:subject` | From controlled vocab (repeatable) |

### Defaults applied

| Field | Value |
|-------|-------|
| Owner | The Jon Sarkin Estate |
| Framed | ∅ |
| Creator | Link to Jon Sarkin item (id:3) |

### Safety: fill-only, never overwrite

The pipeline only fills empty fields. If a property already has a value,
it is preserved. This means you can manually curate any field and the
pipeline will respect your edits on subsequent runs.

### CRITICAL: Omeka S PATCH behavior

Omeka S PATCH replaces the **entire** property set — not just the fields
you send. The script always:
1. GETs the full item (all properties)
2. Merges new values into the existing property map
3. PATCHes the complete merged set

Never call PATCH with a partial property set or you **will lose data**.

### Usage

```bash
# Install dependencies (once)
pip install -r scripts/requirements.txt

# ── Real-time mode (one item at a time) ──
python scripts/enrich_metadata.py --item-id 6886
python scripts/enrich_metadata.py --dry-run --limit 10
make enrich          # all items, real-time
make enrich-dry      # preview only

# ── Batch API mode (50% cheaper, ~1 hour turnaround) ──
make enrich-batch             # submit all items
make enrich-batch-status      # check progress
make enrich-batch-collect     # collect results + apply to Omeka

# Or with options:
python scripts/enrich_metadata.py --batch --limit 100
python scripts/enrich_metadata.py --batch-collect --dry-run

# ── Model selection ──
python scripts/enrich_metadata.py --model haiku  --batch  # $7 total
python scripts/enrich_metadata.py --model sonnet --batch  # $21 total
python scripts/enrich_metadata.py --model opus   --batch  # $42 total
```

### Batch API workflow

The Batch API processes requests asynchronously at 50% off. Typical flow:

1. **Submit** — `make enrich-batch` downloads images, resizes to 1024px,
   submits to Anthropic in chunks of 1000. Prints batch IDs.
2. **Wait** — check with `make enrich-batch-status`. Most batches complete
   within 1 hour (24-hour max).
3. **Collect** — `make enrich-batch-collect` downloads results, caches the
   analysis JSON, and applies enrichment to Omeka items.

Batch metadata is saved in `scripts/.enrich_batches/` (gitignored) so you
can resume collection after interruption.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(required)* | Claude API key |
| `OMEKA_BASE_URL` | `http://localhost:8888` | Omeka S base URL |
| `OMEKA_KEY_IDENTITY` | `catalog_api` | API key identity |
| `OMEKA_KEY_CREDENTIAL` | `sarkin2024` | API key credential |

### Model selection

| Flag | Model | Batch cost (3,415 items) | Quality |
|------|-------|-------------------------|---------|
| `--model haiku` | Claude Haiku 4.5 | ~$7 | Good for clear text |
| `--model sonnet` | Claude Sonnet 4.5 | ~$21 | Best for handwriting + motifs |
| `--model opus` | Claude Opus 4.5 | ~$42 | Highest accuracy |

Use Haiku for dev/testing, Sonnet for the main enrichment pass, and
optionally Opus for a final quality pass.

### Caching

Analysis results are cached in `scripts/.enrich_cache.json` (gitignored).
Use `--force` to re-analyze. Cache keys include the prompt version, so
prompt changes automatically invalidate the cache.

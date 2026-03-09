---
name: harvest-import
description: Run the Wayback Machine harvest pipeline and import writings into Omeka. Use when asked to harvest jsarkin.com writings, import writings, or run the wayback pipeline.
---

# Harvest & Import Writings

Harvest Jon Sarkin's writings from the Wayback Machine and import them
into Omeka S as items using the "Writing (Jon Sarkin)" resource template.

---

## Phase 1: Harvest from Wayback Machine

The harvest pipeline has 4 phases. Each is resumable and idempotent.

### Step 1 — Discover URLs

```bash
make harvest-discover
```

Queries the Wayback Machine CDX API for all archived `jsarkin.com` content.
Classifies URLs into content types. Preview with `--dry-run`:
```bash
python3 scripts/harvest_wayback.py discover --dry-run
```

### Step 2 — Fetch HTML

> **Rate-limited (~1.5s/page).** Can take hours for full corpus.

```bash
make harvest-fetch
```

Resumable — safe to interrupt and restart. Fetches archived HTML to `harvest/raw_html/`.
Limit for testing: `python3 scripts/harvest_wayback.py fetch --limit 10`

### Step 3 — Extract text

```bash
make harvest-extract
```

Parses HTML, preserves formatting, deduplicates across Drupal and WordPress
eras. Outputs to `harvest/texts/`.

### Step 4 — Generate output

```bash
make harvest-output
```

Produces:
- `harvest/sarkin_jsarkin_complete.json` — master JSON with all writings
- `harvest/sarkin_jsarkin_omeka.csv` — Omeka CSV import format
- `harvest/harvest_report.md` — summary report

---

## Phase 2: Import into Omeka

### Prerequisites

- Docker stack running (`make local`)
- API key set up (`make ensure-api-key`)
- Harvest JSON exists at `harvest/sarkin_jsarkin_complete.json`

### Step 1 — Preview

```bash
python3 scripts/import_writings.py --dry-run --limit 5
```

Requires env vars: `OMEKA_KEY_IDENTITY` and `OMEKA_KEY_CREDENTIAL`.

### Step 2 — Import

> **Requires user approval.** Creates items in Omeka.

```bash
python3 scripts/import_writings.py --limit 50   # first batch
python3 scripts/import_writings.py               # all writings
```

Each writing becomes an item with:
- Resource template: `3` ("Writing (Jon Sarkin)")
- Resource class: `118` (schema:CreativeWork)
- Item set: `7502` ("jsarkin.com Writings, 1997–2019")
- Creator: linked to item `3` (Jon Sarkin)

### Step 3 — Verify

```bash
docker compose exec -T db mariadb -u root -proot omeka \
  -e "SELECT COUNT(*) AS writings FROM item i JOIN resource r ON i.id = r.id WHERE r.resource_template_id = 3;"
```

---

## Output locations

| Path | Contents |
|---|---|
| `harvest/raw_html/` | Cached Wayback HTML pages |
| `harvest/texts/` | Extracted text files |
| `harvest/images/` | Extracted images |
| `harvest/sarkin_jsarkin_complete.json` | Master JSON |
| `harvest/sarkin_jsarkin_omeka.csv` | Omeka CSV import |
| `harvest/harvest_report.md` | Summary report |

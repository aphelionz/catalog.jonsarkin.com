---
name: enrichment-pipeline
description: Run the enrichment pipeline to pull production data, backfill defaults, and enrich catalog items with Claude OCR and motif detection. Use when asked to enrich, run the pipeline, or process new catalog items.
---

# Enrichment Pipeline

Run this pipeline to pull new items from production, fill in missing metadata,
and enrich the catalog with Claude-powered OCR and motif detection.

**Core safety principle:** Every script only fills empty fields — existing
values are never overwritten. You can manually curate any field and the
pipeline will respect your edits on subsequent runs.

See `docs/metadata-mapping.md` for the canonical field definitions, property
IDs, and controlled vocabularies.

---

## Prerequisites

- Docker running (`docker compose version`)
- `ANTHROPIC_API_KEY` set in environment (only for steps 9–10; not needed for apply-cache)
- SSH access to production host (for pull steps)

## Cost model

Claude analysis results are cached in `scripts/.enrich_cache.json`. After the
first enrichment run, subsequent DB wipes + re-applies cost nothing — use
`make enrich-apply` to re-apply from cache without calling the Claude API.

Only `make enrich-batch` (step 9) costs money. Steps 1–8 and 11–12 are free.

## Pipeline

### Step 1 — Start local stack

```bash
make local
```

Verify all containers are healthy:

```bash
docker compose ps
```

Wait for Omeka to respond at `http://localhost:8888`.

### Step 2 — Backup local database

Safety net before the pull overwrites the local DB.

```bash
make backup-db
```

Verify: a non-zero `.sql.gz` file appears in `backups/`.

To restore from a backup:
```bash
make restore-db BACKUP=backups/omeka-XXX.sql.gz
```

### Step 3 — Pull production data

> **Requires user approval.** Touches production server and overwrites the
> local database.

```bash
make pull
```

This runs `pull-db`, `pull-files`, `pull-modules`, and `pull-themes` via rsync.
New items added on production come down with the database. Files are additive
(rsync only downloads new/changed files).

Verify the item count:

```bash
docker compose exec -T db mariadb -uomeka -pomeka omeka \
  -e "SELECT COUNT(*) AS items FROM item;"
```

### Step 4 — Re-apply cached enrichments

Apply any previously cached Claude results to the fresh database. This is
free (no API call) and fast. Skip this step only on the very first run when
no cache exists yet.

```bash
make enrich-apply
```

Verify: output shows applied count. Items with no cached results are skipped.

### Step 5 — Doctor-catalog (baseline)

```bash
make doctor-catalog
```

Review the Field Summary table. This is the "before" snapshot — note the
missing-field counts and percentage coverage. Optionally save to a file:

```bash
make doctor-catalog 2>/dev/null > reports/doctor-pre.txt
```

### Step 6 — Backfill defaults (preview)

```bash
make backfill-dry
```

Review the output. The backfill script fills these defaults only when missing:

| Field | Default Value |
|-------|---------------|
| Location | Gloucester, MA |
| Creator | Jon Sarkin (resource link to item 3) |
| Work Type | Drawing |
| Support | Album Sleeve |
| Owner | The Jon Sarkin Estate |
| Height/Width | 12.5 (when support is Album Sleeve) |
| Box | Copies title |
| Identifier | Generates temp ID `JS-{year}-T{item_id}` |

### Step 7 — Backfill defaults (apply)

```bash
make backfill
```

Verify: output shows patched count with zero failures.

### Step 8 — Doctor-catalog (post-backfill)

```bash
make doctor-catalog
```

Compare to Step 5. Location, Creator, Work Type, Support, Owner, Height,
Width, Box, and Catalog Number gaps should be reduced. Remaining gaps are
fields that need Claude analysis: Title, Medium, Signature, Motifs, Date.

### Step 9 — Enrichment preview

> **Always preview before submitting a batch.**

```bash
make enrich-dry
```

Review which items need enrichment and what changes Claude would make.
Uses cached results from `scripts/.enrich_cache.json` when available.

The analysis prompt is defined in `scripts/enrich_metadata.py` at line 141
(`ANALYSIS_PROMPT`). Claude extracts: title, transcription, signature, date,
medium, support, work_type, motifs, and condition_notes. See
`docs/metadata-mapping.md` for how these map to Omeka properties.

### Step 10 — Submit enrichment batch

> **Requires user approval.** Calls the Claude API. Costs real money.

| Model | Flag | Batch cost (~3,400 items) |
|-------|------|--------------------------|
| Haiku | `--model haiku` | ~$7 |
| Sonnet | `--model sonnet` | ~$21 |
| Opus | `--model opus` | ~$42 |

```bash
make enrich-batch                # defaults to haiku
# or for a specific model:
python3 scripts/enrich_metadata.py --batch --model sonnet
```

The script downloads images (20 parallel workers), resizes to 1024px max,
and submits in chunks of 500 to the Anthropic Batch API. Batch metadata is
saved to `scripts/.enrich_batches/`.

### Step 11 — Monitor and collect results

Check batch status (no API cost, can repeat):

```bash
make enrich-batch-status
```

Wait until all batches show "ended" (~1 hour typical, 24-hour max).

Preview what the collection would write:

```bash
python3 scripts/enrich_metadata.py --batch-collect --dry-run
```

> **Requires user approval.** Writes enrichment results to Omeka.

```bash
make enrich-batch-collect
```

Verify: output shows applied count with acceptable error count.

### Step 12 — Doctor-catalog (post-enrichment)

```bash
make doctor-catalog
```

Compare to Step 8. Title, Medium, Signature, Motifs, and Date gaps should
be significantly reduced. Items that still lack a Title likely had no legible
text. Items missing Date were unsigned.

### Step 13 — Re-ingest into Qdrant

Rebuild the search index so it reflects newly enriched metadata and
transcriptions.

```bash
make ingest
```

Verify: test search at `http://localhost:8888` or check collection size at
`http://localhost:6333/dashboard`.

---

## Guardrails

| Command | Risk | Always do first |
|---------|------|-----------------|
| `make push-schema` | Writes schema to prod (safe to re-run) | Verify local schema is correct |
| `make pull` | Touches prod, overwrites local DB | `make backup-db` |
| `make enrich-apply` | Writes to Omeka (free, no API) | `--dry-run` preview |
| `make backfill` | Writes to Omeka | `make backfill-dry` |
| `make enrich-batch` | Claude API cost | `make enrich-dry` |
| `make enrich-batch-collect` | Writes to Omeka | `--dry-run` preview |
| `make enrich` (real-time) | Claude API cost | `make enrich-dry` |

Never expose `ANTHROPIC_API_KEY` or Omeka API credentials in output or commits.

## Troubleshooting

- **Batch stuck:** Check `make enrich-batch-status`. If expired (24h timeout),
  re-submit with `make enrich-batch`.
- **Stale cache:** Delete `scripts/.enrich_cache.json` or use `--force` to
  re-analyze. Changing the prompt in `scripts/enrich_metadata.py` requires
  bumping `ANALYSIS_PROMPT_VERSION` (line 197) to auto-invalidate.
- **PATCH failures (422):** Usually resource template validation. The script
  omits `o:resource_template` from payloads to avoid this. Check the error
  response body.
- **Restore from backup:** `make restore-db BACKUP=backups/omeka-XXX.sql.gz`
- **Schema out of sync:** If you modify custom vocabs or the resource template
  locally, run `make push-schema` before the next `make pull` so production
  has the same schema. This is idempotent and safe to re-run.

---
name: enrichment-pipeline
description: Run the enrichment pipeline to pull production data, backfill defaults, and enrich catalog items with Claude OCR and motif detection. Use when asked to enrich, run the pipeline, or process new catalog items.
---

# Enrichment Pipeline

Pull new items from production, fill in missing metadata, and enrich the
catalog with Claude-powered OCR and motif detection.

**Core safety principle:** Every script only fills empty fields — existing
values are never overwritten. You can manually curate any field and the
pipeline will respect your edits on subsequent runs.

See `docs/metadata-mapping.md` for the canonical field definitions, property
IDs, and controlled vocabularies.

---

## Prerequisites

- Docker running (`docker compose version`)
- `ANTHROPIC_API_KEY` set in `.env` (sourced automatically; only needed for step 5)
- SSH access to production host (for pull steps)

## Cost model

Claude analysis results are cached in `scripts/.enrich_cache.json`. Only
`make enrich-batch` (step 5) costs money. All other steps are free.

| Model | Flag | Batch cost (~200 items) |
|-------|------|------------------------|
| Haiku | `--model haiku` | ~$0.50 |
| Sonnet | `--model sonnet` | ~$1.50 |

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

### Step 2 — Pull new items from production

> **Requires user approval.** Touches production server.

```bash
make pull-new
```

This incrementally syncs only items with IDs greater than the local max.
It exports targeted rows from `resource`, `item`, `media`, and `value`
tables, then rsyncs media files (additive). No local data is overwritten.

Output shows how many new items were pulled and the ID range.

If zero new items, you can skip the rest of the pipeline.

### Step 3 — Backfill defaults

```bash
make backfill-dry    # preview first
make backfill        # apply
```

Only touches empty fields on new items. Fills: Location, Creator, Work Type,
Support, Owner, Height/Width, Box, and temp Catalog Number.

Verify: output shows patched count with zero failures.

### Step 4 — Doctor-catalog (pre-enrichment baseline)

```bash
make doctor-catalog
```

Review the Field Summary. Remaining gaps (Medium, Signature, Motifs, Date)
are fields that need Claude analysis.

### Step 5 — Submit enrichment batch

> **Requires user approval.** Calls the Claude API. Costs real money.

```bash
make enrich-batch                # defaults to haiku
```

The script only processes items without cached results, so only new items
are sent to Claude. Images are downloaded, resized to 1024px max, and
submitted in chunks of 500 to the Anthropic Batch API.

### Step 6 — Monitor and collect results

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

### Step 7 — Doctor-catalog (post-enrichment)

```bash
make doctor-catalog
```

Compare to Step 4. Medium, Signature, Motifs, and Date gaps should be
reduced for the new items.

### Step 8 — Re-ingest into Qdrant

Rebuild the search index so it reflects newly enriched metadata and
transcriptions.

```bash
make ingest
```

Verify: test search at `http://localhost:8888` or check collection size at
`http://localhost:6333/dashboard`.

---

## Full reset (disaster recovery)

If the local DB is corrupted or you need a clean slate, use the legacy
full-pull workflow:

```bash
make backup-db           # safety net
make push-schema         # preserve local schema changes
make pull                # full DB wipe + replace from prod
# Visit localhost:8888 — run DB migration if prompted
make ensure-api-key      # re-insert local API key
make enrich-apply        # re-apply cached enrichments (free)
make backfill            # re-apply defaults
```

Then continue from Step 5 above.

## Guardrails

| Command | Risk | Always do first |
|---------|------|-----------------|
| `make pull-new` | Reads from prod (additive) | — |
| `make pull` | Overwrites local DB | `make backup-db` |
| `make push-schema` | Writes schema to prod | Verify local schema |
| `make backfill` | Writes to Omeka | `make backfill-dry` |
| `make enrich-batch` | Claude API cost | `make enrich-dry` |
| `make enrich-batch-collect` | Writes to Omeka | `--dry-run` preview |

Never expose `ANTHROPIC_API_KEY` or Omeka API credentials in output or commits.

## Troubleshooting

- **Batch stuck:** Check `make enrich-batch-status`. If expired (24h timeout),
  re-submit with `make enrich-batch`.
- **Stale cache:** Delete `scripts/.enrich_cache.json` or use `--force` to
  re-analyze. Changing the prompt in `scripts/enrich_metadata.py` requires
  bumping `ANALYSIS_PROMPT_VERSION` to auto-invalidate.
- **PATCH failures (422):** Usually resource template validation. The script
  omits `o:resource_template` from payloads to avoid this. Check the error
  response body.
- **Restore from backup:** `make restore-db BACKUP=backups/omeka-XXX.sql.gz`
- **Schema out of sync:** Run `make push-schema` before `make pull` so
  production has the same schema. Idempotent and safe to re-run. Not needed
  for `make pull-new` (incremental sync preserves local schema).

# Efficiency Opportunities

Notes on batching, parallelism, and performance improvements across the
enrichment pipeline scripts.

---

## Completed

- [x] **`requests.Session` pooling** — `enrich_metadata.py` uses a
  module-level session for all Omeka HTTP calls. Both `backfill_defaults.py`
  and `doctor_catalog.py` import from it, so they benefit automatically.
- [x] **`per_page=500`** — cuts API calls from ~36 to ~8 for 3,600 items.
  All scripts share `get_items_page()` from `enrich_metadata.py`.
- [x] **Parallel `apply_from_cache()`** — 10 workers via ThreadPoolExecutor.
  Brought ~20 min down to ~1.5 min for 3,000 items.
- [x] **`PATCH_WORKERS=10` in backfill** — up from 3, ~3x faster.
- [x] **Double-fetch eliminated** — `_apply_one()` fetches once, builds
  payload from the same item dict.

---

## Remaining opportunities

### Parallel page fetches

All scripts fetch pages sequentially. With `per_page=500` this is only
~8 calls, so the benefit is smaller now (~4s → ~1s). Worth doing if
the item count grows significantly.

### Cache I/O batching (real-time mode)

In real-time mode, `.enrich_cache.json` is rewritten after each item.
Consider flushing every N items or at the end.

### Direct SQL for apply-cache

Instead of HTTP PATCHing through Omeka's REST API, write directly to
the `value` table in MariaDB. 10-100x faster.

**Trade-offs:**
- Bypasses Omeka's validation and event hooks
- Requires cache clear after writes
- Fragile across Omeka version upgrades

Only worth pursuing if the HTTP bottleneck returns at larger scale.

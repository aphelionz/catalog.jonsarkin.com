---
name: push-schema
description: Push local Omeka schema (custom vocabs, resource templates, faceted browse config, item sets) to production. Use when local schema has changed and needs to be synced before pulling.
---

# Push Schema to Production

Sync local-only schema customizations to the production database so that
`make pull` preserves them. This is idempotent and safe to re-run.

**What gets pushed:**
- Custom vocabularies (Work Type, Support, Motifs, Condition, Estate, Signature, Framed)
- Resource templates and their property configurations
- Faceted browse pages, categories, facets, and columns
- Item sets (collections) — resource records, titles, and site assignments

**What does NOT get pushed:** Item data, media, values (except item set titles), users, item-to-collection memberships.

---

## Prerequisites

- Docker running with the `db` container healthy
- SSH access to production host

## Steps

### Step 1 — Verify local schema looks correct

Spot-check that vocabs, template, and item sets are as expected:

```bash
docker compose exec -T db mariadb -uomeka -pomeka omeka \
  -e "SELECT id, label FROM custom_vocab; \
      SELECT COUNT(*) AS template_2_props FROM resource_template_property WHERE resource_template_id = 2; \
      SELECT COUNT(*) AS item_sets FROM item_set;"
```

Expected: 7 custom vocabs, 25 template 2 properties, 25 item sets.

### Step 2 — Push to production

> **Requires user approval.** Writes to the production database.

```bash
make push-schema
```

The command exports the schema tables with `REPLACE INTO` (upserts), cleans
up orphan template properties, pushes via SSH, and prints verification counts.

Item set tables are wrapped in `SET FOREIGN_KEY_CHECKS = 0/1` to prevent
CASCADE DELETE from wiping `item_item_set` memberships.

### Step 3 — Verify

Output should show:
- `custom_vocabs: 7`
- `template_2_props: 25`
- `site_pages: 8`
- `item_sets: 25`

---

## When to use

| Scenario | Action |
|----------|--------|
| Modified custom vocabs locally | Push before next `make pull` |
| Changed resource template properties | Push before next `make pull` |
| Updated faceted browse config | Push before next `make pull` |
| Created/modified item sets locally | Push before next `make pull` |
| First-time setup of a fresh production DB | Push once to seed schema |

## Notes

- Only the schema tables are touched — item data is never modified.
- Uses `REPLACE INTO` so existing rows are updated, new rows are inserted.
- Orphan template 2 properties on prod are cleaned up via a DELETE before insert.
- Item set push uses `SET FOREIGN_KEY_CHECKS = 0` to avoid cascading deletes to `item_item_set` (collection memberships).
- Item set `thumbnail_id` references are NULLed on push — asset files are local-only and don't exist on production.
- Run `make backup-db` on local before pushing if you want a rollback point.

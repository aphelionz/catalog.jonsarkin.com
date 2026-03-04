---
name: push-schema
description: Push local Omeka schema (custom vocabs, resource templates, faceted browse config) to production. Use when local schema has changed and needs to be synced before pulling.
---

# Push Schema to Production

Sync local-only schema customizations to the production database so that
`make pull` preserves them. This is idempotent and safe to re-run.

**What gets pushed:**
- Custom vocabularies (Work Type, Support, Motifs, Condition, Estate)
- Resource templates and their property configurations
- Faceted browse pages, categories, facets, and columns

**What does NOT get pushed:** Item data, media, values, users, sites.

---

## Prerequisites

- Docker running with the `db` container healthy
- SSH access to production host

## Steps

### Step 1 — Verify local schema looks correct

Spot-check that vocabs and template are as expected:

```bash
docker compose exec -T db mariadb -uomeka -pomeka omeka \
  -e "SELECT id, label FROM custom_vocab; \
      SELECT COUNT(*) AS template_2_props FROM resource_template_property WHERE resource_template_id = 2;"
```

Expected: 5 custom vocabs, 25 template 2 properties.

### Step 2 — Push to production

> **Requires user approval.** Writes to the production database.

```bash
make push-schema
```

The command exports the schema tables with `REPLACE INTO` (upserts), cleans
up orphan template properties, pushes via SSH, and prints verification counts.

### Step 3 — Verify

Output should show:
- `custom_vocabs: 5`
- `template_2_props: 25`

---

## When to use

| Scenario | Action |
|----------|--------|
| Modified custom vocabs locally | Push before next `make pull` |
| Changed resource template properties | Push before next `make pull` |
| Updated faceted browse config | Push before next `make pull` |
| First-time setup of a fresh production DB | Push once to seed schema |

## Notes

- Only the schema tables are touched — item data is never modified.
- Uses `REPLACE INTO` so existing rows are updated, new rows are inserted.
- Orphan template 2 properties on prod are cleaned up via a DELETE before insert.
- Run `make backup-db` on local before pushing if you want a rollback point.

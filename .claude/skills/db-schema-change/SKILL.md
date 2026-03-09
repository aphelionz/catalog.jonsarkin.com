---
name: db-schema-change
description: Make direct MariaDB schema or data changes (faceted browse config, item sets, nav, custom vocabs). Use when asked to modify facets, add item sets, change navigation, or update DB-stored configuration.
---

# DB Schema Change

Direct MariaDB changes to Omeka configuration tables — faceted browse
facets, item sets, navigation, custom vocabularies.

---

## Prerequisites

- Docker running with the `db` container healthy

## Steps

### Step 1 — Backup

> **Always backup before DB changes.**

```bash
make backup-db
```

### Step 2 — Query current state

```bash
docker compose exec -T db mariadb -u root -proot omeka -e "YOUR QUERY HERE"
```

Use `root/root` for admin operations. Use `omeka/omeka` only for read-only queries if needed.

### Step 3 — Make changes

Write and execute your SQL.

**Critical: JSON in SQL heredocs** — use `\\n` (double backslash) for newlines
in JSON strings. Single `\n` produces literal newlines → 500 errors.

Example (correct):
```bash
docker compose exec -T db mariadb -u root -proot omeka <<'SQL'
UPDATE faceted_browse_facet SET data = '{"property_id":8,"values":["Drawing","Painting"]}'
WHERE id = 5;
SQL
```

### Step 4 — Verify

Re-query the changed rows:
```bash
docker compose exec -T db mariadb -u root -proot omeka -e "SELECT ... FROM ..."
```

Test in browser — if changes don't appear, restart the Omeka container:
```bash
docker compose restart omeka
```
(Doctrine cache may serve stale data.)

### Step 5 — Push schema to production

> **Requires user approval.**

```bash
make push-schema
```

Only needed if the change affects schema tables (custom vocabs, resource
templates, faceted browse config, item sets). Not needed for item data changes.

---

## Common tables

| Table | Purpose |
|---|---|
| `faceted_browse_page` | Browse page definitions |
| `faceted_browse_category` | Category (tab) configs |
| `faceted_browse_facet` | Individual facet definitions (JSON `data` column) |
| `faceted_browse_column` | Column display configs |
| `custom_vocab` | Controlled vocabulary definitions |
| `resource_template` | Resource templates |
| `resource_template_property` | Template ↔ property mappings |
| `site_page` | Pages including navigation |
| `item_set` | Item sets (collections) |
| `navigation` | Site navigation JSON |

## Gotchas

- **JSON `\n` escaping** — double-backslash in heredocs
- **Doctrine cache** — restart `omeka` container if changes don't appear
- **Faceted browse facet positions** — facets have a `position` column; new facets need a position and may require shifting existing ones
- **Item set push** — uses `SET FOREIGN_KEY_CHECKS = 0` to avoid cascade deletes

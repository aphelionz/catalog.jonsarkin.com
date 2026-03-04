---
name: restore-backup
description: Restore the local Omeka database from a backup file. Use when a pull, enrichment, or other operation corrupted the local DB and you need to roll back.
---

# Restore Backup

Roll back the local Omeka database to a previous backup. Use this after a
failed enrichment, bad production pull, or any operation that corrupted
local-only data (custom vocabularies, resource templates, etc.).

**Only the database is restored.** Uploaded files (images) are additive via
rsync and don't need rollback.

---

## Prerequisites

- Docker running with the `db` container healthy
- At least one backup in `backups/` (created by `make backup-db`)

## Steps

### Step 1 — List available backups

```bash
make restore-db
```

Running without a `BACKUP=` argument lists available backups with timestamps
and sizes. Choose the one to restore.

### Step 2 — Restore

> **Requires user approval.** Overwrites the local database.

```bash
make restore-db BACKUP=backups/omeka-YYYYMMDD-HHMMSS.sql.gz
```

The command prints the restored item count for verification.

### Step 3 — Verify

Confirm the database looks correct:

```bash
docker compose exec -T db mariadb -uomeka -pomeka omeka \
  -e "SELECT COUNT(*) AS items FROM item;"
```

Optionally run `make doctor-catalog` to check field coverage.

---

## When to use

| Scenario | Action |
|----------|--------|
| `make pull` overwrote local customizations | Restore the backup taken before the pull |
| Enrichment wrote bad data | Restore, then re-run `make enrich-apply` from cache |
| Backfill applied wrong defaults | Restore the pre-backfill backup |
| Just want to start fresh | Restore any known-good backup |

## Tips

- Always run `make backup-db` before destructive operations (the enrichment
  pipeline does this automatically in Step 2).
- Backups are cheap (~2 MB compressed). Keep several around.
- After restoring, you may want to re-apply cached enrichments with
  `make enrich-apply` — this is free (no API cost).

# Recovery runbook

Paste-in-order commands for getting the catalog back from `gs://catalog-jonsarkin-backups/`. Timings are from the first restore drill, 2026-10-01, against the `monthly/omeka-20261001T050001Z.sql.gz` dump (27.5 MB gzipped, 276 MB of SQL).

## What the backups contain

| Path in bucket | Source on VM | Cadence |
|---|---|---|
| `daily/` | `mariadb-dump --all-databases` (includes the `mysql` grants DB) | 02:00 UTC, 30 kept |
| `weekly/` | same | Sun 04:00 UTC |
| `monthly/` | same | 1st, 05:00 UTC |
| `files/` | `/var/www/omeka-s/files/` (minus `tmp/`) | 03:00 UTC rsync, append-only, versioned |

Outside the bucket, so plan for them separately:

- `/opt/catalog/.env`: DB passwords, `ANTHROPIC_API_KEY`, SMTP creds. Keep a copy in a password manager. Template: `.env.prod.example`.
- Qdrant vectors and the SQLite search index: rebuild path still undocumented (see step 5).
- `acme.json`: Traefik re-issues the TLS cert on first start.
- Code: this repo. `/opt/catalog` is a checkout of it plus `make deploy` rsyncs.

## Auth

Local `gcloud` usually fails non-interactively. Run bucket commands **on the VM** through its impersonation:

```bash
ssh omeka.us-east1-b.folkloric-rite-468520-r2
G="gcloud storage --impersonate-service-account=catalog-backups@folkloric-rite-468520-r2.iam.gserviceaccount.com"
```

On a brand-new VM that has no impersonation yet, run `gcloud auth login` there interactively first.

## 1. Pick and fetch the dump (5 s)

```bash
$G ls -l gs://catalog-jonsarkin-backups/daily/ | tail -3
D=/tmp/restore; mkdir -p $D
$G cp gs://catalog-jonsarkin-backups/daily/omeka-YYYYMMDDTHHMMSSZ.sql.gz $D/
```

Use the newest `daily/` unless the problem is bad data that is already in it; then walk back through `daily/`, `weekly/`, `monthly/`.

## 2. Rehearse in a scratch DB first (10 s)

Prove the dump in a throwaway container before it goes anywhere near prod.

```bash
docker run -d --name restore-scratch -e MARIADB_ROOT_PASSWORD=scratch mariadb:10.11
until docker exec restore-scratch healthcheck.sh --connect; do sleep 1; done
gunzip -c $D/omeka-*.sql.gz | docker exec -i -e MYSQL_PWD=scratch restore-scratch mariadb -uroot
docker exec -e MYSQL_PWD=scratch restore-scratch mariadb -uroot -N -e \
  "SELECT 'item',COUNT(*) FROM omeka.item UNION ALL SELECT 'media',COUNT(*) FROM omeka.media
   UNION ALL SELECT 'value',COUNT(*) FROM omeka.value UNION ALL SELECT 'resource',COUNT(*) FROM omeka.resource;"
```

Container start took 3 s and the import 7 s on a laptop; budget a few minutes on the 2-vCPU VM. The drill reported 6,782 items, 6,358 media, 13,156 resources. Numbers in that range mean the dump is sane.

Gotcha: the dump carries the `mysql` grants DB, so after the import (or a restart) the scratch root password becomes **prod's** `MYSQL_ROOT_PASSWORD`. The `scratch` password works only until the container restarts.

```bash
docker rm -f restore-scratch
```

## 3. Restore into prod (minutes)

```bash
cd /opt/catalog
make backup-db 2>/dev/null || true   # snapshot whatever is there now, even if broken
docker compose -f docker-compose.prod.yml stop omeka
gunzip -c $D/omeka-*.sql.gz | docker compose -f docker-compose.prod.yml exec -T db \
  sh -c 'MYSQL_PWD=$MARIADB_ROOT_PASSWORD mariadb -uroot'
docker compose -f docker-compose.prod.yml restart db
docker compose -f docker-compose.prod.yml start omeka
```

On a fresh VM with an empty `dbdata` volume, `docker compose -f docker-compose.prod.yml up -d db` first, wait for healthy, then import.

## 4. Restore files (about 15 min for everything)

Spot check first. In the drill, 6 files (four 4 MB JPEGs, a 204 MB MP4, a thumbnail) came back in 20 s, all SHA-256 identical to the live copies.

```bash
f=original/<hash>.jpeg
$G cp gs://catalog-jonsarkin-backups/files/$f /tmp/check.jpeg
sha256sum /var/www/omeka-s/files/$f /tmp/check.jpeg
```

Full restore, 34.4 GB at the observed ~42 MiB/s:

```bash
sudo mkdir -p /var/www/omeka-s/files && sudo chown -R $USER /var/www/omeka-s/files
$G rsync --recursive gs://catalog-jonsarkin-backups/files/ /var/www/omeka-s/files/
sudo chown -R 33:33 /var/www/omeka-s/files   # www-data inside the omeka container
```

The bucket holds about 600 more objects than the live tree (files deleted on the VM stay in the bucket by design). Restoring them is harmless; Omeka ignores files no media row points to.

## 5. Bring it up and verify

> [!question] Undrilled: how prod Qdrant and the search index get rebuilt. `docker-compose.prod.yml` has no `ingest` service, and `/opt/catalog` holds a hand-made `qdrant-omeka_items-20260309.snapshot`. Until this is written down, similarity search and full-text may be empty after a VM rebuild while the catalog itself works.

```bash
docker compose -f docker-compose.prod.yml up -d
curl -4 -sS -o /dev/null -w "%{http_code}\n" https://catalog.jonsarkin.com/api/items   # expect 200
curl -4 -sS -o /dev/null -w "%{http_code}\n" https://catalog.jonsarkin.com/            # expect 301
```

If `/api/items` returns HTML, visit `/admin` and click "Update database".

## Drill log

| Date | Dump | Result |
|---|---|---|
| 2026-10-01 | `monthly/omeka-20261001T050001Z.sql.gz` | Imported clean into scratch `mariadb:10.11`. `item`, `media`, `resource` matched prod exactly; `value` was 172 short, all 172 on the 34 resources edited after the 05:00 dump. 6 sampled files byte-identical. |

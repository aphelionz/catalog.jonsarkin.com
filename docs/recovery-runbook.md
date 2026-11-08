# Recovery runbook

Paste-in-order commands for getting the catalog back from `gs://catalog-jonsarkin-backups/`. Timings are from the first restore drill, 2026-10-01, against the `monthly/omeka-20261001T050001Z.sql.gz` dump (27.5 MB gzipped, 276 MB of SQL).

## What the backups contain

| Path in bucket | Source on VM | Cadence |
|---|---|---|
| `daily/` | `mariadb-dump --all-databases` (includes the `mysql` grants DB; `omeka.session` schema only) | 02:00 UTC, 30 kept |
| `weekly/` | same | Sun 04:00 UTC |
| `weekly/state/<stamp>/` | `qdrant-omeka_items.snapshot`, `search-index.sqlite` (clip-api FTS), `env` (`/opt/catalog/.env`) | Sun, after the weekly dump |
| `monthly/` | same as `daily/` | 1st, 05:00 UTC |
| `access-logs/<YYYY-MM>-<container>.log.gz` | Traefik access log for the month just ended, from the container's stdout. Analytics archive, outside the recovery path. Before recreating the traefik container, run `backup-to-gcs.sh accesslog <current YYYY-MM>` or the month so far is lost. | 1st, with the monthly dump; kept indefinitely |
| `files/` | `/var/www/omeka-s/files/` (minus `tmp/`) | 03:00 UTC rsync, append-only, versioned |

Outside the bucket:

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
docker run -d --name restore-scratch -e MARIADB_ROOT_PASSWORD=scratch catalog-mariadb:11.8   # docker compose build db
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

Every backup from before the 2026-11 rebuild comes from the VM treated as compromised, and its `mysql` tables hold that VM's DB users and passwords. Restore one only through [prod-rebuild-runbook.md](prod-rebuild-runbook.md) step 5: the scratch import with `--one-database`, the audit queries, the re-export and the app-user import.

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

## 5. Restore state: .env, Qdrant, search index

The 2026-10-02 state backup was downloaded and checked (snapshot is a valid tar, index passes `integrity_check` with row counts equal to live, `env` byte-identical). The recover commands below have not been run against a live Qdrant yet.

```bash
S=$($G ls gs://catalog-jonsarkin-backups/weekly/state/ | tail -1)   # newest stamp
$G cp "${S}*" $D/
cp $D/env /opt/catalog/.env && chmod 600 /opt/catalog/.env      # before `up` on a fresh VM
docker compose -f docker-compose.prod.yml up -d qdrant clip-api
docker compose -f docker-compose.prod.yml cp $D/qdrant-omeka_items.snapshot qdrant:/qdrant/snapshots/restore.snapshot
docker compose -f docker-compose.prod.yml exec -T clip-api python -c "import urllib.request as u; print(u.urlopen(u.Request('http://qdrant:6333/collections/omeka_items/snapshots/recover?wait=true', data=b'{\"location\":\"file:///qdrant/snapshots/restore.snapshot\"}', headers={'Content-Type':'application/json'}, method='PUT')).read())"
docker compose -f docker-compose.prod.yml cp $D/search-index.sqlite clip-api:/app/search_index/index.sqlite
docker compose -f docker-compose.prod.yml restart clip-api
```

The 2026-10-02 snapshot held 6,109 points (90 MB) and the index 1,280 rows; the whole state backup takes about 20 s.

## 6. Bring it up and verify

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
| 2026-10-02 | `weekly/state/20261002T041008Z/` | First state backup. Snapshot tar valid, index `integrity_check` ok with counts equal to live, `env` identical. Recover step not run. |

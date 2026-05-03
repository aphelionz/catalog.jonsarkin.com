#!/usr/bin/env bash
#
# backup-to-gcs.sh — Daily catalog backup to Google Cloud Storage.
#
# Runs ON THE VM (not locally). Dumps the Omeka MariaDB and rsyncs
# /var/www/omeka-s/files/ to gs://catalog-jonsarkin-backups/.
#
# Auth: uses the VM's default Compute service account to impersonate
# catalog-backups@folkloric-rite-468520-r2.iam.gserviceaccount.com.
# Requires the VM to have cloud-platform access scope and the default
# Compute SA to hold roles/iam.serviceAccountTokenCreator on the
# catalog-backups SA.
#
# Usage:
#   backup-to-gcs.sh [daily|weekly|monthly|files|all]
#
#     daily    DB dump → gs://.../daily/  (default; lifecycle deletes after 30d)
#     weekly   DB dump → gs://.../weekly/ (lifecycle: nearline @ 30d, delete @ 365d)
#     monthly  DB dump → gs://.../monthly/ (lifecycle: coldline @ 60d, no auto-delete)
#     files    Files rsync → gs://.../files/ (versioned bucket, no --delete)
#     all      daily + files
#
# Cron schedule (mark's crontab):
#   0 2 * * *  /opt/catalog/scripts/backup-to-gcs.sh daily   >> /var/log/catalog-backup.log 2>&1
#   0 3 * * *  /opt/catalog/scripts/backup-to-gcs.sh files   >> /var/log/catalog-backup.log 2>&1
#   0 4 * * 0  /opt/catalog/scripts/backup-to-gcs.sh weekly  >> /var/log/catalog-backup.log 2>&1
#   0 5 1 * *  /opt/catalog/scripts/backup-to-gcs.sh monthly >> /var/log/catalog-backup.log 2>&1
#
set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────
PROJECT_ID="folkloric-rite-468520-r2"
BUCKET="gs://catalog-jonsarkin-backups"
SA="catalog-backups@${PROJECT_ID}.iam.gserviceaccount.com"
COMPOSE_DIR="/opt/catalog"
ENV_FILE="${COMPOSE_DIR}/.env"
FILES_DIR="/var/www/omeka-s/files"
LOG_FILE="/var/log/catalog-backup.log"
LOCK_FILE="/var/lock/catalog-backup.lock"

# ── Helpers ────────────────────────────────────────────────────────────
log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

die() {
  log "FATAL: $*"
  exit 1
}

gcs() {
  gcloud storage --impersonate-service-account="$SA" "$@"
}

# Single-flight: refuse to run if another instance is active.
exec 200>"$LOCK_FILE"
flock -n 200 || { log "Another backup in progress; exiting"; exit 0; }

# ── Subcommands ────────────────────────────────────────────────────────

cmd_db() {
  local prefix="$1"
  local stamp; stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local tmpdir; tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN
  local out="${tmpdir}/omeka-${stamp}.sql.gz"

  [ -r "$ENV_FILE" ] || die "Cannot read ${ENV_FILE}"
  local root_pw; root_pw="$(grep -E '^MYSQL_ROOT_PASSWORD=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'"'')"
  [ -n "$root_pw" ] || die "MYSQL_ROOT_PASSWORD not found in ${ENV_FILE}"

  log "DB dump start (prefix=${prefix})"
  cd "$COMPOSE_DIR"
  docker compose exec -T -e "MYSQL_PWD=${root_pw}" db \
    mariadb-dump --user=root \
                 --single-transaction --quick \
                 --routines --triggers --events \
                 --hex-blob \
                 --all-databases \
    | gzip -9 > "$out"

  local size; size="$(stat -c %s "$out")"
  log "DB dump complete: $(basename "$out") (${size} bytes)"

  log "DB upload to ${BUCKET}/${prefix}/"
  gcs cp "$out" "${BUCKET}/${prefix}/$(basename "$out")"
  log "DB upload complete"
}

cmd_files() {
  log "Files rsync start (${FILES_DIR} → ${BUCKET}/files/)"
  # No --delete-unmatched-destination-objects: bucket is append-mostly,
  # versioning catches accidental overwrites.
  # Exclude tmp/: Omeka's PHP temp files are owned by the container's
  # www-data user and unreadable from the host (matches pull_new_items.sh).
  gcs rsync --recursive --exclude='^tmp/.*' "$FILES_DIR" "${BUCKET}/files/"
  log "Files rsync complete"
}

# ── Entrypoint ─────────────────────────────────────────────────────────
{
  case "${1:-daily}" in
    daily)   cmd_db daily ;;
    weekly)  cmd_db weekly ;;
    monthly) cmd_db monthly ;;
    files)   cmd_files ;;
    all)     cmd_db daily; cmd_files ;;
    *)       echo "usage: $0 [daily|weekly|monthly|files|all]" >&2; exit 1 ;;
  esac
} 2>&1 | tee -a "$LOG_FILE"

#!/usr/bin/env bash
#
# backup-monitor.sh — Watchdog that reports backup freshness to Cloud Monitoring.
#
# Runs ON THE VM, hourly. Reads the per-job success stamps that
# backup-to-gcs.sh drops in /var/lib/catalog-backup/ and publishes one
# gauge per job:
#
#   custom.googleapis.com/catalog/backup_age_hours{job="daily|weekly|monthly|files"}
#   custom.googleapis.com/catalog/monitor_heartbeat
#
# Alert policies then threshold on backup_age_hours (see
# setup-gcp-monitoring.sh). This is deliberately a threshold on an age
# gauge rather than a metric-absence condition on a success counter:
# Cloud Monitoring caps absence durations at 24 hours, which cannot
# express "the weekly backup is 8 days late". The heartbeat metric is
# the one thing watched by absence, so a dead watchdog is still loud.
#
# Auth: the VM's default Compute service account, which holds
# roles/monitoring.metricWriter at the project level.
#
# Usage:
#   backup-monitor.sh            report ages to Cloud Monitoring
#   backup-monitor.sh bootstrap  seed missing stamps from the bucket, then report
#
# Cron (mark's crontab):
#   17 * * * * /opt/catalog/scripts/backup-monitor.sh >> /var/log/catalog-backup.log 2>&1
#
set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────
PROJECT_ID="folkloric-rite-468520-r2"
BUCKET="gs://catalog-jonsarkin-backups"
SA="catalog-backups@${PROJECT_ID}.iam.gserviceaccount.com"
STATE_DIR="/var/lib/catalog-backup"
JOBS="daily weekly monthly files"
LOG_FILE="/var/log/catalog-backup.log"

# ── Helpers ────────────────────────────────────────────────────────────
log() {
  printf '[%s] monitor: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

gcs() {
  gcloud storage --impersonate-service-account="$SA" "$@"
}

# Newest object timestamp under a prefix, as epoch seconds. Empty if none.
newest_in_prefix() {
  local prefix="$1"
  gcs ls --long "${BUCKET}/${prefix}/" 2>/dev/null \
    | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' \
    | sort | tail -1 \
    | { read -r ts && date -u -d "$ts" +%s; } || true
}

# Timestamp of the last line in the backup log matching a pattern, as epoch
# seconds. Empty if the log has no such line.
last_log_event() {
  local pattern="$1"
  grep -F "$pattern" "$LOG_FILE" 2>/dev/null | tail -1 \
    | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' \
    | { read -r ts && date -u -d "$ts" +%s; } || true
}

# Seed any missing stamp so a fresh install does not report a spurious
# "never succeeded" on its first pass.
#
# The DB prefixes seed from the newest object in the bucket, which is exact.
# files/ must NOT: the rsync is delta-based, so a run where nothing changed
# uploads no object and the newest-object age keeps climbing even though the
# job is healthy. Seed that one from the log instead, which records the run
# itself rather than its side effect.
bootstrap() {
  mkdir -p "$STATE_DIR"
  for job in $JOBS; do
    local stamp="${STATE_DIR}/last-success-${job}" epoch source
    [ -f "$stamp" ] && continue
    if [ "$job" = "files" ]; then
      epoch="$(last_log_event 'Files rsync complete')"; source="log"
    else
      epoch="$(newest_in_prefix "$job")"; source="bucket"
    fi
    if [ -n "$epoch" ]; then
      touch -d "@${epoch}" "$stamp"
      log "bootstrapped ${job} from ${source} ($(date -u -d "@${epoch}" +%Y-%m-%dT%H:%M:%SZ))"
    else
      log "WARN: no ${source} evidence for ${job}, leaving it unstamped"
    fi
  done
}

# ── Metric publishing ──────────────────────────────────────────────────

# Build one timeSeries entry. $1=metric type suffix, $2=label json, $3=value
series() {
  local type="$1" labels="$2" value="$3"
  cat <<EOF
{
  "metric": {"type": "custom.googleapis.com/catalog/${type}", "labels": ${labels}},
  "resource": {"type": "global", "labels": {"project_id": "${PROJECT_ID}"}},
  "points": [{
    "interval": {"endTime": "${NOW_RFC3339}"},
    "value": {"doubleValue": ${value}}
  }]
}
EOF
}

main() {
  [ "${1:-}" = "bootstrap" ] && bootstrap

  NOW_EPOCH="$(date -u +%s)"
  NOW_RFC3339="$(date -u -d "@${NOW_EPOCH}" +%Y-%m-%dT%H:%M:%SZ)"

  local entries=()
  for job in $JOBS; do
    local stamp="${STATE_DIR}/last-success-${job}"
    if [ ! -f "$stamp" ]; then
      log "WARN: no stamp for ${job}; reporting age 9999h"
      entries+=("$(series backup_age_hours "{\"job\": \"${job}\"}" 9999)")
      continue
    fi
    local mtime age
    mtime="$(stat -c %Y "$stamp")"
    age="$(awk "BEGIN { printf \"%.3f\", (${NOW_EPOCH} - ${mtime}) / 3600 }")"
    entries+=("$(series backup_age_hours "{\"job\": \"${job}\"}" "$age")")
    log "${job} age ${age}h"
  done
  entries+=("$(series monitor_heartbeat '{}' 1)")

  local payload
  payload="$(printf '{"timeSeries": [%s]}' "$(IFS=,; echo "${entries[*]}")")"

  local token; token="$(gcloud auth print-access-token)"
  local code
  code="$(curl -sS -o /tmp/monitor-resp.json -w '%{http_code}' \
    -X POST "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/timeSeries" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d "$payload")"

  if [ "$code" = "200" ]; then
    log "published $((${#entries[@]})) time series"
  else
    log "FATAL: timeSeries.create returned HTTP ${code}: $(cat /tmp/monitor-resp.json)"
    exit 1
  fi
}

{ main "$@"; } 2>&1 | tee -a "$LOG_FILE"

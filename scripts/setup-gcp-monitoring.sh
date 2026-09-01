#!/usr/bin/env bash
#
# setup-gcp-monitoring.sh — Idempotent Cloud Monitoring setup for the catalog.
#
# Creates (or updates in place) the notification channel, backup dead-man's
# switch alerts, host alerts, and uptime checks for catalog.jonsarkin.com.
# Safe to re-run: every object is matched by displayName and PATCHed rather
# than duplicated.
#
# Runs FROM A WORKSTATION with an account holding roles/monitoring.editor.
# Everything goes through the Monitoring v3 REST API rather than gcloud
# subcommands, so it does not depend on which gcloud components happen to be
# installed. To move this to another project or Google account, change
# PROJECT_ID and ALERT_EMAILS below and re-run; nothing else is environment
# specific.
#
# Companion pieces on the VM:
#   backup-to-gcs.sh    writes /var/lib/catalog-backup/last-success-<job>
#   backup-monitor.sh   hourly, turns those stamps into catalog/backup_age_hours
#
# Usage:
#   scripts/setup-gcp-monitoring.sh          create or update everything
#   scripts/setup-gcp-monitoring.sh --dry    print what would change
#
set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────
PROJECT_ID="folkloric-rite-468520-r2"
ALERT_EMAILS="mark@fishcitystudios.com"   # space separated for more
HOST="catalog.jonsarkin.com"
VM_NAME="omeka"

# The Ops Agent labels disk metrics by underlying device, not mount point, so
# this cannot be "/dev/root". It must also be pinned to the real root device:
# the agent also reports every /dev/loop* snap mount, and those sit at 100%
# used by nature, so an unfiltered "disk above 75%" alert pages forever.
# To find it on a rebuilt VM: df / then match against the devices in
# agent.googleapis.com/disk/percent_used.
ROOT_DEVICE="/dev/sda1"

# Staleness thresholds, in hours. Each is the job's period plus headroom.
STALE_DAILY=26
STALE_FILES=26
STALE_WEEKLY=192     # 8 days
STALE_MONTHLY=792    # 33 days
HEARTBEAT_ABSENT=10800   # seconds; watchdog runs hourly, alert after 3 misses
DISK_PCT=75

API="https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}"
DRY=false; [ "${1:-}" = "--dry" ] && DRY=true

# ── Helpers ────────────────────────────────────────────────────────────
# Progress goes to stderr: upsert_channel and upsert_uptime return their
# resource id on stdout, so anything else there corrupts the capture.
log() { printf '  %s\n' "$*" >&2; }
die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

TOKEN="$(gcloud auth print-access-token 2>/dev/null)" || die "gcloud auth print-access-token failed; run: gcloud auth login"

api() {
  local method="$1" url="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" "$url" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" -d "$body"
  else
    curl -sS -X "$method" "$url" -H "Authorization: Bearer ${TOKEN}"
  fi
}

# Fail loudly on an API error rather than silently continuing.
check_err() {
  local resp="$1" what="$2"
  if echo "$resp" | jq -e '.error' >/dev/null 2>&1; then
    die "${what}: $(echo "$resp" | jq -r '.error.message')"
  fi
}

# ── Metric descriptors ─────────────────────────────────────────────────
# Declare the custom metrics up front. Writing a point auto-creates a
# descriptor, but an alert policy cannot reference a metric type that has
# never been seen, so on a fresh project the policies below would fail
# until the VM happened to report. Declaring them makes the order safe.
upsert_descriptor() {
  local type="$1" body="$2" existing resp
  existing="$(api GET "${API}/metricDescriptors/${type}" | jq -r '.type // empty')"
  if [ -n "$existing" ]; then log "descriptor exists: ${type}"; return; fi
  if $DRY; then log "[dry] would create descriptor ${type}"; return; fi
  resp="$(api POST "${API}/metricDescriptors" "$body")"
  check_err "$resp" "create descriptor ${type}"
  log "descriptor created: ${type}"
}

# ── Notification channel ───────────────────────────────────────────────
upsert_channel() {
  local email="$1" name
  name="$(api GET "${API}/notificationChannels" \
    | jq -r --arg e "$email" '.notificationChannels[]? | select(.labels.email_address==$e) | .name' | head -1)"

  if [ -n "$name" ]; then
    log "channel exists: ${email}"
  else
    local body resp
    body="$(jq -n --arg e "$email" '{
      type: "email", displayName: ("Catalog alerts: " + $e),
      labels: {email_address: $e}, enabled: true
    }')"
    if $DRY; then log "[dry] would create channel ${email}"; echo ""; return; fi
    resp="$(api POST "${API}/notificationChannels" "$body")"
    check_err "$resp" "create channel ${email}"
    name="$(echo "$resp" | jq -r '.name')"
    log "channel created: ${email}"
  fi
  echo "$name"
}

# ── Alert policies ─────────────────────────────────────────────────────
# upsert_policy <displayName> <policy-json>
upsert_policy() {
  local display="$1" policy="$2" existing resp
  # The lookup key is the policy displayName, so force it to match rather
  # than trusting the caller's JSON: threshold_policy names itself after the
  # condition, and a mismatch here silently creates a duplicate on every run
  # instead of updating in place.
  policy="$(echo "$policy" | jq --arg d "$display" '.displayName = $d')"
  existing="$(api GET "${API}/alertPolicies" \
    | jq -r --arg d "$display" '.alertPolicies[]? | select(.displayName==$d) | .name' | head -1)"

  if $DRY; then
    log "[dry] would $([ -n "$existing" ] && echo update || echo create): ${display}"
    return
  fi

  if [ -n "$existing" ]; then
    resp="$(api PATCH "https://monitoring.googleapis.com/v3/${existing}?updateMask=conditions,notificationChannels,documentation,alertStrategy,combiner" "$policy")"
    check_err "$resp" "update policy ${display}"
    log "updated: ${display}"
  else
    resp="$(api POST "${API}/alertPolicies" "$policy")"
    check_err "$resp" "create policy ${display}"
    log "created: ${display}"
  fi
}

# threshold_policy <display> <filter> <threshold> <doc>
threshold_policy() {
  jq -n \
    --arg d "$1" --arg f "$2" --argjson t "$3" --arg doc "$4" \
    --argjson ch "$CHANNELS_JSON" '{
    displayName: $d,
    combiner: "OR",
    conditions: [{
      displayName: $d,
      conditionThreshold: {
        filter: $f,
        comparison: "COMPARISON_GT",
        thresholdValue: $t,
        duration: "300s",
        trigger: {count: 1},
        aggregations: [{alignmentPeriod: "600s", perSeriesAligner: "ALIGN_MAX"}]
      }
    }],
    notificationChannels: $ch,
    alertStrategy: {autoClose: "604800s"},
    documentation: {content: $doc, mimeType: "text/markdown"}
  }'
}

# uptime_policy <display> <check_id> <doc>
#
# Fires when more than two probe locations report a failure in the same
# window, which is the shape the pre-existing "Catalog down" policy already
# uses. A naive "fraction of successful checks < 1" alerts on any single
# location blipping, and on the ramp-up of a newly created check, so it pages
# for a site that is fine.
uptime_policy() {
  jq -n --arg d "$1" --arg id "$2" --arg doc "$3" --argjson ch "$CHANNELS_JSON" '{
    displayName: $d,
    combiner: "OR",
    conditions: [{
      displayName: "Uptime check failing from more than 2 regions",
      conditionThreshold: {
        filter: ("metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"" + $id + "\""),
        comparison: "COMPARISON_GT",
        thresholdValue: 2,
        duration: "300s",
        trigger: {count: 1},
        aggregations: [{
          alignmentPeriod: "300s",
          perSeriesAligner: "ALIGN_NEXT_OLDER",
          crossSeriesReducer: "REDUCE_COUNT_FALSE",
          groupByFields: ["resource.label.host"]
        }]
      }
    }],
    notificationChannels: $ch,
    alertStrategy: {autoClose: "604800s"},
    documentation: {content: $doc, mimeType: "text/markdown"}
  }'
}

# ── Uptime checks ──────────────────────────────────────────────────────
# upsert_uptime <displayName> <path> <accepted-status-json>  -> echoes check_id
upsert_uptime() {
  local display="$1" path="$2" codes="$3" existing body resp
  existing="$(api GET "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/uptimeCheckConfigs" \
    | jq -r --arg d "$display" '.uptimeCheckConfigs[]? | select(.displayName==$d) | .name' | head -1)"

  body="$(jq -n --arg d "$display" --arg h "$HOST" --arg p "$path" \
              --arg proj "$PROJECT_ID" --argjson codes "$codes" '{
    displayName: $d,
    monitoredResource: {type: "uptime_url", labels: {host: $h, project_id: $proj}},
    httpCheck: {
      path: $p, port: 443, useSsl: true, validateSsl: true,
      requestMethod: "GET", acceptedResponseStatusCodes: $codes
    },
    period: "300s", timeout: "10s"
  }')"

  if $DRY; then
    log "[dry] would $([ -n "$existing" ] && echo update || echo create) uptime: ${display}"
    echo ""; return
  fi

  if [ -n "$existing" ]; then
    resp="$(api PATCH "https://monitoring.googleapis.com/v3/${existing}?updateMask=httpCheck,period,timeout" "$body")"
    check_err "$resp" "update uptime ${display}"
    log "uptime updated: ${display}"
  else
    resp="$(api POST "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/uptimeCheckConfigs" "$body")"
    check_err "$resp" "create uptime ${display}"
    log "uptime created: ${display}"
  fi
  echo "$resp" | jq -r '.name' | awk -F/ '{print $NF}'
}

# ── Main ───────────────────────────────────────────────────────────────
echo "Cloud Monitoring setup for ${PROJECT_ID}"
$DRY && echo "(dry run, nothing will be written)"

echo "Metric descriptors:"
upsert_descriptor "custom.googleapis.com/catalog/backup_age_hours" '{
  "type": "custom.googleapis.com/catalog/backup_age_hours",
  "metricKind": "GAUGE", "valueType": "DOUBLE", "unit": "h",
  "displayName": "Catalog backup age (hours)",
  "description": "Hours since the named backup job last completed successfully, reported hourly by backup-monitor.sh on the catalog VM.",
  "labels": [{"key": "job", "valueType": "STRING", "description": "daily, weekly, monthly, or files"}]
}'
upsert_descriptor "custom.googleapis.com/catalog/monitor_heartbeat" '{
  "type": "custom.googleapis.com/catalog/monitor_heartbeat",
  "metricKind": "GAUGE", "valueType": "DOUBLE", "unit": "1",
  "displayName": "Catalog backup watchdog heartbeat",
  "description": "Constant 1 written every run of backup-monitor.sh. Absence means the watchdog itself has stopped, so the staleness alerts are blind."
}'

echo "Notification channels:"
CHANNELS=()
for e in $ALERT_EMAILS; do
  n="$(upsert_channel "$e")"
  [ -n "$n" ] && CHANNELS+=("$n")
done
CHANNELS_JSON="$(printf '%s\n' "${CHANNELS[@]+"${CHANNELS[@]}"}" | jq -R . | jq -sc 'map(select(length>0))')"

echo "Backup staleness alerts:"
AGE='metric.type="custom.googleapis.com/catalog/backup_age_hours" AND resource.type="global"'
upsert_policy "Catalog backup stale: daily" \
  "$(threshold_policy "Daily DB backup older than ${STALE_DAILY}h" \
     "${AGE} AND metric.labels.job=\"daily\"" "$STALE_DAILY" \
     "The 02:00 UTC DB dump has not completed successfully. Check /var/log/catalog-backup.log on ${VM_NAME}.")"

upsert_policy "Catalog backup stale: files" \
  "$(threshold_policy "Files rsync older than ${STALE_FILES}h" \
     "${AGE} AND metric.labels.job=\"files\"" "$STALE_FILES" \
     "The 03:00 UTC files rsync has not completed successfully. The uploaded originals are the irreplaceable asset; treat this as urgent.")"

upsert_policy "Catalog backup stale: weekly" \
  "$(threshold_policy "Weekly DB backup older than ${STALE_WEEKLY}h" \
     "${AGE} AND metric.labels.job=\"weekly\"" "$STALE_WEEKLY" \
     "The Sunday 04:00 UTC DB dump has not completed successfully.")"

upsert_policy "Catalog backup stale: monthly" \
  "$(threshold_policy "Monthly DB backup older than ${STALE_MONTHLY}h" \
     "${AGE} AND metric.labels.job=\"monthly\"" "$STALE_MONTHLY" \
     "The 1st-of-month 05:00 UTC DB dump has not completed successfully.")"

echo "Watchdog liveness:"
upsert_policy "Catalog backup watchdog down" "$(jq -n \
  --argjson ch "$CHANNELS_JSON" --argjson d "$HEARTBEAT_ABSENT" '{
  displayName: "Catalog backup watchdog down",
  combiner: "OR",
  conditions: [{
    displayName: "No heartbeat from backup-monitor.sh",
    conditionAbsent: {
      filter: "metric.type=\"custom.googleapis.com/catalog/monitor_heartbeat\" AND resource.type=\"global\"",
      duration: ($d | tostring + "s"),
      aggregations: [{alignmentPeriod: "600s", perSeriesAligner: "ALIGN_MAX"}]
    }
  }],
  notificationChannels: $ch,
  alertStrategy: {autoClose: "604800s"},
  documentation: {
    content: "backup-monitor.sh has stopped reporting. The backup staleness alerts are blind until this is fixed: check the hourly cron and the VM itself.",
    mimeType: "text/markdown"
  }
}')"

echo "Host alerts:"
upsert_policy "Catalog VM disk high" \
  "$(threshold_policy "Root disk above ${DISK_PCT}%" \
     "metric.type=\"agent.googleapis.com/disk/percent_used\" AND resource.type=\"gce_instance\" AND metric.labels.state=\"used\" AND metric.labels.device=\"${ROOT_DEVICE}\"" \
     "$DISK_PCT" \
     "Root disk on ${VM_NAME} is above ${DISK_PCT}%. Usual suspects: /var/www originals, /var/lib/docker, /var/log.")"

echo "Uptime checks:"
# Only the API check is managed here. The HTML side is already covered by the
# hand-made "catalog-jonsarkin-com" check on /item (which returns a real 200,
# not a redirect) and its "Catalog down" policy. Adding a second check on /
# would page twice for one outage. /api/items is the complement worth having:
# it exercises Omeka and MariaDB rather than just proving Traefik is up.
API_ID="$(upsert_uptime "Catalog API" "/api/items" '[{"statusValue":200}]')"

if [ -n "$API_ID" ]; then
  upsert_policy "Catalog API down" \
    "$(uptime_policy "Catalog API down" "$API_ID" \
       "https://${HOST}/api/items is not returning 200 from multiple regions. This path exercises Omeka and MariaDB, so it is the real liveness signal for the stack rather than just the web tier.")"
fi

echo "Done."

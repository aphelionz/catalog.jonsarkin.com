#!/usr/bin/env bash
#
# prune-sessions.sh: delete expired rows from Omeka's session table.
#
# Runs ON THE VM, daily. Omeka's DB session handler refuses any row older
# than session.gc_maxlifetime, so expired rows are dead weight. PHP's
# probabilistic GC should delete them, yet the table reached 301k rows
# (36 days deep) by 2026-10-02, about 8k new rows a day from cookie-less
# bots. A daily cron keeps the cleanup predictable and off the request path.
#
# Cron (mark's crontab):
#   30 1 * * * /opt/catalog/scripts/prune-sessions.sh >/dev/null 2>&1
#
set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────
COMPOSE="docker compose -f /opt/catalog/docker-compose.prod.yml"
LOG_FILE="/var/log/catalog-backup.log"

# ── Helpers ────────────────────────────────────────────────────────────
log() {
  printf '[%s] prune: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

main() {
  # Take the cutoff from Omeka's own PHP config, so it always matches the
  # age Omeka itself still accepts.
  local lifetime
  lifetime="$($COMPOSE exec -T omeka php -r 'echo (int) ini_get("session.gc_maxlifetime");')"
  if ! [ "$lifetime" -gt 0 ] 2>/dev/null; then
    log "FATAL: unusable session.gc_maxlifetime '${lifetime}'"
    exit 1
  fi

  local deleted
  deleted="$($COMPOSE exec -T db sh -c "MYSQL_PWD=\$MARIADB_ROOT_PASSWORD mariadb -uroot -N omeka \
    -e 'DELETE FROM session WHERE modified < UNIX_TIMESTAMP() - ${lifetime}; SELECT ROW_COUNT();'")"
  log "deleted ${deleted} sessions older than ${lifetime}s"
}

{ main "$@"; } 2>&1 | tee -a "$LOG_FILE"

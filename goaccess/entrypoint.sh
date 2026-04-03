#!/bin/sh
# Generate GoAccess HTML report from Traefik JSON access logs every hour.

LOG_FILE="/var/log/traefik/access.log"
REPORT="/var/www/goaccess/index.html"

mkdir -p /var/www/goaccess

while true; do
  if [ -f "$LOG_FILE" ] && [ -s "$LOG_FILE" ]; then
    echo "[$(date)] Generating report ..."
    goaccess "$LOG_FILE" \
      --log-format=TRAEFIKJSON \
      --output="$REPORT" \
      --anonymize-ip \
      --ignore-crawlers \
      --real-os \
      --no-query-string \
      --exclude-ip=127.0.0.1 \
      --ignore-panel=KEYPHRASES \
      --html-report-title="catalog.jonsarkin.com"
    echo "[$(date)] Done"
  else
    echo "[$(date)] Waiting for log data ..."
  fi
  sleep 3600
done

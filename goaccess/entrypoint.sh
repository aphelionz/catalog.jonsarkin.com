#!/bin/sh
# Generate GoAccess HTML report from Traefik JSON access logs every hour.
# Converts Traefik's JSON to Combined Log Format before feeding to GoAccess.

LOG_FILE="/var/log/traefik/access.log"
CLF_FILE="/tmp/access_combined.log"
REPORT="/var/www/goaccess/index.html"

mkdir -p /var/www/goaccess

json_to_clf() {
  # Extract fields from Traefik JSON using sed, output Combined Log Format.
  # Works with busybox tools (no jq/gawk needed).
  while IFS= read -r line; do
    host=$(echo "$line" | sed -n 's/.*"ClientHost":"\([^"]*\)".*/\1/p')
    method=$(echo "$line" | sed -n 's/.*"RequestMethod":"\([^"]*\)".*/\1/p')
    path=$(echo "$line" | sed -n 's/.*"RequestPath":"\([^"]*\)".*/\1/p')
    proto=$(echo "$line" | sed -n 's/.*"RequestProtocol":"\([^"]*\)".*/\1/p')
    status=$(echo "$line" | sed -n 's/.*"DownstreamStatus":\([0-9]*\).*/\1/p')
    size=$(echo "$line" | sed -n 's/.*"DownstreamContentSize":\([0-9]*\).*/\1/p')
    ts=$(echo "$line" | sed -n 's/.*"time":"\([^"]*\)".*/\1/p')
    ua=$(echo "$line" | sed -n 's/.*"request_User-Agent":"\([^"]*\)".*/\1/p')
    ref=$(echo "$line" | sed -n 's/.*"request_Referer":"\([^"]*\)".*/\1/p')
    [ -z "$host" ] && continue
    [ -z "$ref" ] && ref="-"
    [ -z "$ua" ] && ua="-"
    [ -z "$size" ] && size="0"
    printf '%s - - [%s] "%s %s %s" %s %s "%s" "%s"\n' \
      "$host" "$ts" "$method" "$path" "$proto" "$status" "$size" "$ref" "$ua"
  done < "$LOG_FILE"
}

while true; do
  if [ -f "$LOG_FILE" ] && [ -s "$LOG_FILE" ]; then
    echo "[$(date)] Converting logs and generating report ..."
    json_to_clf > "$CLF_FILE"
    LINES=$(wc -l < "$CLF_FILE")
    echo "[$(date)] Converted $LINES log lines"
    if [ "$LINES" -gt 0 ]; then
      goaccess "$CLF_FILE" \
        --log-format='%h - - [%x] "%m %U %H" %s %b "%R" "%u"' \
        --datetime-format='%Y-%m-%dT%H:%M:%SZ' \
        --output="$REPORT" \
        --anonymize-ip \
        --ignore-crawlers \
        --real-os \
        --no-query-string \
        --exclude-ip=127.0.0.1 \
        --ignore-panel=KEYPHRASES \
        --html-report-title="catalog.jonsarkin.com"
    fi
    rm -f "$CLF_FILE"
    echo "[$(date)] Done"
  else
    echo "[$(date)] Waiting for log data ..."
  fi
  sleep 3600
done

#!/bin/bash
# Block destructive make targets that hit production or cost money.
# Exit 2 = block with message. Exit 0 = allow.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# For command-name checks, ignore quoted strings so e.g. a commit message
# mentioning "make pull" doesn't trip the guard. The ssh mutation check
# below still scans the full command (the SQL lives inside quotes).
STRIPPED=$(echo "$COMMAND" | sed -e "s/'[^']*'//g" -e 's/"[^"]*"//g')

# Warn: make deploy (user confirms via normal tool-approval prompt)
if echo "$STRIPPED" | grep -qE '(^|\s)make\s+deploy(\s|$)'; then
  echo "This command deploys to production. Make sure the user has approved." >&2
  exit 0
fi

# Block: make pull (but allow make pull-new, pull-db, pull-files)
if echo "$STRIPPED" | grep -qE '(^|\s)make\s+pull(\s|$)'; then
  echo "BLOCKED: 'make pull' wipes and replaces the local DB from production. Ask the user for approval." >&2
  exit 2
fi

# Warn: theme push to the live storefront
if echo "$STRIPPED" | grep -q 'shopify theme push' && echo "$STRIPPED" | grep -qe '--allow-live'; then
  echo "This pushes the theme to the LIVE storefront (jonsarkin.com). Make sure the user has approved." >&2
  exit 0
fi

# Warn: SSH to prod; stronger warning if the remote command mutates the DB
if echo "$STRIPPED" | grep -qE '(^|[[:space:];&|(])ssh\s[^;&|]*omeka'; then
  if echo "$COMMAND" | grep -qiE '\b(UPDATE|DELETE|INSERT|REPLACE|ALTER|DROP|TRUNCATE)\b'; then
    echo "This runs a MUTATING command on production. Run 'make backup-db' first and make sure the user has approved." >&2
  else
    echo "This command touches the production server. Make sure the user has approved." >&2
  fi
  exit 0
fi

exit 0

#!/bin/bash
# Block destructive make targets that hit production or cost money.
# Exit 2 = block with message. Exit 0 = allow.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Block: make enrich / make process-new (costs money via Claude API)
# Allow: --dry-run, --batch-status, --apply-cache (read-only operations)
if echo "$COMMAND" | grep -qE '(^|\s)make\s+(enrich|process-new)(\s|$)'; then
  if ! echo "$COMMAND" | grep -qE '\-\-dry-run|\-\-batch-status|\-\-apply-cache'; then
    echo "BLOCKED: This command hits the Claude API and costs money. Add ARGS=\"--dry-run\" first. Ask the user for approval before proceeding." >&2
    exit 2
  fi
fi

# Warn: make deploy (user confirms via normal tool-approval prompt)
if echo "$COMMAND" | grep -qE '(^|\s)make\s+deploy(\s|$)'; then
  echo "This command deploys to production. Make sure the user has approved." >&2
  exit 0
fi

# Block: make pull (but allow make pull-new, pull-db, pull-files)
if echo "$COMMAND" | grep -qE '(^|\s)make\s+pull(\s|$)'; then
  echo "BLOCKED: 'make pull' wipes and replaces the local DB from production. Ask the user for approval." >&2
  exit 2
fi

exit 0

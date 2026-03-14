#!/bin/bash
# Block destructive make targets that hit production or cost money.
# Exit 2 = block with message. Exit 0 = allow.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

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

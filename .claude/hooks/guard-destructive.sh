#!/bin/bash
# Block destructive make targets that hit production or cost money.
# Exit 2 = block with message. Exit 0 = allow.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Block: make enrich / enrich-batch / enrich-batch-collect (costs money)
# Allow: make enrich-dry, make enrich-apply, make enrich-batch-status
if echo "$COMMAND" | grep -qE '(^|\s)make\s+(enrich|enrich-batch|enrich-batch-collect)(\s|$)'; then
  if ! echo "$COMMAND" | grep -qE '(enrich-dry|enrich-apply|enrich-batch-status)'; then
    echo "BLOCKED: This command hits the Claude API and costs money. Run 'make enrich-dry' first. Ask the user for approval before proceeding." >&2
    exit 2
  fi
fi

# Block: make deploy
if echo "$COMMAND" | grep -qE '(^|\s)make\s+deploy(\s|$)'; then
  echo "BLOCKED: This command deploys to production. Ask the user for explicit approval." >&2
  exit 2
fi

# Block: make pull (but allow make pull-new, pull-db, pull-files, pull-modules, pull-themes)
if echo "$COMMAND" | grep -qE '(^|\s)make\s+pull(\s|$)'; then
  echo "BLOCKED: 'make pull' wipes and replaces the local DB from production. Ask the user for approval." >&2
  exit 2
fi

exit 0

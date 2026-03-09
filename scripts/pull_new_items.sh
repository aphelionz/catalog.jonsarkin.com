#!/usr/bin/env bash
#
# pull_new_items.sh — Incrementally pull new items from production.
#
# Finds the max resource ID in the local DB, then exports only rows
# with higher IDs from production.  Media files are rsynced separately
# (additive, same as make pull-files).
#
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────
PROD_HOST="${PROD_USER:-mark}@${PROD_HOST:-omeka.us-east1-b.folkloric-rite-468520-r2}"
PROD_DIR="${PROD_DIR:-/opt/catalog}"

LOCAL_DB_CMD="docker compose exec -T db mariadb -uomeka -pomeka omeka"
# Prod DB access: source .env on the VPS to get $MYSQL_USER, $MYSQL_PASSWORD, $MYSQL_DATABASE
PROD_DB_PREFIX="cd $PROD_DIR && . .env && docker compose exec -T db"

# ── 1. Get local max resource ID ─────────────────────────────────────
LOCAL_MAX=$($LOCAL_DB_CMD -N -e "SELECT COALESCE(MAX(id), 0) FROM resource;")
echo "Local max resource ID: $LOCAL_MAX"

# ── 2. Get prod max resource ID (to report delta) ────────────────────
PROD_MAX=$(ssh "$PROD_HOST" "$PROD_DB_PREFIX mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE \
    -N -e 'SELECT COALESCE(MAX(id), 0) FROM resource;'")
echo "Prod max resource ID:  $PROD_MAX"

if [ "$PROD_MAX" -le "$LOCAL_MAX" ]; then
    echo "No new items to pull."
    exit 0
fi

NEW_COUNT=$(( PROD_MAX - LOCAL_MAX ))
echo "Pulling $NEW_COUNT new resource(s) (IDs $((LOCAL_MAX + 1))..$PROD_MAX)..."

# ── 3. Export new rows from prod ─────────────────────────────────────
# Order: resource first (FK parent), then item, media, value.
# --no-create-info: INSERT only, no DDL.
# --insert-ignore: safe for reruns.
TABLES="resource item media value"

ssh "$PROD_HOST" "
    cd $PROD_DIR && . .env

    # Disable FK checks around the inserts
    echo 'SET FOREIGN_KEY_CHECKS=0;'

    for TABLE in $TABLES; do
        if [ \"\$TABLE\" = 'value' ]; then
            WHERE='resource_id > $LOCAL_MAX'
        else
            WHERE='id > $LOCAL_MAX'
        fi
        docker compose exec -T db mariadb-dump -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE \
            --no-create-info --insert-ignore \
            --where=\"\$WHERE\" \
            \"\$TABLE\"
    done

    echo 'SET FOREIGN_KEY_CHECKS=1;'
" | $LOCAL_DB_CMD

echo "Database rows imported."

# ── 4. Sync media files (additive) ──────────────────────────────────
echo "Syncing media files..."
rsync -avz --compress --partial --progress \
    --exclude='tmp/' \
    "${PROD_HOST}:/var/www/omeka-s/files/" \
    omeka/volume/files/
echo "Files synced."

# ── 5. Verify ────────────────────────────────────────────────────────
NEW_LOCAL_MAX=$($LOCAL_DB_CMD -N -e "SELECT COALESCE(MAX(id), 0) FROM resource;")
ITEM_COUNT=$($LOCAL_DB_CMD -N -e "SELECT COUNT(*) FROM item WHERE id > $LOCAL_MAX;")

echo ""
echo "Done. Pulled $ITEM_COUNT new item(s)."
echo "  Local max resource ID: $LOCAL_MAX → $NEW_LOCAL_MAX"

.PHONY: help local down logs ingest ingest-full ingest-dry process-new sync deploy pull pull-new pull-db pull-files doctor backup-db restore-db push-schema ensure-api-key classify classify-opencv classify-stats reclassify segment segment-force segment-test segment-tier segment-id push-segments sam-playground

.DEFAULT_GOAL := help

-include .env
export

# Production host (matches omeka/ansible/inventory.ini)
PROD_HOST  := omeka.us-east1-b.folkloric-rite-468520-r2
PROD_USER  := mark
PROD_DIR   := /opt/catalog
BACKUP_DIR := backups

help: ## Show available targets
	@echo ""
	@echo "  Dev"
	@grep -E '^(local|down|logs|doctor):.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Ingest"
	@grep -E '^(ingest|ingest-full|ingest-dry|process-new):.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Data Sync"
	@grep -E '^(sync|pull-new|pull-db|pull-files|pull|deploy|push-schema):.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Classification & Segmentation (local M4/MPS)"
	@grep -E '^(classify|classify-opencv|classify-stats|reclassify|segment|segment-force|segment-test|segment-tier|segment-id|push-segments|sam-playground):.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Utilities"
	@grep -E '^(backup-db|restore-db|ensure-api-key):.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Enrichment is now in the Omeka admin UI: Admin > Enrich Queue"
	@echo ""

# ── Dev ──────────────────────────────────────────────────────────────

local: ## Start the catalog stack (omeka + qdrant + clip-api)
	docker compose up -d

down: ## Stop and remove containers
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

doctor: ## Check local dev prerequisites
	@echo "Checking prerequisites..."
	@command -v docker >/dev/null 2>&1 && echo "  ✓ docker" || echo "  ✗ docker not found"
	@docker compose version >/dev/null 2>&1 && echo "  ✓ docker compose" || echo "  ✗ docker compose v2 not found"
	@ssh -o ConnectTimeout=3 $(PROD_USER)@$(PROD_HOST) 'docker compose version' >/dev/null 2>&1 && echo "  ✓ prod docker" || echo "  ✗ prod docker unreachable"
	@command -v rsync >/dev/null 2>&1 && echo "  ✓ rsync" || echo "  ✗ rsync not found (needed for pull)"
	@(! lsof -i :8888 -sTCP:LISTEN >/dev/null 2>&1) && echo "  ✓ port 8888 available" || echo "  ✗ port 8888 in use"
	@(! lsof -i :8000 -sTCP:LISTEN >/dev/null 2>&1) && echo "  ✓ port 8000 available" || echo "  ✗ port 8000 in use"
	@(! lsof -i :6333 -sTCP:LISTEN >/dev/null 2>&1) && echo "  ✓ port 6333 available" || echo "  ✗ port 6333 in use"
	@echo "Done."

# ── Enrich & Ingest ──────────────────────────────────────────────────

ingest: ## Incremental ingest: only new/updated items into Qdrant (CPU)
	docker compose run --rm ingest

ingest-full: ## Full re-ingest: reprocess all items into Qdrant (CPU)
	docker compose run --rm ingest --force

ingest-dry: ## Preview what incremental ingest would process
	docker compose run --rm ingest --dry-run

process-new: ## Re-index search (enrichment now via Omeka admin UI)
	docker compose run --rm ingest

# ── Data Sync ────────────────────────────────────────────────────────

sync: ## Pull new items from prod + ingest into local Qdrant
	$(MAKE) pull-new
	$(MAKE) ingest

pull-new: ## Pull only new items from production (additive, no DB wipe)
	bash scripts/pull_new_items.sh

pull: pull-db ensure-api-key pull-files ## Full pull: wipe + replace local DB from production

pull-db: ## Pull production database into local MariaDB
	@echo "Dumping production database..."
	ssh $(PROD_USER)@$(PROD_HOST) 'cd $(PROD_DIR) && . .env && docker compose exec -T db mariadb-dump -u$$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE' \
	| docker compose exec -T db mariadb -uomeka -pomeka omeka
	@echo "Database imported."

pull-files: ## Pull production uploaded files to local
	@echo "Syncing production files..."
	rsync -avz --compress --partial --progress \
		--exclude='tmp/' \
		$(PROD_USER)@$(PROD_HOST):/var/www/omeka-s/files/ \
		omeka/volume/files/
	@echo "Files synced."

deploy: ## Push code (modules/themes) to production and restart Omeka
	rsync -avz --compress --partial --progress \
		--exclude='.env' --exclude='omeka/volume/files/' --exclude='backups/' \
		--exclude='.hf_cache/' --exclude='__pycache__/' --exclude='.venv/' \
		--exclude='harvest/' --exclude='search_index/' --exclude='.DS_Store' \
		--exclude='.git/' --exclude='node_modules/' --exclude='.claude/' \
		--exclude='acme.json' --exclude='omeka/volume/logs/' \
		./ $(PROD_USER)@$(PROD_HOST):$(PROD_DIR)/
	ssh $(PROD_USER)@$(PROD_HOST) 'cd $(PROD_DIR) && docker compose restart omeka'

push-schema: ## Push local schema, site pages, item sets, and config to production
	@echo "Exporting local schema tables..."
	@{ \
		echo "SET FOREIGN_KEY_CHECKS = 0;"; \
		echo "DELETE FROM resource_template_property WHERE resource_template_id = 2;"; \
		echo "DELETE FROM site_page_block;"; \
		echo "DELETE FROM site_page;"; \
		echo "DELETE FROM site_item_set;"; \
		echo "DELETE FROM value WHERE resource_id IN (SELECT id FROM item_set);"; \
		echo "DELETE FROM item_set;"; \
		echo "DELETE FROM resource WHERE resource_type = 'Omeka\\\\Entity\\\\ItemSet';"; \
		docker compose exec -T db mariadb-dump -uomeka -pomeka omeka \
			--replace --no-create-info --skip-extended-insert --skip-lock-tables \
			--where="resource_type = 'Omeka\\\\Entity\\\\ItemSet'" resource; \
		echo "UPDATE resource SET thumbnail_id = NULL WHERE resource_type = 'Omeka\\\\\\\\Entity\\\\\\\\ItemSet';"; \
		docker compose exec -T db mariadb-dump -uomeka -pomeka omeka \
			--replace --no-create-info --skip-extended-insert \
			custom_vocab resource_template resource_template_property \
			faceted_browse_page faceted_browse_category \
			faceted_browse_facet faceted_browse_column \
			site site_page site_page_block site_setting \
			item_set site_item_set; \
		docker compose exec -T db mariadb-dump -uomeka -pomeka omeka \
			--replace --no-create-info --skip-extended-insert --skip-lock-tables \
			--where="resource_id IN (SELECT id FROM item_set)" value; \
		echo "SET FOREIGN_KEY_CHECKS = 1;"; \
	} > /tmp/omeka-schema-export.sql
	@echo "Pushing to production..."
	cat /tmp/omeka-schema-export.sql | ssh $(PROD_USER)@$(PROD_HOST) \
		'cd $(PROD_DIR) && . .env && docker compose exec -T db mariadb -u$$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE'
	@echo "Verifying production schema..."
	@ssh $(PROD_USER)@$(PROD_HOST) 'cd $(PROD_DIR) && . .env && docker compose exec -T db mariadb -u$$MYSQL_USER -p$$MYSQL_PASSWORD $$MYSQL_DATABASE \
			-e "SELECT COUNT(*) AS custom_vocabs FROM custom_vocab; \
			    SELECT COUNT(*) AS template_2_props FROM resource_template_property WHERE resource_template_id = 2; \
			    SELECT COUNT(*) AS site_pages FROM site_page; \
			    SELECT COUNT(*) AS item_sets FROM item_set;"'
	@echo "Done. Expected: 7 custom_vocabs, 25 template_2_props, 8 site_pages, 18 item_sets."

# ── Classification & Segmentation (local M4/MPS — not in Docker) ──

classify: ## Metadata-derived density classification (fast, uses MariaDB)
	cd sarkin-clip && .venv/bin/python classify.py $(if $(P_LOW),--p-low $(P_LOW)) $(if $(P_HIGH),--p-high $(P_HIGH)) $(if $(DB_HOST),--db-host $(DB_HOST))

classify-opencv: ## OpenCV-based density classification (downloads images)
	cd sarkin-clip && .venv/bin/python classify.py --opencv $(if $(P_LOW),--p-low $(P_LOW)) $(if $(P_HIGH),--p-high $(P_HIGH))

classify-stats: ## Show density tier distribution with per-source breakdown
	cd sarkin-clip && .venv/bin/python classify.py --stats

reclassify: ## Reclassify tiers from stored scores by percentile
	cd sarkin-clip && .venv/bin/python classify.py --reclassify $(if $(P_LOW),--p-low $(P_LOW)) $(if $(P_HIGH),--p-high $(P_HIGH))

segment: ## Run SAM 2.1 segmentation locally (incremental, density-tiered)
	cd sarkin-clip && .venv/bin/python local_segment_ingest.py segment

segment-force: ## Re-segment all items with SAM 2.1 (recreates collection)
	cd sarkin-clip && .venv/bin/python local_segment_ingest.py segment --force

segment-test: ## Segment ~20 test items (mix of tiers) for parameter tuning
	cd sarkin-clip && .venv/bin/python local_segment_ingest.py segment --test

segment-tier: ## Segment items in a specific tier (usage: make segment-tier TIER=sparse)
	cd sarkin-clip && .venv/bin/python local_segment_ingest.py segment --tier $(TIER)

segment-id: ## Segment a single item (usage: make segment-id ID=1234)
	cd sarkin-clip && .venv/bin/python local_segment_ingest.py segment --id $(ID)

push-segments: ## Push segment JPEGs + Qdrant vectors to production
	cd sarkin-clip && .venv/bin/python local_segment_ingest.py push

sam-playground: ## Interactive SAM parameter playground (local, MPS)
	cd sarkin-clip && uv run --python .venv/bin/python sam_playground.py

# ── Utilities ────────────────────────────────────────────────────────

backup-db: ## Dump local MariaDB to a timestamped .sql.gz file
	@mkdir -p $(BACKUP_DIR)
	@echo "Backing up local database..."
	docker compose exec -T db mariadb-dump -uomeka -pomeka omeka \
		| gzip > $(BACKUP_DIR)/omeka-$$(date +%Y%m%d-%H%M%S).sql.gz
	@echo "Backup saved:"
	@ls -lh $(BACKUP_DIR)/*.sql.gz | tail -1

restore-db: ## Restore local MariaDB from a backup file (usage: make restore-db BACKUP=backups/omeka-XXX.sql.gz)
	@if [ -z "$(BACKUP)" ]; then \
		echo "Usage: make restore-db BACKUP=backups/omeka-XXX.sql.gz"; \
		echo ""; \
		echo "Available backups:"; \
		ls -lht $(BACKUP_DIR)/*.sql.gz 2>/dev/null || echo "  (none)"; \
		exit 1; \
	fi
	@test -f "$(BACKUP)" || (echo "File not found: $(BACKUP)" && exit 1)
	@echo "Restoring from $(BACKUP)..."
	gunzip -c "$(BACKUP)" | docker compose exec -T db mariadb -uomeka -pomeka omeka
	@echo "Restore complete. Item count:"
	@docker compose exec -T db mariadb -uomeka -pomeka omeka \
		-e "SELECT COUNT(*) AS items FROM item;"
	@$(MAKE) --no-print-directory ensure-api-key

ensure-api-key: ## Create local-only API key (safe to re-run; never use on prod)
	@HASH=$$(docker compose exec -T omeka php -r "echo password_hash('sarkin2024', PASSWORD_BCRYPT);") && \
	docker compose exec -T db mariadb -uomeka -pomeka omeka -e " \
		INSERT INTO api_key (id, owner_id, label, credential_hash, created) \
		VALUES ('catalog_api', 1, 'Local development key', '$$HASH', NOW()) \
		ON DUPLICATE KEY UPDATE credential_hash = '$$HASH';"
	@echo "Local API key ensured (catalog_api)."

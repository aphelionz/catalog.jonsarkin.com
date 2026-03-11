.PHONY: local down logs ingest ingest-full ingest-dry enrich enrich-dry enrich-batch enrich-batch-status enrich-batch-collect enrich-apply enrich-prod-dry enrich-prod doctor doctor-catalog pull pull-new pull-db pull-files pull-modules pull-themes deploy backup-db restore-db push-schema ensure-api-key harvest harvest-discover harvest-fetch harvest-extract harvest-output visual-refs visual-refs-apply visual-refs-cross-media visual-locs visual-locs-apply visual-locs-cross-media

-include .env
export

# Production host (matches omeka/ansible/inventory.ini)
PROD_HOST  := omeka.us-east1-b.folkloric-rite-468520-r2
PROD_USER  := mark
PROD_DIR   := /opt/catalog
BACKUP_DIR := backups

local: ## Start the catalog stack (omeka + qdrant + clip-api)
	docker compose up -d

down: ## Stop and remove containers
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

ingest: ## Incremental ingest: only new/updated items into Qdrant (CPU)
	docker compose run --rm ingest

ingest-full: ## Full re-ingest: reprocess all items into Qdrant (CPU)
	docker compose run --rm ingest --force

ingest-dry: ## Preview what incremental ingest would process
	docker compose run --rm ingest --dry-run

enrich: ## Enrich items with Claude OCR + metadata (writes to Omeka)
	python3 scripts/enrich_metadata.py

enrich-dry: ## Preview enrichment changes without writing
	python3 scripts/enrich_metadata.py --dry-run

enrich-batch: ## Submit all items to Claude Batch API (50% cheaper, haiku default)
	python3 scripts/enrich_metadata.py --batch --model haiku

enrich-batch-status: ## Check status of pending enrichment batches
	python3 scripts/enrich_metadata.py --batch-status

enrich-batch-collect: ## Collect batch results and apply to Omeka
	python3 scripts/enrich_metadata.py --batch-collect

enrich-apply: ## Re-apply cached enrichment results to Omeka (no API cost)
	python3 scripts/enrich_metadata.py --apply-cache

enrich-prod-dry: ## Preview enrichment of new prod items (no writes)
	python3 scripts/enrich_metadata.py --target prod --dry-run

enrich-prod: ## Enrich new prod items (writes to production — requires confirmation)
	python3 scripts/enrich_metadata.py --target prod

references: ## Preview cultural reference extraction from JIM Stories (dry-run)
	python3 scripts/extract_jim_references.py

references-apply: ## Extract cultural references and write to DB + create faceted browse
	python3 scripts/extract_jim_references.py --apply

visual-refs: ## Extract cultural references from visual work transcriptions (Pass 1, dry-run)
	python3 scripts/extract_visual_references.py

visual-refs-apply: ## Extract visual references and write to DB + extend faceted browse
	python3 scripts/extract_visual_references.py --apply

visual-refs-cross-media: ## Generate cross-media reference reports (visual works ↔ JIM Stories)
	python3 scripts/extract_visual_references.py --cross-media

visual-locs: ## Extract geographic locations from visual work transcriptions (dry-run)
	python3 scripts/extract_visual_locations.py

visual-locs-apply: ## Extract visual locations and write to DB + extend faceted browse
	python3 scripts/extract_visual_locations.py --apply

visual-locs-cross-media: ## Generate cross-media location reports (visual works ↔ JIM Stories)
	python3 scripts/extract_visual_locations.py --cross-media

deploy: ## Push code (modules/themes) to production and restart Omeka
	rsync -avz --compress --partial --progress \
		--exclude='.env' --exclude='omeka/volume/files/' --exclude='backups/' \
		--exclude='.hf_cache/' --exclude='__pycache__/' --exclude='.venv/' \
		--exclude='harvest/' --exclude='search_index/' --exclude='.DS_Store' \
		--exclude='.git/' --exclude='node_modules/' --exclude='.claude/' \
		--exclude='acme.json' --exclude='omeka/volume/logs/' \
		./ $(PROD_USER)@$(PROD_HOST):$(PROD_DIR)/
	ssh $(PROD_USER)@$(PROD_HOST) 'cd $(PROD_DIR) && docker compose restart omeka'

pull-new: ## Pull only new items from production (additive, no DB wipe)
	bash scripts/pull_new_items.sh

pull: pull-db ensure-api-key pull-files pull-modules pull-themes ## Full pull: wipe + replace local DB from production

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

pull-modules: ## Pull production modules to local
	@echo "Syncing production modules..."
	rsync -avz --compress --partial --progress \
		$(PROD_USER)@$(PROD_HOST):$(PROD_DIR)/omeka/volume/modules/ \
		omeka/volume/modules/
	@echo "Modules synced."

pull-themes: ## Pull production themes to local
	@echo "Syncing production themes..."
	rsync -avz --compress --partial --progress \
		$(PROD_USER)@$(PROD_HOST):$(PROD_DIR)/omeka/volume/themes/ \
		omeka/volume/themes/
	@echo "Themes synced."

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

doctor-catalog: ## Check catalog items for completeness issues
	python3 scripts/doctor_catalog.py

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


editor: ## Launch rapid-fire metadata editor standalone (localhost:9000, no Docker)
	python3 tools/rapid-editor/serve.py

harvest: ## Full Wayback Machine harvest pipeline (discover + fetch + extract + output)
	python3 scripts/harvest_wayback.py

harvest-discover: ## Discover jsarkin.com URLs in Wayback Machine
	python3 scripts/harvest_wayback.py discover

harvest-fetch: ## Fetch archived pages (resumable, rate-limited ~1.5s/page)
	python3 scripts/harvest_wayback.py fetch

harvest-extract: ## Extract text from fetched pages + deduplicate
	python3 scripts/harvest_wayback.py extract

harvest-output: ## Generate CSV, JSON, and report from extracted data
	python3 scripts/harvest_wayback.py output

ensure-api-key: ## Create local-only API key (safe to re-run; never use on prod)
	@HASH=$$(docker compose exec -T omeka php -r "echo password_hash('sarkin2024', PASSWORD_BCRYPT);") && \
	docker compose exec -T db mariadb -uomeka -pomeka omeka -e " \
		INSERT INTO api_key (id, owner_id, label, credential_hash, created) \
		VALUES ('catalog_api', 1, 'Local development key', '$$HASH', NOW()) \
		ON DUPLICATE KEY UPDATE credential_hash = '$$HASH';"
	@echo "Local API key ensured (catalog_api)."

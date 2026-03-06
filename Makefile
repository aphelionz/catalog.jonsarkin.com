.PHONY: local down logs ingest ingest-full ingest-dry enrich enrich-dry enrich-batch enrich-batch-status enrich-batch-collect enrich-apply doctor doctor-catalog pull pull-new pull-db pull-files pull-modules pull-themes deploy backup-db restore-db push-schema backfill backfill-dry create-box-collections create-box-collections-dry ensure-api-key

-include .env
export

# Production host (matches omeka/ansible/inventory.ini)
PROD_HOST  := omeka.us-east1-b.folkloric-rite-468520-r2
PROD_USER  := mark
OMEKA_ROOT := /var/www/omeka-s
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

deploy: ## Push code (modules/themes) to production
	ansible-playbook -i omeka/ansible/inventory.ini omeka/ansible/deploy.yml

pull-new: ## Pull only new items from production (additive, no DB wipe)
	bash scripts/pull_new_items.sh

pull: pull-db ensure-api-key pull-files pull-modules pull-themes ## Full pull: wipe + replace local DB from production

pull-db: ## Pull production database into local MariaDB
	@echo "Dumping production database..."
	ssh $(PROD_USER)@$(PROD_HOST) '\
		cd $(OMEKA_ROOT)/config && \
		DB_USER=$$(grep "^user" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_PASS=$$(grep "^password" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_NAME=$$(grep "^dbname" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_HOST=$$(grep "^host" database.ini | sed "s/.*= *//; s/\"//g") && \
		mariadb-dump -u"$$DB_USER" -p"$$DB_PASS" -h"$$DB_HOST" "$$DB_NAME"' \
	| docker compose exec -T db mariadb -uomeka -pomeka omeka
	@echo "Database imported."

pull-files: ## Pull production uploaded files to local
	@echo "Syncing production files..."
	rsync -avz --compress --partial --progress \
		--exclude='tmp/' \
		$(PROD_USER)@$(PROD_HOST):$(OMEKA_ROOT)/files/ \
		omeka/volume/files/
	@echo "Files synced."

pull-modules: ## Pull production modules to local
	@echo "Syncing production modules..."
	rsync -avz --compress --partial --progress \
		$(PROD_USER)@$(PROD_HOST):$(OMEKA_ROOT)/modules/ \
		omeka/volume/modules/
	@echo "Modules synced."

pull-themes: ## Pull production themes to local
	@echo "Syncing production themes..."
	rsync -avz --compress --partial --progress \
		$(PROD_USER)@$(PROD_HOST):$(OMEKA_ROOT)/themes/ \
		omeka/volume/themes/
	@echo "Themes synced."

doctor: ## Check local dev prerequisites
	@echo "Checking prerequisites..."
	@command -v docker >/dev/null 2>&1 && echo "  ✓ docker" || echo "  ✗ docker not found"
	@docker compose version >/dev/null 2>&1 && echo "  ✓ docker compose" || echo "  ✗ docker compose v2 not found"
	@command -v ansible-playbook >/dev/null 2>&1 && echo "  ✓ ansible" || echo "  ✗ ansible not found (needed for deploy)"
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
	cat /tmp/omeka-schema-export.sql | ssh $(PROD_USER)@$(PROD_HOST) '\
		cd $(OMEKA_ROOT)/config && \
		DB_USER=$$(grep "^user" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_PASS=$$(grep "^password" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_NAME=$$(grep "^dbname" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_HOST=$$(grep "^host" database.ini | sed "s/.*= *//; s/\"//g") && \
		mariadb -u"$$DB_USER" -p"$$DB_PASS" -h"$$DB_HOST" "$$DB_NAME"'
	@echo "Verifying production schema..."
	@ssh $(PROD_USER)@$(PROD_HOST) '\
		cd $(OMEKA_ROOT)/config && \
		DB_USER=$$(grep "^user" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_PASS=$$(grep "^password" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_NAME=$$(grep "^dbname" database.ini | sed "s/.*= *//; s/\"//g") && \
		DB_HOST=$$(grep "^host" database.ini | sed "s/.*= *//; s/\"//g") && \
		mariadb -u"$$DB_USER" -p"$$DB_PASS" -h"$$DB_HOST" "$$DB_NAME" \
			-e "SELECT COUNT(*) AS custom_vocabs FROM custom_vocab; \
			    SELECT COUNT(*) AS template_2_props FROM resource_template_property WHERE resource_template_id = 2; \
			    SELECT COUNT(*) AS site_pages FROM site_page; \
			    SELECT COUNT(*) AS item_sets FROM item_set;"'
	@echo "Done. Expected: 7 custom_vocabs, 25 template_2_props, 8 site_pages, 25 item_sets."

backfill-dry: ## Preview backfill changes without writing
	python3 scripts/backfill_defaults.py --dry-run

backfill: ## Backfill default metadata values (only fills empty fields)
	python3 scripts/backfill_defaults.py

backfill-box-motifs-dry: ## Preview box-derived motif additions
	python3 scripts/backfill_box_motifs.py --dry-run

backfill-box-motifs: ## Add box-derived motifs to dcterms:subject
	python3 scripts/backfill_box_motifs.py

create-box-collections-dry: ## Preview box-category item set creation
	python3 scripts/create_box_item_sets.py --dry-run

create-box-collections: ## Create item sets for box categories and assign items
	python3 scripts/create_box_item_sets.py

editor: ## Launch rapid-fire metadata editor (localhost:9000)
	python3 tools/rapid-editor/serve.py

ensure-api-key: ## Create local-only API key (safe to re-run; never use on prod)
	@HASH=$$(docker compose exec -T omeka php -r "echo password_hash('sarkin2024', PASSWORD_BCRYPT);") && \
	docker compose exec -T db mariadb -uomeka -pomeka omeka -e " \
		INSERT INTO api_key (id, owner_id, label, credential_hash, created) \
		VALUES ('catalog_api', 1, 'Local development key', '$$HASH', NOW()) \
		ON DUPLICATE KEY UPDATE credential_hash = '$$HASH';"
	@echo "Local API key ensured (catalog_api)."

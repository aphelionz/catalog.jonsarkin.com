.PHONY: local down logs ingest enrich enrich-dry enrich-batch enrich-batch-status enrich-batch-collect doctor pull pull-db pull-files pull-modules pull-themes deploy

# Production host (matches omeka/ansible/inventory.ini)
PROD_HOST  := omeka.us-east1-b.folkloric-rite-468520-r2
PROD_USER  := mark
OMEKA_ROOT := /var/www/omeka-s

local: ## Start the catalog stack (omeka + qdrant + clip-api)
	docker compose up -d

down: ## Stop and remove containers
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

ingest: ## Run a one-shot ingest from Omeka into Qdrant (CPU)
	docker compose run --rm ingest

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

deploy: ## Push code (modules/themes) to production
	ansible-playbook -i omeka/ansible/inventory.ini omeka/ansible/deploy.yml

pull: pull-db pull-files pull-modules pull-themes ## Pull database, files, modules, and themes from production

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

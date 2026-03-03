.PHONY: local down logs ingest doctor pull pull-db pull-files deploy

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

deploy: ## Push code (modules/themes) to production
	ansible-playbook -i omeka/ansible/inventory.ini omeka/ansible/deploy.yml

pull: pull-db pull-files ## Pull both database and files from production

pull-db: ## Pull production database into local MariaDB
	@echo "Dumping production database..."
	ssh $(PROD_USER)@$(PROD_HOST) '\
		cd $(OMEKA_ROOT)/volume/config && \
		DB_USER=$$(grep "^user" database.ini | sed "s/.*= *//") && \
		DB_PASS=$$(grep "^password" database.ini | sed "s/.*= *//") && \
		DB_NAME=$$(grep "^dbname" database.ini | sed "s/.*= *//") && \
		mariadb-dump -u"$$DB_USER" -p"$$DB_PASS" "$$DB_NAME"' \
	| docker compose exec -T db mariadb -uomeka -pomeka omeka
	@echo "Database imported."

pull-files: ## Pull production uploaded files to local
	@echo "Syncing production files..."
	rsync -avz --compress --partial --progress \
		$(PROD_USER)@$(PROD_HOST):$(OMEKA_ROOT)/volume/files/ \
		omeka/volume/files/
	@echo "Files synced."

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

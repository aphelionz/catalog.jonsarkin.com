PYTHON := python
VENV ?= venv
ACT := source $(VENV)/bin/activate

.PHONY: all ingest docker-build-gpu docker-run-gpu docker-build-cpu docker-run-cpu

all: ingest

ingest:
	@echo "Starting full ingest..."
	@$(ACT) && $(PYTHON) fetch_omeka.py

docker-build-gpu:
	@echo "Building GPU image (CUDA 12.1 only)..."
	@docker build -t sarkin-ingest:gpu .

docker-run-gpu:
	@echo "Running GPU ingest container..."
	@docker run --rm --gpus all -e OPENAI_API_KEY -e OMEKA_TEMPLATE_ID=2 -e SEARCH_DB_PATH=/app/search_index/index.sqlite \
		-v $$(pwd)/.hf_cache:/app/.hf_cache -v $$(pwd)/search_index:/app/search_index sarkin-ingest:gpu

docker-build-cpu:
	@echo "Building CPU image..."
	@docker build -f Dockerfile.cpu -t sarkin-ingest:cpu .

docker-run-cpu:
	@echo "Running CPU ingest container..."
	@docker run --rm -e OPENAI_API_KEY -e OMEKA_TEMPLATE_ID=2 -e SEARCH_DB_PATH=/app/search_index/index.sqlite \
		-v $$(pwd)/.hf_cache:/app/.hf_cache -v $$(pwd)/search_index:/app/search_index sarkin-ingest:cpu

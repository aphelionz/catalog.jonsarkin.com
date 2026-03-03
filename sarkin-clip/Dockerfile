# GPU-only runner. Uses PyTorch base with CUDA 12.1 + cuDNN to avoid downloading giant wheels in CI.
FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.hf_cache \
    TRANSFORMERS_CACHE=/app/.hf_cache \
    QDRANT_URL=http://hyphae:6333 \
    QDRANT_COLLECTION=omeka_items \
    ANTHROPIC_MODEL=claude-sonnet-4-5-20241022 \
    SEARCH_DB_PATH=/app/search_index/index.sqlite

# Minimal system deps for pillow/vision
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install only what isn't in the base (torch/vision already present)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --upgrade pip && \
    pip3 install --no-cache-dir \
        open_clip_torch==3.2.0 \
        anthropic>=0.40.0 \
        qdrant-client==1.16.1 \
        pillow==10.4.0 \
        requests==2.32.5 \
        tqdm==4.67.1 && \
    pip3 cache purge

COPY embed_image_to_qdrant.py fetch_omeka.py image.jpg ./
COPY clip_api /app/clip_api

ENTRYPOINT ["python3", "fetch_omeka.py"]

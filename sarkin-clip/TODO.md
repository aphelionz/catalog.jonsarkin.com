# TODO

- Omeka metadata: map additional fields into the payload (year, subjects, curator notes, dominant color, collections/sets, item URL, owner, resource class, created/modified).
- Qdrant schema: migrate collection to larger CLIP dims (e.g., 768 for ViT-L/14-336 or SigLIP) and re-embed.
- CLI args: allow embed script to take inputs via flags/env instead of editing constants.
- Robust fetch: add retry/backoff and checksum caching for media downloads; skip already ingested items.
- OCR fallback: handle OpenAI OCR failures gracefully (retry, fallback to local EasyOCR/Tesseract if desired).
- Logging/metrics: basic timing + error logging per item; optionally push WER/CER when ground truth exists.
- Tests: smoke test for embed pipeline with a tiny image fixture; unit test Omeka field mapping.

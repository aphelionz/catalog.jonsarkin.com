# Segmentation Pipeline

Local SAM 2.1 segmentation + push-to-prod workflow for segment-level visual search.

## Architecture

Three search modalities, each with a different model:

| Model | Collection | What it does | Query type |
|-------|-----------|--------------|------------|
| CLIP (ViT-B-32) | `omeka_items` (512-dim) | Text search, full-image similarity | Text or image |
| DINOv2 patches | `sarkin_motif_patches_518` (768-dim) | Motif/pattern search | Image crop |
| DINOv2 segments | `sarkin_motif_segments` (768-dim) | Object-level search | Image crop |

CLIP cannot be replaced — it's the only model that encodes both text and images into the same space. DINOv2 is vision-only.

## How segmentation works

1. **SAM 2.1 Hiera Large** segments each artwork into 5-60 masked regions
2. **DINOv2 ViT-B/14** embeds each segment's CLS token (768-dim)
3. Masked segment JPEGs are saved to disk (gray background outside mask)
4. Vectors + metadata are upserted to Qdrant

SAM runs only at ingest time. At query time, only DINOv2 is needed (to embed the uploaded search image).

## Running locally (M4/MPS)

Segmentation runs natively on macOS — not in Docker — to use Metal GPU acceleration.

### Prerequisites

```bash
cd sarkin-clip
uv venv --python 3.11 .venv
SAM2_BUILD_CUDA=0 uv pip install -r requirements.local.txt
uv pip install torch torchvision pillow numpy httpx open_clip_torch fastapi pydantic
```

### Segment the corpus

```bash
# Local Docker stack must be running (omeka + qdrant)
make local

# Incremental — skips items already in the segment collection
make segment

# Full re-segment — replaces all existing segments
make segment-force
```

**Speed:** ~19s/item on M4 Max. Full corpus (~3700 items) takes ~19 hours.

**Output:**
- `sarkin-clip/segments/{omeka_id}/{idx}.jpg` — masked segment JPEGs
- `sarkin-clip/segments/{omeka_id}/meta.json` — bbox, area, stability scores
- Local Qdrant `sarkin_motif_segments` — DINOv2 CLS vectors (768-dim)

### Push to production

```bash
make push-segments
```

This does two things:
1. **rsync** segment JPEGs to `prod:/opt/catalog/segments/` (bind-mounted into clip-api container)
2. **SSH tunnel** to prod Qdrant, scrolls local segment vectors, upserts to prod collection

## Density classification

Items are classified into density tiers before segmentation. Two modes:

- **Metadata-derived** (`make classify`) — fast, uses item set membership and motif counts from MariaDB
- **OpenCV-based** (`make classify-opencv`) — downloads each image and computes edge density + white space percentage

Tiers are assigned by percentile thresholds (default: p_low=33, p_high=67):

| Tier | Density | SAM behavior |
|------|---------|-------------|
| `sparse` | Low edge density / few motifs | Fewer points, higher thresholds |
| `medium` | Moderate | Balanced preset |
| `dense` | High edge density / many motifs | More points, lower thresholds, multi-layer crops |

Use `make classify-stats` to see the distribution and `make reclassify` to re-tier from stored scores.

## SAM parameters (density-tiered presets)

Each density tier uses a different SAM preset:

| Parameter | Sparse | Medium | Dense |
|-----------|--------|--------|-------|
| `points_per_side` | 16 | 24 | 48 |
| `pred_iou_thresh` | 0.88 | 0.86 | 0.82 |
| `stability_score_thresh` | 0.92 | 0.90 | 0.88 |
| `crop_n_layers` | 0 | 1 | 2 |
| `crop_n_points_downscale_factor` | 2 | 2 | 1 |
| `min_mask_region_area` | 500 | 500 | 300 |
| Post-filter: min area % | 0.8% | 0.8% | 0.5% |
| Post-filter: max area % | 40% | 40% | 40% |
| `max_segments` | 40 | 60 | 100 |

## SAM playground

Interactive Gradio UI for experimenting with SAM parameters on individual images:

```bash
make sam-playground    # opens at http://localhost:7860
```

Two tabs:
- **Automatic Masks** — full SamAutomaticMaskGenerator with adjustable presets (sparse/medium/dense/custom), post-filtering, and mask overlay visualization
- **Prompted Prediction** — click to place point prompts, SamPredictor returns up to 3 candidate masks with IoU scores

## Prod deployment

SAM is **not** installed on prod. The prod clip-api image only has DINOv2 (for query-time embedding) and serves pre-computed segment JPEGs as static files.

Key env vars on prod:
- `SEGMENT_ENABLED=true` — segment search endpoint is active
- `SEGMENT_INGEST_ENABLED=false` — segment ingest endpoint returns 503
- `/opt/catalog/segments` bind-mounted to `/app/segments` in clip-api

## Qdrant point schema

```
Collection: sarkin_motif_segments
Vector: 768-dim cosine (DINOv2 CLS, int8 quantized)
Point ID: omeka_item_id * 1000 + segment_index

Payload:
  omeka_item_id: int
  omeka_url: str
  thumb_url: str
  segment_index: int
  segment_url: "/segments/{omeka_id}/{idx}.jpg"
  bbox: [x, y, w, h]
  area: int (pixels)
```

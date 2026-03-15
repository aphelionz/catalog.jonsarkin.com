"""MobileSAM automatic segmentation for artwork decomposition.

Uses the same lazy-load singleton pattern as dino.py and embeddings.py.
Only needed during batch segmentation and single-item ingest — NOT at query time.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from typing import Any, Dict, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Tunable parameters for Sarkin's dense, layered outsider art
POINTS_PER_SIDE = 16
PRED_IOU_THRESH = 0.86
STABILITY_SCORE_THRESH = 0.92
MIN_MASK_REGION_AREA = 500
CROP_N_LAYERS = 0
CROP_N_POINTS_DOWNSCALE_FACTOR = 1

# Post-filtering
MIN_AREA_PCT = 0.005  # discard segments < 0.5% of image area
MAX_AREA_PCT = 0.40   # discard segments > 40% of image area
MAX_SEGMENTS_PER_IMAGE = 40

_sam_cache: Tuple[Any, Any] | None = None  # (model, mask_generator)
_sam_lock = threading.Lock()


def _get_checkpoint_path() -> str:
    """Return path to MobileSAM checkpoint, downloading if needed."""
    cache_dir = os.environ.get("TORCH_HOME", os.path.join(os.getcwd(), ".hf_cache"))
    os.makedirs(cache_dir, exist_ok=True)
    checkpoint_path = os.path.join(cache_dir, "mobile_sam.pt")
    if not os.path.exists(checkpoint_path):
        import urllib.request
        url = "https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt"
        logger.info("Downloading MobileSAM checkpoint to %s ...", checkpoint_path)
        urllib.request.urlretrieve(url, checkpoint_path)
        logger.info("Download complete")
    return checkpoint_path


def _get_sam() -> Tuple[Any, Any]:
    """Lazy-load MobileSAM model and mask generator (singleton, thread-safe)."""
    global _sam_cache
    with _sam_lock:
        if _sam_cache is not None:
            return _sam_cache

        import torch
        from mobile_sam import sam_model_registry, SamAutomaticMaskGenerator

        checkpoint = _get_checkpoint_path()
        # Always run SAM on CPU:
        # - MPS doesn't support float64 ops that SAM's mask generator uses
        # - On small GPUs (6GB), loading SAM + DINOv2 together causes OOM
        # - MobileSAM is fast enough on CPU (~2-4s/image)
        device = "cpu"
        model = sam_model_registry["vit_t"](checkpoint=checkpoint)
        model.to(device)
        model.eval()

        mask_generator = SamAutomaticMaskGenerator(
            model=model,
            points_per_side=POINTS_PER_SIDE,
            pred_iou_thresh=PRED_IOU_THRESH,
            stability_score_thresh=STABILITY_SCORE_THRESH,
            min_mask_region_area=MIN_MASK_REGION_AREA,
            crop_n_layers=CROP_N_LAYERS,
            crop_n_points_downscale_factor=CROP_N_POINTS_DOWNSCALE_FACTOR,
        )

        _sam_cache = (model, mask_generator)
        logger.info("MobileSAM loaded on %s", device)
        return _sam_cache


def segment_image(image_bytes: bytes) -> List[Dict[str, Any]]:
    """Run automatic segmentation on an image.

    Returns a list of filtered segments, each containing:
        - mask: np.ndarray (H, W) bool
        - bbox: (x, y, w, h) tuple of ints
        - area: int (pixels)
        - area_pct: float (percentage of total image area)
        - stability_score: float
    """
    from PIL import Image

    _, mask_generator = _get_sam()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Cap input size to reduce memory — SAM works fine at 1024px
    MAX_DIM = 1024
    w, h = image.size
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    image_array = np.array(image)
    total_area = image_array.shape[0] * image_array.shape[1]

    import torch
    with torch.no_grad():
        raw_masks = mask_generator.generate(image_array)

    # Post-filter: extract only what we need, discard raw SAM output promptly
    filtered = []
    for m in raw_masks:
        area = m["area"]
        area_pct = area / total_area
        if area_pct < MIN_AREA_PCT or area_pct > MAX_AREA_PCT:
            continue
        filtered.append({
            "mask": m["segmentation"],  # bool ndarray (H, W)
            "bbox": tuple(m["bbox"]),   # (x, y, w, h)
            "area": area,
            "area_pct": round(area_pct, 4),
            "stability_score": round(m["stability_score"], 4),
        })
    del raw_masks, image_array

    # Sort by area descending, cap at max
    filtered.sort(key=lambda s: s["area"], reverse=True)
    filtered = filtered[:MAX_SEGMENTS_PER_IMAGE]

    return filtered

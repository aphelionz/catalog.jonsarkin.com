"""Automatic segmentation for artwork decomposition.

Supports three backends (checked in order):
  1. SAM 2.1 (sam2 package) — best quality, MPS support
  2. MobileSAM (mobile_sam package) — lightweight fallback for Docker
  3. Unavailable — import-safe, raises RuntimeError if called

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

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

SAM_BACKEND: str = "unavailable"  # "sam2", "mobile_sam", or "unavailable"

try:
    from sam2.build_sam import build_sam2  # noqa: F401
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator  # noqa: F401
    SAM_BACKEND = "sam2"
except ImportError:
    try:
        from mobile_sam import sam_model_registry, SamAutomaticMaskGenerator  # noqa: F401
        SAM_BACKEND = "mobile_sam"
    except ImportError:
        pass

SAM_AVAILABLE = SAM_BACKEND != "unavailable"

# ---------------------------------------------------------------------------
# Parameters — tuned per backend for Sarkin's dense, layered outsider art
# ---------------------------------------------------------------------------

# SAM 2.1 Hiera Large (quality-maximizing)
_SAM2_PARAMS = dict(
    points_per_side=32,
    pred_iou_thresh=0.86,
    stability_score_thresh=0.90,
    min_mask_region_area=500,
    crop_n_layers=1,
    crop_n_points_downscale_factor=1,
)

# MobileSAM ViT-T (lightweight fallback)
_MOBILE_SAM_PARAMS = dict(
    points_per_side=16,
    pred_iou_thresh=0.86,
    stability_score_thresh=0.92,
    min_mask_region_area=500,
    crop_n_layers=0,
    crop_n_points_downscale_factor=1,
)

# Post-filtering (shared)
MIN_AREA_PCT = 0.005   # discard segments < 0.5% of image area
MAX_AREA_PCT = 0.40    # discard segments > 40% of image area
MAX_SEGMENTS_PER_IMAGE_SAM2 = 60
MAX_SEGMENTS_PER_IMAGE_MOBILE = 40
MAX_DIM = 1024

# ---------------------------------------------------------------------------
# Singleton model cache
# ---------------------------------------------------------------------------

_sam_cache: Tuple[Any, Any] | None = None  # (model, mask_generator)
_sam_lock = threading.Lock()


def _get_mobile_sam_checkpoint() -> str:
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


def _get_sam2_checkpoint() -> Tuple[str, str]:
    """Return (model_cfg, checkpoint_path) for SAM 2.1 Hiera Large."""
    cache_dir = os.environ.get("TORCH_HOME", os.path.join(os.getcwd(), ".hf_cache"))
    os.makedirs(cache_dir, exist_ok=True)
    checkpoint_path = os.path.join(cache_dir, "sam2.1_hiera_large.pt")
    model_cfg = "configs/sam2.1/sam2.1_hiera_l.yaml"
    if not os.path.exists(checkpoint_path):
        import urllib.request
        url = "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt"
        logger.info("Downloading SAM 2.1 Hiera Large checkpoint to %s ...", checkpoint_path)
        urllib.request.urlretrieve(url, checkpoint_path)
        logger.info("Download complete (%.0f MB)", os.path.getsize(checkpoint_path) / 1e6)
    return model_cfg, checkpoint_path


def _get_sam() -> Tuple[Any, Any]:
    """Lazy-load SAM model and mask generator (singleton, thread-safe)."""
    global _sam_cache
    with _sam_lock:
        if _sam_cache is not None:
            return _sam_cache

        if not SAM_AVAILABLE:
            raise RuntimeError(
                "No SAM backend installed. Install sam2 (recommended) or mobile_sam."
            )

        import torch

        if SAM_BACKEND == "sam2":
            _sam_cache = _load_sam2(torch)
        else:
            _sam_cache = _load_mobile_sam(torch)

        return _sam_cache


def _load_sam2(torch: Any) -> Tuple[Any, Any]:
    """Load SAM 2.1 Hiera Large with MPS support."""
    from sam2.build_sam import build_sam2
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

    model_cfg, checkpoint = _get_sam2_checkpoint()

    if torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")

    model = build_sam2(model_cfg, checkpoint, device=device)
    mask_generator = SAM2AutomaticMaskGenerator(
        model=model,
        **_SAM2_PARAMS,
    )

    logger.info("SAM 2.1 Hiera Large loaded on %s", device)
    return (model, mask_generator)


def _load_mobile_sam(torch: Any) -> Tuple[Any, Any]:
    """Load MobileSAM ViT-T (CPU-only fallback)."""
    from mobile_sam import sam_model_registry, SamAutomaticMaskGenerator

    checkpoint = _get_mobile_sam_checkpoint()
    # MobileSAM on CPU: MPS lacks float64 support, small GPUs OOM
    device = "cpu"
    model = sam_model_registry["vit_t"](checkpoint=checkpoint)
    model.to(device)
    model.eval()

    mask_generator = SamAutomaticMaskGenerator(
        model=model,
        **_MOBILE_SAM_PARAMS,
    )

    logger.info("MobileSAM loaded on %s", device)
    return (model, mask_generator)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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

    # Cap input size to reduce memory
    w, h = image.size
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    image_array = np.array(image)
    total_area = image_array.shape[0] * image_array.shape[1]

    import torch
    with torch.no_grad():
        raw_masks = mask_generator.generate(image_array)

    # Post-filter
    max_segments = (
        MAX_SEGMENTS_PER_IMAGE_SAM2
        if SAM_BACKEND == "sam2"
        else MAX_SEGMENTS_PER_IMAGE_MOBILE
    )

    filtered = []
    for m in raw_masks:
        area = m["area"]
        area_pct = area / total_area
        if area_pct < MIN_AREA_PCT or area_pct > MAX_AREA_PCT:
            continue
        filtered.append({
            "mask": m["segmentation"],  # bool ndarray (H, W)
            "bbox": tuple(int(v) for v in m["bbox"]),  # (x, y, w, h)
            "area": area,
            "area_pct": round(area_pct, 4),
            "stability_score": round(m["stability_score"], 4),
        })
    del raw_masks, image_array

    # Sort by area descending, cap at max
    filtered.sort(key=lambda s: s["area"], reverse=True)
    filtered = filtered[:max_segments]

    return filtered

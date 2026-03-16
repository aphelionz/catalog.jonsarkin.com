"""Segmentation for artwork decomposition.

Supports three backends (checked in order):
  1. SAM 2.1 (sam2 package) — best quality, MPS support
  2. MobileSAM (mobile_sam package) — lightweight fallback for Docker
  3. Unavailable — import-safe, raises RuntimeError if called

Two modes:
  - Automatic mask generation: segment_image() / segment_image_custom()
  - Prompted prediction (SAM2 only): predict_from_prompts()
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
# Density-tiered SAM presets
# ---------------------------------------------------------------------------

# Each preset has SAM generator params + post-filtering params.
# Generator params are passed to SAM2AutomaticMaskGenerator / SamAutomaticMaskGenerator.
# Post-filtering params (min_area_pct, max_area_pct, max_segments) are applied after.
SAM_PRESETS = {
    "sparse": {
        # Clear backgrounds, isolated elements, small motifs (cow skulls, stick figures)
        "points_per_side": 32,
        "pred_iou_thresh": 0.88,
        "stability_score_thresh": 0.92,
        "min_mask_region_area": 200,
        "crop_n_layers": 1,
        "crop_n_points_downscale_factor": 2,
        # Post-filtering
        "min_area_pct": 0.002,
        "max_area_pct": 0.50,
        "max_segments": 60,
    },
    "medium": {
        # Moderate overlap, mixed elements — close to previous defaults
        "points_per_side": 24,
        "pred_iou_thresh": 0.86,
        "stability_score_thresh": 0.90,
        "min_mask_region_area": 500,
        "crop_n_layers": 1,
        "crop_n_points_downscale_factor": 2,
        # Post-filtering
        "min_area_pct": 0.005,
        "max_area_pct": 0.40,
        "max_segments": 40,
    },
    "dense": {
        # Packed, layered, overlapping — aggressive prompting + filtering
        "points_per_side": 32,
        "pred_iou_thresh": 0.82,
        "stability_score_thresh": 0.85,
        "min_mask_region_area": 800,
        "crop_n_layers": 2,
        "crop_n_points_downscale_factor": 2,
        # Post-filtering
        "min_area_pct": 0.008,
        "max_area_pct": 0.35,
        "max_segments": 40,
    },
}

# Keys that are SAM generator constructor args (vs post-filtering)
_GENERATOR_KEYS = {
    "points_per_side", "pred_iou_thresh", "stability_score_thresh",
    "min_mask_region_area", "crop_n_layers", "crop_n_points_downscale_factor",
}

MAX_DIM = 1024

# ---------------------------------------------------------------------------
# Legacy parameter dicts (kept for reference / MobileSAM fallback)
# ---------------------------------------------------------------------------

_SAM2_PARAMS = {k: v for k, v in SAM_PRESETS["medium"].items() if k in _GENERATOR_KEYS}
_MOBILE_SAM_PARAMS = dict(
    points_per_side=16,
    pred_iou_thresh=0.86,
    stability_score_thresh=0.92,
    min_mask_region_area=500,
    crop_n_layers=0,
    crop_n_points_downscale_factor=1,
)

# ---------------------------------------------------------------------------
# Singleton model cache — model loaded once, generators cached per tier
# ---------------------------------------------------------------------------

_model_cache: Any | None = None
_generator_cache: Dict[str, Any] = {}
_predictor_cache: Any | None = None
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


def _get_model() -> Any:
    """Lazy-load the SAM model (singleton, thread-safe). Returns model only."""
    global _model_cache
    with _sam_lock:
        if _model_cache is not None:
            return _model_cache

        if not SAM_AVAILABLE:
            raise RuntimeError(
                "No SAM backend installed. Install sam2 (recommended) or mobile_sam."
            )

        import torch

        if SAM_BACKEND == "sam2":
            _model_cache = _load_sam2_model(torch)
        else:
            _model_cache = _load_mobile_sam_model(torch)

        return _model_cache


def _load_sam2_model(torch: Any) -> Any:
    """Load SAM 2.1 Hiera Large model (without generator)."""
    from sam2.build_sam import build_sam2

    model_cfg, checkpoint = _get_sam2_checkpoint()

    if torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")

    model = build_sam2(model_cfg, checkpoint, device=device)
    logger.info("SAM 2.1 Hiera Large loaded on %s", device)
    return model


def _load_mobile_sam_model(torch: Any) -> Any:
    """Load MobileSAM ViT-T model (without generator)."""
    from mobile_sam import sam_model_registry

    checkpoint = _get_mobile_sam_checkpoint()
    device = "cpu"
    model = sam_model_registry["vit_t"](checkpoint=checkpoint)
    model.to(device)
    model.eval()
    logger.info("MobileSAM loaded on %s", device)
    return model


def _get_generator(tier: str = "medium") -> Any:
    """Get or create a mask generator for the given density tier."""
    with _sam_lock:
        if tier in _generator_cache:
            return _generator_cache[tier]

    model = _get_model()
    preset = SAM_PRESETS.get(tier, SAM_PRESETS["medium"])
    gen_params = {k: v for k, v in preset.items() if k in _GENERATOR_KEYS}

    with _sam_lock:
        # Double-check after acquiring lock
        if tier in _generator_cache:
            return _generator_cache[tier]

        if SAM_BACKEND == "sam2":
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
            generator = SAM2AutomaticMaskGenerator(model=model, **gen_params)
        else:
            from mobile_sam import SamAutomaticMaskGenerator
            # MobileSAM uses its own fixed params regardless of tier
            generator = SamAutomaticMaskGenerator(model=model, **_MOBILE_SAM_PARAMS)

        _generator_cache[tier] = generator
        logger.info("Created %s mask generator for tier '%s'", SAM_BACKEND, tier)
        return generator


def _get_sam() -> Tuple[Any, Any]:
    """Legacy API: return (model, default_generator) for backward compat."""
    model = _get_model()
    generator = _get_generator("medium")
    return (model, generator)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def segment_image(image_bytes: bytes, tier: str = "medium") -> List[Dict[str, Any]]:
    """Run automatic segmentation on an image.

    Args:
        image_bytes: raw image bytes (JPEG, PNG, etc.)
        tier: density tier ('sparse', 'medium', 'dense') — selects SAM preset

    Returns a list of filtered segments, each containing:
        - mask: np.ndarray (H, W) bool
        - bbox: (x, y, w, h) tuple of ints
        - area: int (pixels)
        - area_pct: float (percentage of total image area)
        - stability_score: float
    """
    from PIL import Image

    mask_generator = _get_generator(tier)
    preset = SAM_PRESETS.get(tier, SAM_PRESETS["medium"])

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

    # Post-filter using tier-specific thresholds
    min_area_pct = preset.get("min_area_pct", 0.005)
    max_area_pct = preset.get("max_area_pct", 0.40)
    max_segments = preset.get("max_segments", 40)

    filtered = []
    for m in raw_masks:
        area = m["area"]
        area_pct = area / total_area
        if area_pct < min_area_pct or area_pct > max_area_pct:
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


def segment_image_custom(
    image_array: np.ndarray,
    generator_params: Dict[str, Any],
    min_area_pct: float = 0.005,
    max_area_pct: float = 0.40,
    max_segments: int = 40,
) -> Tuple[List[Dict[str, Any]], int]:
    """Run automatic segmentation with arbitrary generator params.

    Unlike segment_image(), this builds a fresh generator each call (the heavy
    model is still cached).  Intended for interactive parameter tuning.

    Returns (filtered_segments, raw_mask_count).
    """
    model = _get_model()

    if SAM_BACKEND == "sam2":
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        generator = SAM2AutomaticMaskGenerator(model=model, **generator_params)
    else:
        from mobile_sam import SamAutomaticMaskGenerator
        generator = SamAutomaticMaskGenerator(model=model, **generator_params)

    total_area = image_array.shape[0] * image_array.shape[1]

    import torch
    with torch.no_grad():
        raw_masks = generator.generate(image_array)

    raw_count = len(raw_masks)
    filtered = []
    for m in raw_masks:
        area = m["area"]
        pct = area / total_area
        if pct < min_area_pct or pct > max_area_pct:
            continue
        filtered.append({
            "mask": m["segmentation"],
            "bbox": tuple(int(v) for v in m["bbox"]),
            "area": area,
            "area_pct": round(pct, 4),
            "stability_score": round(m["stability_score"], 4),
        })
    del raw_masks

    filtered.sort(key=lambda s: s["area"], reverse=True)
    filtered = filtered[:max_segments]
    return filtered, raw_count


# ---------------------------------------------------------------------------
# Prompted prediction (SAM2 only)
# ---------------------------------------------------------------------------

PREDICTOR_AVAILABLE = SAM_BACKEND == "sam2"


def get_predictor() -> Any:
    """Lazy-load SAM2ImagePredictor (singleton, thread-safe).

    Raises RuntimeError if the backend is not SAM2.
    """
    global _predictor_cache
    if not PREDICTOR_AVAILABLE:
        raise RuntimeError(
            "Prompted prediction requires SAM2 (sam2 package). "
            "MobileSAM only supports automatic mask generation."
        )
    with _sam_lock:
        if _predictor_cache is not None:
            return _predictor_cache

    model = _get_model()

    from sam2.sam2_image_predictor import SAM2ImagePredictor
    with _sam_lock:
        if _predictor_cache is None:
            _predictor_cache = SAM2ImagePredictor(model)
            logger.info("SAM2ImagePredictor created")
        return _predictor_cache


def predict_from_prompts(
    image_array: np.ndarray,
    point_coords: np.ndarray | None = None,
    point_labels: np.ndarray | None = None,
    box: np.ndarray | None = None,
    multimask_output: bool = True,
) -> List[Dict[str, Any]]:
    """Run prompted prediction on an image.

    Args:
        image_array: (H, W, 3) uint8 RGB array
        point_coords: (N, 2) array of (x, y) points, or None
        point_labels: (N,) array of 1=foreground / 0=background, or None
        box: (4,) array [x1, y1, x2, y2], or None
        multimask_output: if True returns 3 candidate masks, else 1

    Returns list of dicts with mask (bool H,W), score, area.
    """
    predictor = get_predictor()

    import torch
    with torch.inference_mode():
        predictor.set_image(image_array)
        masks, scores, _ = predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            box=box,
            multimask_output=multimask_output,
        )

    results = []
    for i in range(masks.shape[0]):
        results.append({
            "mask": masks[i],
            "score": float(scores[i]),
            "area": int(masks[i].sum()),
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results

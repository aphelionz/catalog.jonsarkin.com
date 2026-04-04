"""DINOv2 ViT-B/14 patch-level embeddings for motif search.

Extracts per-patch embeddings from artworks and mean-pooled query vectors
from motif crops. Uses the same singleton/lazy-load pattern as embeddings.py.
"""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import Any, List, Tuple

import numpy as np

DINO_MODEL_NAME = "dinov2_vitb14_reg"
DINO_DIM = 768
DINO_INPUT_SIZE = 518
DINO_PATCH_SIZE = 14
DINO_GRID_SIZE = DINO_INPUT_SIZE // DINO_PATCH_SIZE  # 16
DINO_NUM_PREFIX_TOKENS = 5  # 1 CLS + 4 register tokens

_dino_cache: Tuple[str, Any, Any] | None = None  # (device, model, transform)
_dino_lock = threading.Lock()
_dino_encode_lock = threading.Lock()


def _ensure_torch_home() -> None:
    """Point torch.hub cache to the same dir used by HF/CLIP weights."""
    if "TORCH_HOME" not in os.environ:
        cache_dir = str(Path(".hf_cache").resolve())
        os.environ["TORCH_HOME"] = cache_dir
        os.environ.setdefault("HF_HOME", cache_dir)


def _get_dino() -> Tuple[str, Any, Any]:
    """Lazy-load DINOv2 model (singleton, thread-safe)."""
    global _dino_cache
    with _dino_lock:
        if _dino_cache is not None:
            return _dino_cache

        _ensure_torch_home()
        import torch
        import torchvision.transforms as T

        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        model = torch.hub.load(
            "facebookresearch/dinov2",
            DINO_MODEL_NAME,
            pretrained=True,
        )
        model.to(device)
        model.eval()

        transform = T.Compose([
            T.Resize(DINO_INPUT_SIZE, interpolation=T.InterpolationMode.BICUBIC),
            T.CenterCrop(DINO_INPUT_SIZE),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

        _dino_cache = (device, model, transform)
        return _dino_cache


def extract_patches(image_bytes: bytes) -> Tuple[List[List[float]], int, int]:
    """Extract per-patch embeddings from an artwork image.

    Returns:
        (patch_vectors, grid_h, grid_w) where each patch_vector is a
        768-dim L2-normalized list of floats.
    """
    from PIL import Image
    import torch
    import torch.nn.functional as F

    device, model, transform = _get_dino()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    tensor = transform(image).unsqueeze(0).to(device)

    with _dino_encode_lock, torch.no_grad():
        features = model.forward_features(tensor)
        # forward_features returns a dict with pre-separated, pre-normalized tokens:
        #   x_norm_patchtokens: [1, 256, 768]
        patches = features["x_norm_patchtokens"]  # [1, 256, 768]
        patches = F.normalize(patches, dim=-1)

    patch_list = patches[0].cpu().tolist()
    return patch_list, DINO_GRID_SIZE, DINO_GRID_SIZE


def embed_query_crop(image_bytes: bytes) -> List[float]:
    """Embed a motif crop as a single query vector (mean-pooled patches).

    Returns a 768-dim L2-normalized vector.
    """
    from PIL import Image
    import torch
    import torch.nn.functional as F

    device, model, transform = _get_dino()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    tensor = transform(image).unsqueeze(0).to(device)

    with _dino_encode_lock, torch.no_grad():
        features = model.forward_features(tensor)
        patches = features["x_norm_patchtokens"]  # [1, 256, 768]
        query = patches.mean(dim=1)  # [1, 768]
        query = F.normalize(query, dim=-1)

    return query[0].cpu().tolist()

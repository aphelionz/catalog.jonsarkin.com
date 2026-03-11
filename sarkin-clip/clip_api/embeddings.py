from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import Any, List, Tuple

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
EMBED_MODEL = f"{MODEL_NAME}:{PRETRAINED}"

# Cache tuple: (device, model, tokenizer, preprocess)
_model_cache: Tuple[str, Any, Any, Any] | None = None
_model_lock = threading.Lock()
_encode_lock = threading.Lock()


def _ensure_hf_cache() -> None:
    if "HF_HOME" not in os.environ:
        os.environ["HF_HOME"] = str(Path(".hf_cache").resolve())
    os.environ.setdefault("TRANSFORMERS_CACHE", os.environ["HF_HOME"])


def _get_clip() -> Tuple[str, Any, Any, Any]:
    global _model_cache
    with _model_lock:
        if _model_cache is not None:
            return _model_cache
        _ensure_hf_cache()
        import open_clip  # local import to keep optional for tests
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            MODEL_NAME, pretrained=PRETRAINED, cache_dir=os.environ["HF_HOME"]
        )
        tokenizer = open_clip.get_tokenizer(MODEL_NAME)
        model.to(device)
        model.eval()
        _model_cache = (device, model, tokenizer, preprocess)
        return _model_cache


def embed_text(text: str) -> List[float]:
    device, model, tokenizer, _ = _get_clip()
    import torch

    tokens = tokenizer([text]).to(device)
    with _encode_lock, torch.no_grad():
        features = model.encode_text(tokens)
        features /= features.norm(dim=-1, keepdim=True)
        return features[0].cpu().tolist()


def embed_image(image_bytes: bytes) -> List[float]:
    from PIL import Image

    device, model, _, preprocess = _get_clip()
    import torch

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_tensor = preprocess(image).unsqueeze(0).to(device)
    with _encode_lock, torch.no_grad():
        features = model.encode_image(image_tensor)
        features /= features.norm(dim=-1, keepdim=True)
        return features[0].cpu().tolist()

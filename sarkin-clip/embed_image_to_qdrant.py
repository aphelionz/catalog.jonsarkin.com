import sys
import os
from pathlib import Path
import time
import threading

import torch
from PIL import Image
import open_clip
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from clip_api.preprocess import PREPROC_VERSION
from clip_api.search_index import upsert_document
# Force Hugging Face caches into repo-local folder to avoid home permissions
HF_CACHE = Path(".hf_cache").resolve()
os.environ["HF_HOME"] = str(HF_CACHE)
os.environ["TRANSFORMERS_CACHE"] = str(HF_CACHE)
os.environ["HF_HUB_CACHE"] = str(HF_CACHE)

# --- defaults / config (overridden by caller) ---
DEFAULT_IMAGE_PATH = Path("image.jpg")  # adjust if needed
DEFAULT_OMEKA_ITEM_ID = 1
DEFAULT_OMEKA_DESCRIPTION = "Placeholder Omeka description for the item."
DEFAULT_COLLECTION = "debug"
DEFAULT_YEAR = 2025
DEFAULT_SUBJECTS = ["beat poets", "classic rock", "despairing", "funny"]
DEFAULT_CURATOR_NOTES = ["jittery linework", "psychedelic", "humor + dread"]
DEFAULT_DOMINANT_COLOR = "blue"
DEFAULT_OMEKA_URL = "https://catalog.jonsarkin.com/"
DEFAULT_THUMB_URL = "https://catalog.jonsarkin.com/"
CATALOG_VERSION = 2

QDRANT_URL = os.getenv("QDRANT_URL", "http://hyphae:6333")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "omeka_items")

# Use a 512-dim CLIP to match Qdrant schema.
MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
EMBED_MODEL = f"{MODEL_NAME}:{PRETRAINED}"

# Keep HF cache in repo to avoid home permissions
os.environ.setdefault("HF_HOME", str(Path(".hf_cache").resolve()))
os.environ.setdefault("TRANSFORMERS_CACHE", os.environ["HF_HOME"])

_model_cache = None
_model_lock = threading.Lock()
_encode_lock = threading.Lock()


def get_clip():
    """Singleton CLIP load to avoid reloading per item."""
    global _model_cache
    with _model_lock:
        if _model_cache is not None:
            return _model_cache
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            MODEL_NAME, pretrained=PRETRAINED, cache_dir=str(HF_CACHE)
        )
        tokenizer = open_clip.get_tokenizer(MODEL_NAME)
        model.to(device)
        model.eval()
        _model_cache = (device, model, preprocess, tokenizer)
        return _model_cache


def embed_and_upsert(
    image_path: Path,
    omeka_item_id: int = DEFAULT_OMEKA_ITEM_ID,
    omeka_description: str = DEFAULT_OMEKA_DESCRIPTION,
    collection: str = DEFAULT_COLLECTION,
    year: int = DEFAULT_YEAR,
    subjects=None,
    curator_notes=None,
    dominant_color: str = DEFAULT_DOMINANT_COLOR,
    omeka_url: str = DEFAULT_OMEKA_URL,
    thumb_url: str = DEFAULT_THUMB_URL,
):
    subjects = subjects if subjects is not None else DEFAULT_SUBJECTS
    curator_notes = curator_notes if curator_notes is not None else DEFAULT_CURATOR_NOTES

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    t_total_start = time.perf_counter()

    device, model, preprocess, tokenizer = get_clip()

    image = Image.open(image_path).convert("RGB")
    image_tensor = preprocess(image).unsqueeze(0).to(device)

    t_img_start = time.perf_counter()
    with _encode_lock, torch.no_grad():
        image_features = model.encode_image(image_tensor)
        image_features /= image_features.norm(dim=-1, keepdim=True)
        visual_vec = image_features[0].cpu().tolist()  # 512-dim
    t_img = time.perf_counter() - t_img_start

    # OCR fields are populated by the enrichment pipeline (make enrich-batch),
    # not during ingest.  Keep empty placeholders for schema compatibility.
    ocr_text_raw = ""
    ocr_text_norm = ""

    text_blob_sections = [
        f"Description: {omeka_description}",
        f"Subjects / Tags / Themes: {', '.join(subjects)}",
    ]
    if year:
        text_blob_sections.append(f"Year: {year}")
    if collection:
        text_blob_sections.append(f"Collection: {collection}")
    if curator_notes:
        text_blob_sections.append(f"Curator Notes: {', '.join(curator_notes)}")

    text_blob = "\n".join(text_blob_sections)
    text_tokens = tokenizer([text_blob]).to(device)

    t_txt_start = time.perf_counter()
    with _encode_lock, torch.no_grad():
        text_features = model.encode_text(text_tokens)
        text_features /= text_features.norm(dim=-1, keepdim=True)
        text_vec = text_features[0].cpu().tolist()
    t_txt = time.perf_counter() - t_txt_start
    updated_at = int(time.time())

    client = QdrantClient(url=QDRANT_URL)
    point = PointStruct(
        id=omeka_item_id,
        vector={
            "visual_vec": visual_vec,
            "text_vec_clip": text_vec,
        },
        payload={
            "omeka_item_id": omeka_item_id,
            "omeka_description": omeka_description,
            "collection": collection,
            "year": year,
            "subjects": subjects,
            "curator_notes": curator_notes,
            "dominant_color": dominant_color,
            "omeka_url": omeka_url,
            "thumb_url": thumb_url,
            "ocr_text": ocr_text_raw,
            "ocr_text_raw": ocr_text_raw,
            "ocr_text_norm": ocr_text_norm,
            "text_blob": text_blob,
            "catalog_version": CATALOG_VERSION,
            "embed_model": EMBED_MODEL,
            "preproc_version": PREPROC_VERSION,
            "updated_at": updated_at,
        },
    )
    client.upsert(collection_name=COLLECTION_NAME, points=[point])
    upsert_document(
        {
            "omeka_item_id": omeka_item_id,
            "catalog_version": CATALOG_VERSION,
            "omeka_url": omeka_url,
            "thumb_url": thumb_url,
            "omeka_description": omeka_description,
            "subjects": ", ".join(subjects),
            "mediums": "",
            "years": str(year) if year else "",
            "curator_notes": ", ".join(curator_notes) if curator_notes else "",
            "ocr_text_raw": ocr_text_raw,
            "ocr_text_norm": ocr_text_norm,
            "text_blob": text_blob,
        }
    )
    t_total = time.perf_counter() - t_total_start
    print(
        f"Upserted point into Qdrant: {point.id} | timing (s) clip_image={t_img:.2f} clip_text={t_txt:.2f} total={t_total:.2f}"
    )


if __name__ == "__main__":
    # Allows running directly with default placeholders
    try:
        embed_and_upsert(DEFAULT_IMAGE_PATH)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

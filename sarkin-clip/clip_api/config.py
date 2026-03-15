from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

MAX_LIMIT = 200


def _env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer") from exc


def _env_float(key: str) -> Optional[float]:
    value = os.getenv(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be a float") from exc


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise RuntimeError(f"{key} must be a boolean")


@dataclass(frozen=True)
class Settings:
    qdrant_url: str
    qdrant_collection: str
    vector_name: str
    text_vector_name: str
    qdrant_api_key: Optional[str]
    default_limit: int
    default_score_threshold: Optional[float]
    similar_enabled: bool
    text_search_enabled: bool
    search_db_path: str
    search_mode: str
    search_semantic_weight: float
    search_rrf_k: int
    disable_hybrid_boost: bool
    dino_collection: str
    dino_enabled: bool
    segment_collection: str
    segment_enabled: bool
    segment_dir: str
    max_limit: int = MAX_LIMIT


def load_settings() -> Settings:
    qdrant_url = os.getenv("QDRANT_URL", "http://hyphae:6333").rstrip("/")
    disable_text_search = _env_bool("DISABLE_TEXT_SEARCH", False)
    search_semantic_weight = _env_float("SEARCH_SEMANTIC_WEIGHT")
    disable_hybrid_boost = _env_bool("DISABLE_HYBRID_BOOST", False)
    return Settings(
        qdrant_url=qdrant_url,
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "omeka_items"),
        vector_name=os.getenv("VECTOR_NAME", "visual_vec"),
        text_vector_name=os.getenv("TEXT_VECTOR_NAME", "text_vec_clip"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY"),
        default_limit=_env_int("DEFAULT_LIMIT", 30),
        default_score_threshold=_env_float("DEFAULT_SCORE_THRESHOLD"),
        similar_enabled=_env_bool("SIMILAR_ENABLED", True),
        text_search_enabled=not disable_text_search,
        search_db_path=os.getenv("SEARCH_DB_PATH", ".search_index.sqlite"),
        search_mode=os.getenv("SEARCH_MODE", "hybrid"),
        search_semantic_weight=0.6 if search_semantic_weight is None else search_semantic_weight,
        search_rrf_k=_env_int("SEARCH_RRF_K", 60),
        disable_hybrid_boost=disable_hybrid_boost,
        dino_collection=os.getenv("DINO_COLLECTION", "sarkin_motif_patches_518"),
        dino_enabled=_env_bool("DINO_ENABLED", True),
        segment_collection=os.getenv("SEGMENT_COLLECTION", "sarkin_motif_segments"),
        segment_enabled=_env_bool("SEGMENT_ENABLED", True),
        segment_dir=os.getenv("SEGMENT_DIR", "/app/segments"),
    )

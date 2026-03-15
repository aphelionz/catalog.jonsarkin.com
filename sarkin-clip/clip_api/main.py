from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from pathlib import Path

logger = logging.getLogger("uvicorn.error")

from fastapi import FastAPI, HTTPException, UploadFile

from clip_api import embeddings
from clip_api.config import Settings, load_settings
from clip_api.models import (
    DinoIngestRequest,
    IconographyBatchItem,
    IconographyBatchResponse,
    IconographyResponse,
    ImageSearchResponse,
    IngestRequest,
    IngestResponse,
    MatchItem,
    MotifDetail,
    MotifMatchItem,
    MotifSearchResponse,
    SearchResponse,
    SearchResult,
    SegmentIngestRequest,
    SegmentMatchItem,
    SegmentSearchResponse,
    SimilarResponse,
)
from clip_api.preprocess import PREPROC_VERSION, compose_text_blob, extract_tags_text, tokenize
from clip_api.qdrant import QdrantError, QdrantUnavailable, close_client, extract_error_message, request_json
from clip_api.search_index import SearchIndexError, SearchIndexUnavailable, search as search_index

app = FastAPI(title="clip-api", version="1.0")

ALLOWED_PAYLOAD_FIELDS = {"omeka_item_id", "omeka_url", "thumb_url", "catalog_version"}
SEARCH_PAYLOAD_FIELDS = {
    "omeka_item_id",
    "omeka_url",
    "thumb_url",
    "text_blob",
    "ocr_text",
    "ocr_text_raw",
    "omeka_description",
    "description",
    "curator_notes",
    "subjects",
    "tags",
    "subject",
    "tag",
}
HEALTH_OK = "ok"
HEALTH_DISABLED = "disabled"
HEALTH_DEGRADED = "degraded"
TEXT_SEARCH_LIMIT_DEFAULT = 10
TEXT_SEARCH_LIMIT_MAX = 50
TEXT_SNIPPET_LIMIT = 240
SEARCH_MODES = {"semantic", "exact", "hybrid"}
TAG_MATCH_BOOST = 0.1
BLOB_MATCH_BOOST = 0.03


def _settings() -> Settings:
    return load_settings()


@app.on_event("startup")
def _ensure_dino_collection() -> None:
    """Auto-create the DINOv2 patch collection if it doesn't exist."""
    from clip_api.dino import DINO_DIM

    settings = _settings()
    if not settings.dino_enabled:
        return
    url = f"{settings.qdrant_url}/collections/{settings.dino_collection}"
    try:
        resp = request_json("GET", url, headers=_headers(settings), timeout=5.0)
        if resp.is_success:
            return  # already exists
    except QdrantUnavailable:
        logger.warning("Qdrant unavailable, skipping DINOv2 collection check")
        return
    try:
        request_json(
            "PUT",
            url,
            headers=_headers(settings),
            json={
                "vectors": {"size": DINO_DIM, "distance": "Cosine"},
                "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
                "optimizers_config": {"memmap_threshold": 20000},
            },
            timeout=10.0,
        )
        logger.info("Created DINOv2 patch collection: %s", settings.dino_collection)
    except Exception:
        logger.warning("Could not create DINOv2 collection %s", settings.dino_collection)


@app.on_event("startup")
def _ensure_segment_collection() -> None:
    """Auto-create the SAM segment collection if it doesn't exist."""
    from clip_api.dino import DINO_DIM

    settings = _settings()
    if not settings.segment_enabled:
        return
    url = f"{settings.qdrant_url}/collections/{settings.segment_collection}"
    try:
        resp = request_json("GET", url, headers=_headers(settings), timeout=5.0)
        if resp.is_success:
            return
    except QdrantUnavailable:
        logger.warning("Qdrant unavailable, skipping segment collection check")
        return
    try:
        request_json(
            "PUT",
            url,
            headers=_headers(settings),
            json={
                "vectors": {"size": DINO_DIM, "distance": "Cosine"},
                "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
                "optimizers_config": {"memmap_threshold": 20000},
            },
            timeout=10.0,
        )
        logger.info("Created segment collection: %s", settings.segment_collection)
    except Exception:
        logger.warning("Could not create segment collection %s", settings.segment_collection)


@app.on_event("startup")
def _mount_segment_static_files() -> None:
    """Mount the segments directory for serving segment JPEGs."""
    import os
    from fastapi.staticfiles import StaticFiles

    settings = _settings()
    segment_dir = settings.segment_dir
    os.makedirs(segment_dir, exist_ok=True)
    app.mount("/segments", StaticFiles(directory=segment_dir), name="segments")
    logger.info("Mounted segment images from %s", segment_dir)


@app.on_event("startup")
def _warm_text_model() -> None:
    settings = _settings()
    if not settings.text_search_enabled:
        return
    try:
        embeddings.embed_text("warmup")
    except Exception:
        # Don't fail startup if the model can't warm.
        return


@app.on_event("startup")
def _warm_segment_models() -> None:
    """Pre-load segment models at startup to avoid OOM from concurrent lazy-loading."""
    settings = _settings()
    if settings.segment_enabled:
        try:
            from clip_api import dino
            dino._get_dino()
            logger.info("DINOv2 model pre-loaded (segment query)")
        except Exception as e:
            logger.warning("Failed to pre-load DINOv2: %s", e)
    if settings.segment_ingest_enabled:
        try:
            from clip_api import sam
            sam._get_sam()
            logger.info("SAM model pre-loaded (segment ingest)")
        except Exception as e:
            logger.warning("Failed to pre-load SAM: %s", e)


@app.on_event("shutdown")
def _close_qdrant_client() -> None:
    close_client()


def _parse_int_param(name: str, raw: Optional[str], *, min_value: Optional[int] = None, max_value: Optional[int] = None) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise HTTPException(status_code=400, detail=f"{name} must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise HTTPException(status_code=400, detail=f"{name} must be <= {max_value}")
    return value


def _parse_float_param(
    name: str,
    raw: Optional[str],
    *,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be a float") from exc
    if min_value is not None and value < min_value:
        raise HTTPException(status_code=400, detail=f"{name} must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise HTTPException(status_code=400, detail=f"{name} must be <= {max_value}")
    return value


def _parse_point_id(raw: str) -> Any:
    if raw.isdigit():
        return int(raw)
    return raw


def _normalize_id(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return raw


def _ids_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left) == str(right)


def _normalize_catalog_version(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_item_payload(payload: Dict[str, Any], point_id: Any) -> Dict[str, Any]:
    omeka_item_id = payload.get("omeka_item_id")
    if omeka_item_id is None:
        omeka_item_id = point_id
    return {
        "omeka_item_id": omeka_item_id,
        "omeka_url": payload.get("omeka_url"),
        "thumb_url": payload.get("thumb_url"),
        "catalog_version": _normalize_catalog_version(payload.get("catalog_version")),
    }


def _truncate_text(text: str, max_len: int = TEXT_SNIPPET_LIMIT) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    trimmed = text[: max_len - 3].rstrip()
    return f"{trimmed}..."


def _build_snippet(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    snippet = " ".join(text.split())
    if not snippet:
        return ""
    return _truncate_text(snippet, TEXT_SNIPPET_LIMIT)


def _headers(settings: Settings) -> Dict[str, str]:
    if settings.qdrant_api_key:
        return {"api-key": settings.qdrant_api_key}
    return {}


def _qdrant_healthy(settings: Settings) -> bool:
    url = f"{settings.qdrant_url}/healthz"
    try:
        response = request_json("GET", url, headers=_headers(settings), timeout=2.0)
    except QdrantUnavailable:
        return False
    return response.is_success


def _get_point(settings: Settings, point_id: Any, *, with_payload: bool, with_vector: bool) -> Optional[Dict[str, Any]]:
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/{point_id}"
    params = {
        "with_payload": "true" if with_payload else "false",
        "with_vector": "true" if with_vector else "false",
    }
    response = request_json("GET", url, headers=_headers(settings), params=params)
    if response.status_code == 404:
        return None
    if not response.is_success:
        message = extract_error_message(response)
        raise QdrantError(message, status_code=response.status_code, payload=message)
    payload = response.json()
    return payload.get("result")


def _vector_present(result: Dict[str, Any], vector_name: str) -> bool:
    vector = result.get("vector")
    if vector is None:
        return False
    if isinstance(vector, dict):
        vec = vector.get(vector_name)
        return bool(vec)
    if isinstance(vector, list):
        return len(vector) > 0
    return False


def _recommend(
    settings: Settings,
    point_id: Any,
    *,
    limit: int,
    catalog_version: Optional[int],
    score_threshold: Optional[float],
) -> Dict[str, Any]:
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/recommend"
    payload_fields = [
        "omeka_item_id",
        "omeka_url",
        "thumb_url",
        "catalog_version",
    ]
    body: Dict[str, Any] = {
        "positive": [point_id],
        "using": settings.vector_name,
        "limit": limit,
        "with_payload": payload_fields,
        "with_vector": False,
    }
    must_not = [{"has_id": [point_id]}]
    filters: Dict[str, Any] = {"must_not": must_not}
    if catalog_version is not None:
        filters.setdefault("must", []).append(
            {"key": "catalog_version", "match": {"value": catalog_version}}
        )
    if filters:
        body["filter"] = filters

    include_threshold = score_threshold is not None
    if include_threshold:
        body["score_threshold"] = score_threshold

    response = request_json("POST", url, headers=_headers(settings), json=body)
    if response.status_code == 400 and include_threshold:
        message = extract_error_message(response)
        if "score_threshold" in message or "unknown" in message:
            body.pop("score_threshold", None)
            response = request_json("POST", url, headers=_headers(settings), json=body)

    if not response.is_success:
        message = extract_error_message(response)
        raise QdrantError(message, status_code=response.status_code, payload=message)

    return response.json()


def _search_text(
    settings: Settings,
    query_vector: list[float],
    *,
    limit: int,
    offset: int,
    score_threshold: Optional[float],
    catalog_version: Optional[int],
) -> Dict[str, Any]:
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/search"
    body: Dict[str, Any] = {
        "vector": {"name": settings.text_vector_name, "vector": query_vector},
        "limit": limit,
        "offset": offset,
        "with_payload": list(SEARCH_PAYLOAD_FIELDS),
        "with_vector": False,
    }
    if catalog_version is not None:
        body["filter"] = {"must": [{"key": "catalog_version", "match": {"value": catalog_version}}]}
    include_threshold = score_threshold is not None
    if include_threshold:
        body["score_threshold"] = score_threshold

    response = request_json("POST", url, headers=_headers(settings), json=body)
    if response.status_code == 400 and include_threshold:
        message = extract_error_message(response)
        if "score_threshold" in message or "unknown" in message:
            body.pop("score_threshold", None)
            response = request_json("POST", url, headers=_headers(settings), json=body)

    if not response.is_success:
        message = extract_error_message(response)
        raise QdrantError(message, status_code=response.status_code, payload=message)

    return response.json()


def _normalize_mode(raw: Optional[str], settings: Settings) -> str:
    if raw is None or not raw.strip():
        mode = settings.search_mode
    else:
        mode = raw.strip().lower()
    if mode not in SEARCH_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(SEARCH_MODES)}")
    return mode


def _combine_results(
    *,
    semantic_results: list[SearchResult],
    lexical_results: list[Dict[str, Any]],
    limit: int,
    offset: int,
    semantic_weight: float,
    rrf_k: int,
) -> list[SearchResult]:
    combined: Dict[Any, Dict[str, Any]] = {}
    semantic_weight = max(0.0, min(1.0, semantic_weight))
    lexical_weight = max(0.0, min(1.0, 1.0 - semantic_weight))

    def add_item(item_id: Any, *, omeka_url: Optional[str], thumb_url: Optional[str], snippet: Optional[str]) -> None:
        entry = combined.setdefault(
            item_id,
            {
                "omeka_item_id": item_id,
                "omeka_url": omeka_url,
                "thumb_url": thumb_url,
                "snippet": snippet,
                "score": 0.0,
            },
        )
        if entry.get("omeka_url") is None and omeka_url:
            entry["omeka_url"] = omeka_url
        if entry.get("thumb_url") is None and thumb_url:
            entry["thumb_url"] = thumb_url
        if entry.get("snippet") in (None, "") and snippet:
            entry["snippet"] = snippet

    for rank, item in enumerate(semantic_results):
        item_id = item.omeka_item_id
        score = semantic_weight / (rrf_k + rank + 1)
        add_item(item_id, omeka_url=item.omeka_url, thumb_url=item.thumb_url, snippet=item.snippet)
        combined[item_id]["score"] += score

    for rank, item in enumerate(lexical_results):
        item_id = item["omeka_item_id"]
        score = lexical_weight / (rrf_k + rank + 1)
        add_item(
            item_id,
            omeka_url=item.get("omeka_url"),
            thumb_url=item.get("thumb_url"),
            snippet=item.get("snippet"),
        )
        combined[item_id]["score"] += score

    if not combined:
        return []

    max_score = max(entry["score"] for entry in combined.values()) or 1.0
    ordered = sorted(combined.values(), key=lambda entry: entry["score"], reverse=True)
    sliced = ordered[offset : offset + limit]
    results = []
    for entry in sliced:
        results.append(
            SearchResult(
                omeka_item_id=entry["omeka_item_id"],
                omeka_url=entry.get("omeka_url"),
                thumb_url=entry.get("thumb_url"),
                score=float(entry["score"] / max_score),
                snippet=entry.get("snippet"),
            )
        )
    return results


@dataclass(frozen=True)
class _SemanticCandidate:
    result: SearchResult
    tag_tokens: set[str]
    blob_tokens: set[str]
    score: float


def _apply_hybrid_boost(
    query: str,
    candidates: list[_SemanticCandidate],
    *,
    disable: bool,
) -> list[SearchResult]:
    if disable or not candidates:
        return [candidate.result for candidate in candidates]
    query_tokens = tokenize(query)
    if not query_tokens:
        return [candidate.result for candidate in candidates]

    scored: list[tuple[float, int, SearchResult]] = []
    for idx, candidate in enumerate(candidates):
        boost = 0.0
        if query_tokens & candidate.tag_tokens:
            boost += TAG_MATCH_BOOST
        if query_tokens & candidate.blob_tokens:
            boost += BLOB_MATCH_BOOST
        scored.append((candidate.score + boost, idx, candidate.result))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored]


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    settings = _settings()
    if not settings.similar_enabled:
        return {"status": HEALTH_DISABLED}
    if not _qdrant_healthy(settings):
        return {"status": HEALTH_DEGRADED}
    return {"status": HEALTH_OK}


@app.get(
    "/v1/omeka/items/{omeka_id}/similar",
    response_model=SimilarResponse,
)
def similar_items(
    omeka_id: str,
    limit: Optional[str] = None,
    catalog_version: Optional[str] = None,
    score_threshold: Optional[str] = None,
) -> SimilarResponse:
    settings = _settings()
    if not settings.similar_enabled:
        raise HTTPException(status_code=503, detail="Similar search is disabled")
    limit_value = (
        settings.default_limit
        if limit is None
        else _parse_int_param("limit", limit, min_value=1, max_value=settings.max_limit)
    )
    if limit_value > settings.max_limit:
        raise HTTPException(status_code=400, detail=f"limit must be <= {settings.max_limit}")
    if catalog_version is None:
        catalog_version_value = 2
    else:
        catalog_version_value = _parse_int_param("catalog_version", catalog_version, min_value=1)

    score_value = (
        settings.default_score_threshold
        if score_threshold is None
        else _parse_float_param("score_threshold", score_threshold, min_value=0.0, max_value=1.0)
    )

    if score_value is not None and not (0.0 <= score_value <= 1.0):
        raise HTTPException(status_code=400, detail="score_threshold must be between 0 and 1")

    point_id = _parse_point_id(omeka_id)

    try:
        point = _get_point(settings, point_id, with_payload=True, with_vector=True)
    except QdrantUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QdrantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if point is None:
        raise HTTPException(status_code=404, detail="Source item not found")

    payload = point.get("payload") or {}

    if catalog_version_value is not None:
        payload_version = _normalize_catalog_version(payload.get("catalog_version"))
        if payload_version != catalog_version_value:
            raise HTTPException(status_code=404, detail="Source item not found")

    if not _vector_present(point, settings.vector_name):
        raise HTTPException(status_code=422, detail="Source item missing required vector")

    source_payload = {k: v for k, v in payload.items() if k in ALLOWED_PAYLOAD_FIELDS}
    source = _build_item_payload(source_payload, point_id)
    source_item_id = source["omeka_item_id"]

    try:
        recommendation = _recommend(
            settings,
            point_id,
            limit=limit_value,
            catalog_version=catalog_version_value,
            score_threshold=score_value,
        )
    except QdrantUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QdrantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    results = recommendation.get("result") or []
    matches = []
    for item in results:
        match_payload = item.get("payload") or {}
        match_payload = {k: v for k, v in match_payload.items() if k in ALLOWED_PAYLOAD_FIELDS}
        match = _build_item_payload(match_payload, item.get("id"))
        score = float(item.get("score") or 0.0)
        if score_value is not None and score < score_value:
            continue
        if _ids_equal(match["omeka_item_id"], source_item_id) or _ids_equal(item.get("id"), point_id):
            continue
        matches.append(MatchItem(**match, score=score))

    return SimilarResponse(source=source, matches=matches)


@app.get(
    "/v1/omeka/search",
    response_model=SearchResponse,
)
def search_items(
    q: Optional[str] = None,
    limit: Optional[str] = None,
    offset: Optional[str] = None,
    min_score: Optional[str] = None,
    catalog_version: Optional[str] = None,
    mode: Optional[str] = None,
) -> SearchResponse:
    settings = _settings()
    if not settings.text_search_enabled:
        raise HTTPException(status_code=503, detail="Text search is disabled")

    if q is None or not q.strip():
        raise HTTPException(status_code=400, detail="q is required")
    q_value = q.strip()

    limit_value = (
        TEXT_SEARCH_LIMIT_DEFAULT
        if limit is None
        else _parse_int_param("limit", limit, min_value=1, max_value=TEXT_SEARCH_LIMIT_MAX)
    )
    offset_value = 0 if offset is None else _parse_int_param("offset", offset, min_value=0)
    catalog_version_value = 2 if catalog_version is None else _parse_int_param("catalog_version", catalog_version, min_value=1)
    mode_value = _normalize_mode(mode, settings)

    min_score_value = (
        None
        if min_score is None
        else _parse_float_param("min_score", min_score, min_value=0.0, max_value=1.0)
    )
    if min_score_value is not None and not (0.0 <= min_score_value <= 1.0):
        raise HTTPException(status_code=400, detail="min_score must be between 0 and 1")

    candidate_limit = max(limit_value + offset_value, limit_value)
    candidate_limit = min(TEXT_SEARCH_LIMIT_MAX, candidate_limit)

    semantic_candidates: list[_SemanticCandidate] = []
    lexical_results: list[Dict[str, Any]] = []

    if mode_value in {"semantic", "hybrid"}:
        try:
            query_vector = embeddings.embed_text(q_value)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to embed query") from exc

        try:
            search_response = _search_text(
                settings,
                query_vector,
                limit=candidate_limit,
                offset=0,
                score_threshold=min_score_value,
                catalog_version=catalog_version_value,
            )
        except QdrantUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except QdrantError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        for item in search_response.get("result") or []:
            payload = item.get("payload") or {}
            omeka_item_id = _normalize_id(payload.get("omeka_item_id", item.get("id")))
            text_blob = compose_text_blob(payload)
            tags_text = extract_tags_text(payload)
            tag_tokens = tokenize(tags_text)
            blob_tokens = tokenize(text_blob) - tag_tokens
            score = float(item.get("score") or 0.0)
            result = SearchResult(
                omeka_item_id=omeka_item_id,
                omeka_url=payload.get("omeka_url"),
                thumb_url=payload.get("thumb_url"),
                score=score,
                snippet=_build_snippet(text_blob),
            )
            semantic_candidates.append(
                _SemanticCandidate(
                    result=result,
                    tag_tokens=tag_tokens,
                    blob_tokens=blob_tokens,
                    score=score,
                )
            )

    if mode_value in {"exact", "hybrid"}:
        try:
            lexical_rows = search_index(
                q_value,
                limit=candidate_limit,
                offset=0,
                catalog_version=catalog_version_value,
                db_path=Path(settings.search_db_path),
            )
            lexical_results = [
                {
                    "omeka_item_id": row.omeka_item_id,
                    "omeka_url": row.omeka_url,
                    "thumb_url": row.thumb_url,
                    "score": row.score,
                    "snippet": row.snippet,
                }
                for row in lexical_rows
            ]
        except SearchIndexUnavailable as exc:
            if mode_value == "exact":
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        except SearchIndexError as exc:
            if mode_value == "exact":
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    semantic_results = _apply_hybrid_boost(
        q_value,
        semantic_candidates,
        disable=settings.disable_hybrid_boost,
    )

    if mode_value == "semantic":
        results = semantic_results[offset_value : offset_value + limit_value]
    elif mode_value == "exact":
        results = [
            SearchResult(
                omeka_item_id=item["omeka_item_id"],
                omeka_url=item.get("omeka_url"),
                thumb_url=item.get("thumb_url"),
                score=1.0 if idx == 0 else max(0.0, 1.0 - (idx / max(1, len(lexical_results)))),
                snippet=item.get("snippet"),
            )
            for idx, item in enumerate(lexical_results[offset_value : offset_value + limit_value])
        ]
    else:
        results = _combine_results(
            semantic_results=semantic_results,
            lexical_results=lexical_results,
            limit=limit_value,
            offset=offset_value,
            semantic_weight=settings.search_semantic_weight,
            rrf_k=settings.search_rrf_k,
        )

    return SearchResponse(
        q=q_value,
        limit=limit_value,
        offset=offset_value,
        results=results,
        preproc_version=PREPROC_VERSION,
        embed_model=embeddings.EMBED_MODEL,
    )


@app.get(
    "/v1/omeka/items/{omeka_id}/iconography",
    response_model=IconographyResponse,
)
def item_iconography(omeka_id: str) -> IconographyResponse:
    settings = _settings()
    point_id = _parse_point_id(omeka_id)

    try:
        point = _get_point(settings, point_id, with_payload=True, with_vector=False)
    except QdrantUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QdrantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if point is None:
        raise HTTPException(status_code=404, detail="Item not found")

    payload = point.get("payload") or {}
    rarity_score = payload.get("rarity_score")
    if rarity_score is None:
        raise HTTPException(status_code=404, detail="Iconographic data not available")

    motif_details_raw = payload.get("rarity_motif_details") or []
    motifs = [
        MotifDetail(
            motif=d["motif"],
            corpus_frequency=d["corpus_frequency"],
            corpus_percentage=d["corpus_percentage"],
        )
        for d in motif_details_raw
    ]

    return IconographyResponse(
        omeka_item_id=payload.get("omeka_item_id", point_id),
        score=float(rarity_score),
        class_number=int(payload.get("rarity_class_number", 1)),
        motifs=motifs,
        corpus_size=int(payload.get("rarity_corpus_size", 0)),
    )


@app.get(
    "/v1/omeka/items/iconography/batch",
    response_model=IconographyBatchResponse,
)
def items_iconography_batch(ids: str = "") -> IconographyBatchResponse:
    if not ids.strip():
        raise HTTPException(status_code=400, detail="ids parameter is required")

    try:
        id_list = [_parse_point_id(i.strip()) for i in ids.split(",") if i.strip()]
    except Exception:
        raise HTTPException(status_code=400, detail="ids must be comma-separated integers")

    if len(id_list) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 ids per batch request")

    settings = _settings()
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points"
    body = {
        "ids": id_list,
        "with_payload": ["omeka_item_id", "rarity_class_number"],
        "with_vector": False,
    }

    try:
        response = request_json("POST", url, headers=_headers(settings), json=body)
    except QdrantUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not response.is_success:
        message = extract_error_message(response)
        raise HTTPException(status_code=502, detail=message)

    items = []
    for point in response.json().get("result") or []:
        payload = point.get("payload") or {}
        class_number = payload.get("rarity_class_number")
        if class_number is None:
            continue
        items.append(IconographyBatchItem(
            omeka_item_id=payload.get("omeka_item_id", point.get("id")),
            class_number=int(class_number),
        ))

    return IconographyBatchResponse(items=items)


IMAGE_SEARCH_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_SEARCH_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _search_visual(
    settings: Settings,
    query_vector: list[float],
    *,
    limit: int,
    catalog_version: Optional[int],
) -> Dict[str, Any]:
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/search"
    body: Dict[str, Any] = {
        "vector": {"name": settings.vector_name, "vector": query_vector},
        "limit": limit,
        "with_payload": list(ALLOWED_PAYLOAD_FIELDS),
        "with_vector": False,
    }
    if catalog_version is not None:
        body["filter"] = {"must": [{"key": "catalog_version", "match": {"value": catalog_version}}]}

    response = request_json("POST", url, headers=_headers(settings), json=body)
    if not response.is_success:
        message = extract_error_message(response)
        raise QdrantError(message, status_code=response.status_code, payload=message)
    return response.json()


@app.post(
    "/v1/omeka/images/search",
    response_model=ImageSearchResponse,
)
async def image_search(
    image: UploadFile,
    limit: Optional[str] = None,
    catalog_version: Optional[str] = None,
) -> ImageSearchResponse:
    settings = _settings()
    if not settings.similar_enabled:
        raise HTTPException(status_code=503, detail="Visual search is disabled")

    if image.content_type and image.content_type not in IMAGE_SEARCH_ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Image must be JPEG, PNG, or WebP")

    image_bytes = await image.read()
    if len(image_bytes) > IMAGE_SEARCH_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image must be under 10 MB")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")

    limit_value = (
        settings.default_limit
        if limit is None
        else _parse_int_param("limit", limit, min_value=1, max_value=settings.max_limit)
    )
    catalog_version_value = 2 if catalog_version is None else _parse_int_param("catalog_version", catalog_version, min_value=1)

    try:
        query_vector = embeddings.embed_image(image_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not process image") from exc

    try:
        search_response = _search_visual(
            settings,
            query_vector,
            limit=limit_value,
            catalog_version=catalog_version_value,
        )
    except QdrantUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QdrantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    matches = []
    for item in search_response.get("result") or []:
        payload = item.get("payload") or {}
        payload = {k: v for k, v in payload.items() if k in ALLOWED_PAYLOAD_FIELDS}
        match = _build_item_payload(payload, item.get("id"))
        score = float(item.get("score") or 0.0)
        matches.append(MatchItem(**match, score=score))

    return ImageSearchResponse(matches=matches)


# ── Motif search (DINOv2 patch-level) ────────────────────────────────────────


MOTIF_SEARCH_PAYLOAD_FIELDS = ["omeka_item_id", "omeka_url", "thumb_url", "patch_index"]


def _search_motif_patches(
    settings: Settings,
    query_vector: list[float],
    *,
    raw_limit: int,
) -> Dict[str, Any]:
    url = f"{settings.qdrant_url}/collections/{settings.dino_collection}/points/search"
    body: Dict[str, Any] = {
        "vector": query_vector,
        "limit": raw_limit,
        "with_payload": MOTIF_SEARCH_PAYLOAD_FIELDS,
        "with_vector": False,
    }
    response = request_json("POST", url, headers=_headers(settings), json=body)
    if not response.is_success:
        message = extract_error_message(response)
        raise QdrantError(message, status_code=response.status_code, payload=message)
    return response.json()


def _deduplicate_patch_matches(results: list, limit: int) -> list[MotifMatchItem]:
    """Group patch hits by omeka_item_id, keep best score per item."""
    best: Dict[Any, Dict[str, Any]] = {}
    for item in results:
        payload = item.get("payload") or {}
        omeka_id = payload.get("omeka_item_id")
        if omeka_id is None:
            continue
        score = float(item.get("score") or 0.0)
        if omeka_id not in best or score > best[omeka_id]["score"]:
            best[omeka_id] = {
                "omeka_item_id": omeka_id,
                "omeka_url": payload.get("omeka_url"),
                "thumb_url": payload.get("thumb_url"),
                "score": score,
                "patch_index": payload.get("patch_index", 0),
            }
    sorted_items = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    return [MotifMatchItem(**item) for item in sorted_items[:limit]]


@app.post(
    "/v1/omeka/images/motif-search",
    response_model=MotifSearchResponse,
)
async def motif_search(
    image: UploadFile,
    limit: Optional[str] = None,
) -> MotifSearchResponse:
    """Search for artworks containing a visual motif using DINOv2 patch embeddings."""
    from clip_api import dino

    settings = _settings()
    if not settings.dino_enabled:
        raise HTTPException(status_code=503, detail="Motif search is disabled")

    if image.content_type and image.content_type not in IMAGE_SEARCH_ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Image must be JPEG, PNG, or WebP")

    image_bytes = await image.read()
    if len(image_bytes) > IMAGE_SEARCH_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image must be under 10 MB")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")

    limit_value = (
        settings.default_limit
        if limit is None
        else _parse_int_param("limit", limit, min_value=1, max_value=settings.max_limit)
    )
    # Fetch many more raw patches than needed: each artwork has ~1369 patches
    # (at 518×518), so top-scoring items consume many slots before we see other items.
    raw_limit = min(limit_value * 50, 10000)

    try:
        query_vector = dino.embed_query_crop(image_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not process image") from exc

    try:
        search_response = _search_motif_patches(
            settings,
            query_vector,
            raw_limit=raw_limit,
        )
    except QdrantUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QdrantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    results = search_response.get("result") or []
    matches = _deduplicate_patch_matches(results, limit_value)

    return MotifSearchResponse(matches=matches)


# ── Single-item ingest ──────────────────────────────────────────────────────


@app.post("/v1/ingest/{omeka_id}", response_model=IngestResponse)
async def ingest_single_item(omeka_id: int, req: IngestRequest) -> IngestResponse:
    """Embed a single item and upsert to Qdrant + SQLite FTS index."""
    from clip_api.ingest import ingest_item

    settings = _settings()
    try:
        result = await ingest_item(
            settings=settings,
            omeka_item_id=omeka_id,
            image_url=req.image_url,
            title=req.title,
            description=req.description,
            subjects=req.subjects,
            year=req.year,
            curator_notes=req.curator_notes,
            omeka_url=req.omeka_url,
            thumb_url=req.thumb_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ingest failed: {exc}") from exc

    return IngestResponse(**result)


@app.post("/v1/dino/ingest/{omeka_id}", response_model=IngestResponse)
async def dino_ingest_single_item(omeka_id: int, req: DinoIngestRequest) -> IngestResponse:
    """Embed a single item's DINOv2 patch vectors only (no CLIP, no FTS)."""
    from clip_api.ingest import fetch_image_bytes, ingest_dino_patches
    import time

    settings = _settings()
    if not settings.dino_enabled:
        raise HTTPException(status_code=503, detail="DINOv2 ingest is disabled")

    t_start = time.perf_counter()
    try:
        image_bytes = await fetch_image_bytes(req.image_url)
        await ingest_dino_patches(
            settings,
            omeka_id,
            image_bytes=image_bytes,
            omeka_url=req.omeka_url,
            thumb_url=req.thumb_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DINOv2 ingest failed: {exc}") from exc

    elapsed = time.perf_counter() - t_start
    return IngestResponse(status="ok", omeka_item_id=omeka_id, elapsed_seconds=round(elapsed, 2))


# ── SAM segment search ─────────────────────────────────────────────────────


def _search_segments(settings: Settings, query_vector: list, raw_limit: int) -> dict:
    """Search the SAM segment collection."""
    url = f"{settings.qdrant_url}/collections/{settings.segment_collection}/points/search"
    body = {
        "vector": query_vector,
        "limit": raw_limit,
        "with_payload": True,
    }
    response = request_json("POST", url, headers=_headers(settings), json=body)
    if not response.is_success:
        message = extract_error_message(response)
        raise QdrantError(message, status_code=response.status_code, payload=message)
    return response.json()


def _deduplicate_segment_matches(results: list, limit: int) -> list[SegmentMatchItem]:
    """Group segment hits by omeka_item_id, keep best score per item."""
    best: Dict[Any, Dict[str, Any]] = {}
    for item in results:
        payload = item.get("payload") or {}
        omeka_id = payload.get("omeka_item_id")
        if omeka_id is None:
            continue
        score = float(item.get("score") or 0.0)
        if omeka_id not in best or score > best[omeka_id]["score"]:
            best[omeka_id] = {
                "omeka_item_id": omeka_id,
                "omeka_url": payload.get("omeka_url"),
                "thumb_url": payload.get("thumb_url"),
                "score": score,
                "segment_index": payload.get("segment_index", 0),
                "segment_url": payload.get("segment_url"),
                "bbox": payload.get("bbox"),
            }
    sorted_items = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    return [SegmentMatchItem(**item) for item in sorted_items[:limit]]


@app.post(
    "/v1/omeka/images/segment-search",
    response_model=SegmentSearchResponse,
)
async def segment_search(
    image: UploadFile,
    limit: Optional[str] = None,
) -> SegmentSearchResponse:
    """Search for artworks by matching SAM segment-level DINOv2 CLS embeddings."""
    from clip_api import dino

    settings = _settings()
    if not settings.segment_enabled:
        raise HTTPException(status_code=503, detail="Segment search is disabled")

    if image.content_type and image.content_type not in IMAGE_SEARCH_ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Image must be JPEG, PNG, or WebP")

    image_bytes = await image.read()
    if len(image_bytes) > IMAGE_SEARCH_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image must be under 10 MB")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")

    limit_value = (
        settings.default_limit
        if limit is None
        else _parse_int_param("limit", limit, min_value=1, max_value=settings.max_limit)
    )
    # Segments are ~20-40 per artwork, so raw_limit multiplier is much lower than patches
    raw_limit = min(limit_value * 10, 2000)

    try:
        query_vector = dino.embed_segment_query(image_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not process image") from exc

    try:
        search_response = _search_segments(settings, query_vector, raw_limit=raw_limit)
    except QdrantUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except QdrantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    results = search_response.get("result") or []
    matches = _deduplicate_segment_matches(results, limit_value)

    return SegmentSearchResponse(matches=matches)


@app.post("/v1/segment/ingest/{omeka_id}", response_model=IngestResponse)
async def segment_ingest_single_item(omeka_id: int, req: SegmentIngestRequest) -> IngestResponse:
    """Segment a single item with SAM and embed segments with DINOv2 CLS."""
    from clip_api.ingest import fetch_image_bytes, ingest_segments
    import time

    settings = _settings()
    if not settings.segment_ingest_enabled:
        raise HTTPException(
            status_code=503,
            detail="Segment ingest is disabled on this instance; run segmentation locally",
        )

    t_start = time.perf_counter()
    try:
        image_bytes = await fetch_image_bytes(req.image_url)
        await ingest_segments(
            settings,
            omeka_id,
            image_bytes=image_bytes,
            omeka_url=req.omeka_url,
            thumb_url=req.thumb_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Segment ingest failed: {exc}") from exc

    elapsed = time.perf_counter() - t_start
    return IngestResponse(status="ok", omeka_item_id=omeka_id, elapsed_seconds=round(elapsed, 2))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("clip_api.main:app", host="0.0.0.0", port=8000)

from __future__ import annotations

import os

import requests
from fastapi.testclient import TestClient

from clip_api.main import app


def test_missing_source_returns_404() -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/items/999/similar")
        assert resp.status_code == 404


def test_catalog_version_mismatch_returns_404() -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/items/1/similar", params={"catalog_version": "1"})
        assert resp.status_code == 404


def test_similar_returns_matches_and_excludes_source() -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/items/1/similar", params={"limit": "5"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"]["omeka_item_id"] == 1
        match_ids = [m["omeka_item_id"] for m in data["matches"]]
        assert 1 not in match_ids
        assert 2 in match_ids


def test_catalog_version_filtering() -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/items/1/similar", params={"catalog_version": "2"})
        assert resp.status_code == 200
        data = resp.json()
        assert all(m["catalog_version"] == 2 for m in data["matches"])


def test_score_threshold_filters_matches() -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/items/1/similar", params={"score_threshold": "1.0"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["matches"] == []


def test_similar_disabled_returns_503(monkeypatch) -> None:
    monkeypatch.setenv("SIMILAR_ENABLED", "false")
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/items/1/similar")
        assert resp.status_code == 503


def test_missing_vector_returns_422() -> None:
    base_url = os.environ["QDRANT_URL"]
    collection = os.environ.get("QDRANT_COLLECTION", "omeka_items")
    delete_resp = requests.post(
        f"{base_url}/collections/{collection}/points/vectors/delete",
        json={"points": [1], "vectors": ["visual_vec"]},
        timeout=10,
    )
    delete_resp.raise_for_status()

    with TestClient(app) as client:
        resp = client.get("/v1/omeka/items/1/similar", params={"catalog_version": "2"})
        assert resp.status_code == 422

from __future__ import annotations

from fastapi.testclient import TestClient

from clip_api.main import app
from clip_api.qdrant import QdrantUnavailable


def test_healthz_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_healthz_disabled(monkeypatch) -> None:
    monkeypatch.setenv("SIMILAR_ENABLED", "false")
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"


def test_healthz_degraded_on_qdrant_unavailable(monkeypatch) -> None:
    def _raise_unavailable(*_args, **_kwargs):
        raise QdrantUnavailable("down")

    monkeypatch.setenv("SIMILAR_ENABLED", "true")
    monkeypatch.setattr("clip_api.main.request_json", _raise_unavailable)
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"

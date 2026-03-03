from __future__ import annotations

from fastapi.testclient import TestClient

from clip_api import embeddings
from clip_api.main import app


def test_search_limit_hard_cap(monkeypatch) -> None:
    monkeypatch.setattr("clip_api.embeddings.embed_text", lambda _text: [1.0, 0.0, 0.0])
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/search", params={"q": "classic rock", "limit": "999"})
        assert resp.status_code == 400


def test_search_smoke(monkeypatch) -> None:
    monkeypatch.setattr("clip_api.embeddings.embed_text", lambda _text: [1.0, 0.0, 0.0])
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/search", params={"q": "classic rock"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["q"] == "classic rock"
        assert data["limit"] == 10
        assert data["offset"] == 0
        assert data["preproc_version"] == 1
        assert data["embed_model"] == embeddings.EMBED_MODEL
        assert isinstance(data["results"], list)
        if data["results"]:
            result = data["results"][0]
            for key in ("omeka_item_id", "title", "omeka_url", "thumb_url", "score", "snippet"):
                assert key in result


def test_search_exact_mode_ignores_embeddings(monkeypatch) -> None:
    def _boom(_text: str) -> None:
        raise RuntimeError("embeddings should not be called in exact mode")

    monkeypatch.setattr("clip_api.embeddings.embed_text", _boom)
    with TestClient(app) as client:
        resp = client.get("/v1/omeka/search", params={"q": "classic rock", "mode": "exact"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"]


def test_hybrid_boost_reorders_results(monkeypatch) -> None:
    def _fake_search_text(*_args, **_kwargs):
        return {
            "result": [
                {
                    "id": 1,
                    "score": 0.9,
                    "payload": {
                        "omeka_item_id": 1,
                        "title": "One",
                        "omeka_url": "https://example.com/items/1",
                        "thumb_url": "https://example.com/thumbs/1.jpg",
                        "omeka_description": "classic rock poster",
                        "subjects": ["poster"],
                        "ocr_text": "",
                    },
                },
                {
                    "id": 2,
                    "score": 0.85,
                    "payload": {
                        "omeka_item_id": 2,
                        "title": "Two",
                        "omeka_url": "https://example.com/items/2",
                        "thumb_url": "https://example.com/thumbs/2.jpg",
                        "omeka_description": "psychedelic handwriting",
                        "subjects": ["classic rock"],
                        "ocr_text": "",
                    },
                },
            ]
        }

    monkeypatch.delenv("DISABLE_HYBRID_BOOST", raising=False)
    monkeypatch.setattr("clip_api.embeddings.embed_text", lambda _text: [1.0, 0.0, 0.0])
    monkeypatch.setattr("clip_api.main._search_text", _fake_search_text)

    with TestClient(app) as client:
        resp = client.get("/v1/omeka/search", params={"q": "Two", "mode": "semantic"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"]
        assert data["results"][0]["omeka_item_id"] == 2

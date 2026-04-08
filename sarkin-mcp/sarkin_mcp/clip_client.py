from __future__ import annotations

import httpx

from .config import Config


class ClipClient:
    """Thin synchronous wrapper around the clip-api HTTP endpoints."""

    def __init__(self, cfg: Config):
        self.base_url = cfg.clip_api_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    def healthz(self) -> bool:
        try:
            resp = self._client.get("/healthz")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def search(self, query: str, mode: str = "hybrid", limit: int = 20, offset: int = 0) -> dict:
        resp = self._client.get(
            "/v1/omeka/search",
            params={"q": query, "mode": mode, "limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        return resp.json()

    def find_similar(self, item_id: int, limit: int = 20) -> dict:
        resp = self._client.get(
            f"/v1/omeka/items/{item_id}/similar",
            params={"limit": limit},
        )
        resp.raise_for_status()
        return resp.json()

    def search_by_image(self, image_bytes: bytes, limit: int = 20) -> dict:
        resp = self._client.post(
            "/v1/omeka/images/search",
            files={"file": ("query.jpg", image_bytes, "image/jpeg")},
            params={"limit": limit},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()

    def iconography(self, item_id: int) -> dict:
        resp = self._client.get(f"/v1/omeka/items/{item_id}/iconography")
        resp.raise_for_status()
        return resp.json()

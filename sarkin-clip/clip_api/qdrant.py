from __future__ import annotations

from typing import Any, Dict, Optional
import threading

import httpx


class QdrantUnavailable(RuntimeError):
    pass


class QdrantError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


_CLIENT: Optional[httpx.Client] = None
_CLIENT_LOCK = threading.Lock()


def get_client() -> httpx.Client:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT.is_closed:
            _CLIENT = httpx.Client()
        return _CLIENT


def close_client() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None and not _CLIENT.is_closed:
            _CLIENT.close()


def request_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> httpx.Response:
    try:
        response = get_client().request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        raise QdrantUnavailable(str(exc)) from exc
    return response


def extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        for key in ("error", "message", "status"):
            if key in payload:
                return str(payload[key])
    return str(payload)

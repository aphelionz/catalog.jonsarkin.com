from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
import sys

import pytest
import requests

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from clip_api.search_index import upsert_document


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def _wait_for_qdrant(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/healthz", timeout=2)
            if resp.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("Qdrant did not become healthy in time")


@pytest.fixture(scope="session")
def qdrant_container() -> str:
    base_url = os.getenv("QDRANT_URL", "http://hyphae:6333")
    try:
        _wait_for_qdrant(base_url, timeout=5.0)
        yield base_url
        return
    except RuntimeError:
        pass

    if shutil.which("docker") is None:
        pytest.skip("docker is required for integration tests")

    port = _find_free_port()
    container_name = f"qdrant-test-{uuid.uuid4().hex[:8]}"
    base_url = f"http://localhost:{port}"

    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "-p",
                f"{port}:6333",
                "--name",
                container_name,
                "qdrant/qdrant:latest",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        pytest.skip("docker daemon unavailable for integration tests")

    try:
        _wait_for_qdrant(base_url)
        yield base_url
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


@pytest.fixture(autouse=True)
def qdrant_fixture(qdrant_container: str) -> None:
    collection = os.getenv("QDRANT_TEST_COLLECTION", "omeka_items_test")
    base_url = qdrant_container

    requests.delete(f"{base_url}/collections/{collection}", timeout=5)

    create_body = {
        "vectors": {
            "visual_vec": {
                "size": 3,
                "distance": "Cosine",
            },
            "text_vec_clip": {
                "size": 3,
                "distance": "Cosine",
            },
        },
    }
    resp = requests.put(
        f"{base_url}/collections/{collection}", json=create_body, timeout=10
    )
    resp.raise_for_status()

    points = [
        {
            "id": 1,
            "vector": {
                "visual_vec": [1.0, 0.0, 0.0],
                "text_vec_clip": [1.0, 0.0, 0.0],
            },
            "payload": {
                "omeka_item_id": 1,
                "title": "One",
                "omeka_url": "https://example.com/items/1",
                "thumb_url": "https://example.com/thumbs/1.jpg",
                "catalog_version": 2,
                "text_blob": "classic rock poster with handwritten notes",
                "ocr_text": "classic rock",
            },
        },
        {
            "id": 2,
            "vector": {
                "visual_vec": [0.9, 0.1, 0.0],
                "text_vec_clip": [0.9, 0.1, 0.0],
            },
            "payload": {
                "omeka_item_id": 2,
                "title": "Two",
                "omeka_url": "https://example.com/items/2",
                "thumb_url": "https://example.com/thumbs/2.jpg",
                "catalog_version": 2,
                "text_blob": "psychedelic handwriting and doodles",
                "ocr_text": "",
            },
        },
        {
            "id": 3,
            "vector": {
                "visual_vec": [0.0, 1.0, 0.0],
                "text_vec_clip": [0.0, 1.0, 0.0],
            },
            "payload": {
                "omeka_item_id": 3,
                "title": "Three",
                "omeka_url": "https://example.com/items/3",
                "thumb_url": "https://example.com/thumbs/3.jpg",
                "catalog_version": 1,
                "text_blob": "abstract scribbles",
                "ocr_text": "notes",
            },
        },
    ]
    upsert = requests.put(
        f"{base_url}/collections/{collection}/points",
        params={"wait": "true"},
        json={"points": points},
        timeout=10,
    )
    upsert.raise_for_status()

    # Ensure env defaults for API
    os.environ["QDRANT_COLLECTION"] = collection
    os.environ["VECTOR_NAME"] = "visual_vec"
    os.environ["QDRANT_URL"] = base_url

    yield


@pytest.fixture(autouse=True)
def search_index_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "search_index.sqlite"
    monkeypatch.setenv("SEARCH_DB_PATH", str(db_path))

    documents = [
        {
            "omeka_item_id": 1,
            "catalog_version": 2,
            "title": "One",
            "omeka_url": "https://example.com/items/1",
            "thumb_url": "https://example.com/thumbs/1.jpg",
            "omeka_description": "classic rock poster with handwritten notes",
            "subjects": "classic rock",
            "ocr_text_raw": "classic rock",
            "ocr_text_norm": "classic rock",
            "text_blob": "classic rock poster with handwritten notes",
        },
        {
            "omeka_item_id": 2,
            "catalog_version": 2,
            "title": "Two",
            "omeka_url": "https://example.com/items/2",
            "thumb_url": "https://example.com/thumbs/2.jpg",
            "omeka_description": "psychedelic handwriting and doodles",
            "subjects": "psychedelic",
            "ocr_text_raw": "",
            "ocr_text_norm": "",
            "text_blob": "psychedelic handwriting and doodles",
        },
        {
            "omeka_item_id": 3,
            "catalog_version": 1,
            "title": "Three",
            "omeka_url": "https://example.com/items/3",
            "thumb_url": "https://example.com/thumbs/3.jpg",
            "omeka_description": "abstract scribbles",
            "subjects": "",
            "ocr_text_raw": "notes",
            "ocr_text_norm": "notes",
            "text_blob": "abstract scribbles",
        },
    ]

    for doc in documents:
        upsert_document(doc, db_path=db_path)

    yield

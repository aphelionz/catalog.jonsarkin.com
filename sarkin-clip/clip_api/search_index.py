from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_DB_LOCK = threading.Lock()

TABLE_NAME = "omeka_fts"
TOKENIZE = "unicode61 remove_diacritics 2"

_COLUMNS = [
    ("omeka_item_id", "UNINDEXED"),
    ("catalog_version", "UNINDEXED"),
    ("title", ""),
    ("omeka_url", "UNINDEXED"),
    ("thumb_url", "UNINDEXED"),
    ("description", ""),
    ("subjects", ""),
    ("mediums", ""),
    ("years", ""),
    ("curator_notes", ""),
    ("ocr_text_raw", ""),
    ("ocr_text_norm", ""),
    ("text_blob", ""),
]

TEXT_BLOB_COL_INDEX = [name for name, _ in _COLUMNS].index("text_blob")


class SearchIndexError(RuntimeError):
    pass


class SearchIndexUnavailable(SearchIndexError):
    pass


@dataclass(frozen=True)
class SearchRow:
    omeka_item_id: int
    title: Optional[str]
    omeka_url: Optional[str]
    thumb_url: Optional[str]
    score: float
    snippet: Optional[str]


def _db_path() -> Path:
    return Path(os.getenv("SEARCH_DB_PATH", ".search_index.sqlite"))


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    columns_sql = ", ".join(
        f"{name} {modifier}".rstrip() for name, modifier in _COLUMNS
    )
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE_NAME} USING fts5({columns_sql}, tokenize='{TOKENIZE}');"
    )


def _ensure_parent(path: Path) -> None:
    if path.parent == Path("."):
        return
    path.parent.mkdir(parents=True, exist_ok=True)


def upsert_document(payload: Dict[str, Optional[str]], *, db_path: Optional[Path] = None) -> None:
    path = db_path or _db_path()
    _ensure_parent(path)
    omeka_item_id = payload.get("omeka_item_id")
    if omeka_item_id is None:
        raise SearchIndexError("omeka_item_id is required for search index upsert")

    try:
        catalog_version = int(payload.get("catalog_version")) if payload.get("catalog_version") is not None else 0
    except (TypeError, ValueError):
        catalog_version = 0

    row_values = {
        "omeka_item_id": int(omeka_item_id),
        "catalog_version": catalog_version,
        "title": payload.get("title") or "",
        "omeka_url": payload.get("omeka_url") or "",
        "thumb_url": payload.get("thumb_url") or "",
        "description": payload.get("omeka_description") or payload.get("description") or "",
        "subjects": payload.get("subjects") or "",
        "mediums": payload.get("mediums") or "",
        "years": payload.get("years") or "",
        "curator_notes": payload.get("curator_notes") or "",
        "ocr_text_raw": payload.get("ocr_text_raw") or payload.get("ocr_text") or "",
        "ocr_text_norm": payload.get("ocr_text_norm") or "",
        "text_blob": payload.get("text_blob") or "",
    }

    column_names = [name for name, _ in _COLUMNS]
    placeholders = ", ".join("?" for _ in column_names)
    values = [row_values[name] for name in column_names]

    with _DB_LOCK:
        with _connect(path) as conn:
            _init_db(conn)
            conn.execute(f"DELETE FROM {TABLE_NAME} WHERE rowid = ?", (row_values["omeka_item_id"],))
            conn.execute(
                f"INSERT INTO {TABLE_NAME} (rowid, {', '.join(column_names)}) VALUES (?, {placeholders})",
                [row_values["omeka_item_id"], *values],
            )
            conn.commit()


def _build_query(query: str) -> str:
    tokens = [token for token in query.strip().split() if token]
    if not tokens:
        return ""
    escaped = [token.replace('"', '""') for token in tokens]
    return " AND ".join(f'"{token}"' for token in escaped)


def search(
    query: str,
    *,
    limit: int,
    offset: int,
    catalog_version: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[SearchRow]:
    path = db_path or _db_path()
    if not path.exists():
        raise SearchIndexUnavailable(f"search index not found at {path}")

    fts_query = _build_query(query)
    if not fts_query:
        return []

    where_clauses = [f"{TABLE_NAME} MATCH ?"]
    params: List[object] = [fts_query]
    if catalog_version is not None:
        where_clauses.append("catalog_version = ?")
        params.append(catalog_version)
    params.extend([limit, offset])

    sql = (
        f"SELECT omeka_item_id, title, omeka_url, thumb_url, "
        f"bm25({TABLE_NAME}) as score, "
        f"snippet({TABLE_NAME}, {TEXT_BLOB_COL_INDEX}, '', '', '...', 12) as snippet "
        f"FROM {TABLE_NAME} WHERE {' AND '.join(where_clauses)} "
        f"ORDER BY score LIMIT ? OFFSET ?"
    )

    with _DB_LOCK:
        with _connect(path) as conn:
            _init_db(conn)
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                raise SearchIndexError(str(exc)) from exc

    results: List[SearchRow] = []
    for row in rows:
        results.append(
            SearchRow(
                omeka_item_id=int(row["omeka_item_id"]),
                title=row["title"] or None,
                omeka_url=row["omeka_url"] or None,
                thumb_url=row["thumb_url"] or None,
                score=float(row["score"] or 0.0),
                snippet=row["snippet"] or None,
            )
        )
    return results

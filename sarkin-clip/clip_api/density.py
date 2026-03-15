"""Image density classification for SAM parameter tuning.

Classifies artworks into three density tiers (sparse/medium/dense) based on
edge density, white pixel percentage, and color variance. Each tier maps to
a tuned SAM parameter preset.

The image_density table lives in the same SQLite DB as the FTS index.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _assign_tier(edge_density: float, boundaries: tuple[float, float] | None) -> str:
    """Assign tier from edge_density using percentile boundaries or hardcoded fallback."""
    if boundaries is not None:
        low, high = boundaries
        if edge_density < low:
            return "sparse"
        elif edge_density > high:
            return "dense"
        return "medium"
    # Legacy hardcoded fallback (used before first reclassify run)
    if edge_density < 0.08:
        return "sparse"
    elif edge_density > 0.15:
        return "dense"
    return "medium"


def classify_density(image_input, boundaries: tuple[float, float] | None = None) -> dict:
    """Classify an image's visual density.

    Args:
        image_input: file path (str/Path), or raw bytes.
        boundaries: optional (low, high) edge_density thresholds from percentile config.

    Returns:
        dict with keys: tier, edge_density, white_pct, color_std
    """
    if isinstance(image_input, (str, Path)):
        img = cv2.imread(str(image_input))
    else:
        arr = np.frombuffer(image_input, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Could not decode image")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    total_pixels = h * w

    # 1. Edge density — Canny edge pixels as % of total
    edges = cv2.Canny(gray, 50, 150)
    edge_density = np.count_nonzero(edges) / total_pixels

    # 2. White/light pixel percentage — proxy for background/whitespace
    white_pct = np.count_nonzero(gray > 220) / total_pixels

    # 3. Color variance — std dev across all channels
    color_std = float(np.std(img))

    tier = _assign_tier(edge_density, boundaries)

    return {
        "tier": tier,
        "edge_density": round(edge_density, 4),
        "white_pct": round(white_pct, 4),
        "color_std": round(color_std, 2),
    }


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS image_density (
    omeka_id INTEGER PRIMARY KEY,
    tier TEXT NOT NULL CHECK(tier IN ('sparse', 'medium', 'dense')),
    edge_density REAL NOT NULL,
    white_pct REAL NOT NULL,
    color_std REAL NOT NULL,
    override INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""

_CREATE_CONFIG_TABLE = """\
CREATE TABLE IF NOT EXISTS density_config (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""

_MIGRATE_OVERRIDE = "ALTER TABLE image_density ADD COLUMN override INTEGER NOT NULL DEFAULT 0"


def _db_path() -> Path:
    """Same DB as the FTS search index."""
    return Path(os.getenv("SEARCH_DB_PATH", ".search_index.sqlite"))


def _migrate(conn: sqlite3.Connection) -> None:
    """Add override column if missing (idempotent)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(image_density)").fetchall()}
    if "override" not in cols:
        conn.execute(_MIGRATE_OVERRIDE)
        conn.commit()
        logger.info("Migrated image_density: added override column")


def init_density_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_CONFIG_TABLE)
    conn.commit()
    _migrate(conn)


def open_density_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialize) the density DB."""
    path = db_path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    init_density_table(conn)
    return conn


def upsert_density(conn: sqlite3.Connection, omeka_id: int, result: dict, override: bool = False) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO image_density (omeka_id, tier, edge_density, white_pct, color_std, override) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (omeka_id, result["tier"], result["edge_density"], result["white_pct"], result["color_std"], int(override)),
    )
    conn.commit()


def set_override(conn: sqlite3.Connection, omeka_id: int, tier: str) -> bool:
    """Manually override an item's density tier. Returns True if updated."""
    row = conn.execute("SELECT omeka_id FROM image_density WHERE omeka_id = ?", (omeka_id,)).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE image_density SET tier = ?, override = 1, computed_at = datetime('now') WHERE omeka_id = ?",
        (tier, omeka_id),
    )
    conn.commit()
    return True


def get_tier(conn: sqlite3.Connection, omeka_id: int) -> Optional[str]:
    row = conn.execute("SELECT tier FROM image_density WHERE omeka_id = ?", (omeka_id,)).fetchone()
    return row["tier"] if row else None


def get_all_tiers(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {omeka_id: tier} for all classified items."""
    rows = conn.execute("SELECT omeka_id, tier FROM image_density").fetchall()
    return {row["omeka_id"]: row["tier"] for row in rows}


def get_override_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of omeka_ids with manual overrides."""
    rows = conn.execute("SELECT omeka_id FROM image_density WHERE override = 1").fetchall()
    return {row["omeka_id"] for row in rows}


def get_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Return tier counts."""
    rows = conn.execute("SELECT tier, COUNT(*) as cnt FROM image_density GROUP BY tier").fetchall()
    return {row["tier"]: row["cnt"] for row in rows}


def get_density_page(
    conn: sqlite3.Connection,
    tier: Optional[str] = None,
    sort: str = "edge_density",
    order: str = "desc",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int]:
    """Paginated density query. Returns (rows, total_count)."""
    valid_sorts = {"edge_density", "white_pct", "color_std", "omeka_id"}
    if sort not in valid_sorts:
        sort = "edge_density"
    if order not in ("asc", "desc"):
        order = "desc"

    where = ""
    params: list = []
    if tier in ("sparse", "medium", "dense"):
        where = "WHERE tier = ?"
        params.append(tier)

    # Count
    count_sql = f"SELECT COUNT(*) FROM image_density {where}"
    total = conn.execute(count_sql, params).fetchone()[0]

    # Page
    offset = (page - 1) * per_page
    sql = (
        f"SELECT omeka_id, tier, edge_density, white_pct, color_std, override "
        f"FROM image_density {where} "
        f"ORDER BY {sort} {order} "
        f"LIMIT ? OFFSET ?"
    )
    rows = conn.execute(sql, params + [per_page, offset]).fetchall()
    return [dict(r) for r in rows], total


# ---------------------------------------------------------------------------
# Percentile-based reclassification
# ---------------------------------------------------------------------------


def get_boundaries(conn: sqlite3.Connection) -> tuple[float, float] | None:
    """Read stored percentile boundaries. Returns (low, high) or None."""
    rows = conn.execute(
        "SELECT key, value FROM density_config WHERE key IN ('p_low_val', 'p_high_val')"
    ).fetchall()
    vals = {row["key"]: row["value"] for row in rows}
    if "p_low_val" in vals and "p_high_val" in vals:
        return (vals["p_low_val"], vals["p_high_val"])
    return None


def save_boundaries(
    conn: sqlite3.Connection,
    p_low_pct: int,
    p_high_pct: int,
    p_low_val: float,
    p_high_val: float,
) -> None:
    """Persist percentile boundaries to density_config."""
    for key, value in [
        ("p_low_pct", float(p_low_pct)),
        ("p_high_pct", float(p_high_pct)),
        ("p_low_val", p_low_val),
        ("p_high_val", p_high_val),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO density_config (key, value, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (key, value),
        )
    conn.commit()


def _percentile(sorted_vals: list[float], pct: int) -> float:
    """Simple linear-interpolation percentile on a pre-sorted list."""
    n = len(sorted_vals)
    k = (pct / 100) * (n - 1)
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_vals[-1]
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def reclassify_all(conn: sqlite3.Connection, p_low: int = 15, p_high: int = 65) -> dict:
    """Reclassify all non-override items using edge_density percentiles.

    Returns the new tier distribution dict.
    """
    rows = conn.execute(
        "SELECT edge_density FROM image_density WHERE override = 0 ORDER BY edge_density"
    ).fetchall()
    if not rows:
        logger.warning("No items to reclassify")
        return get_stats(conn)

    vals = [row["edge_density"] for row in rows]
    low_val = _percentile(vals, p_low)
    high_val = _percentile(vals, p_high)

    save_boundaries(conn, p_low, p_high, low_val, high_val)

    conn.execute(
        "UPDATE image_density SET tier = CASE "
        "WHEN edge_density < ? THEN 'sparse' "
        "WHEN edge_density > ? THEN 'dense' "
        "ELSE 'medium' END, "
        "computed_at = datetime('now') "
        "WHERE override = 0",
        (low_val, high_val),
    )
    conn.commit()

    logger.info("Reclassified: P%d=%.4f  P%d=%.4f", p_low, low_val, p_high, high_val)
    return get_stats(conn)

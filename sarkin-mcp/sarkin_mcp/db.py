from __future__ import annotations

from typing import Any

import pymysql
import pymysql.cursors

from .config import Config

# Property ID → field name mapping (from docs/omeka-invariants.md)
PROPERTY_MAP: dict[int, str] = {
    1: "title",
    3: "motifs",       # repeatable
    4: "description",
    7: "date",
    8: "work_type",
    9: "source",
    10: "catalog_number",
    15: "contributor",
    26: "medium",
    40: "location",
    51: "provenance",
    72: "owner",
    91: "transcription",
    476: "signature",
    603: "height",
    931: "support",
    1129: "width",
    1343: "credit",
    1424: "box",
    962: "mentions",     # repeatable
    1579: "condition",
    1710: "curation_note",
}

REPEATABLE_FIELDS = {"motifs", "mentions"}

PROPERTY_IDS = tuple(PROPERTY_MAP.keys())
PROPERTY_IDS_STR = ",".join(str(p) for p in PROPERTY_IDS)

# Resource template ID for Artwork (Jon Sarkin)
ARTWORK_TEMPLATE_ID = 2


def get_connection(cfg: Config) -> pymysql.Connection:
    return pymysql.connect(
        host=cfg.db_host,
        port=cfg.db_port,
        database=cfg.db_name,
        user=cfg.db_user,
        password=cfg.db_password,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
        read_timeout=30,
    )


def resolve_catalog_number(conn: pymysql.Connection, catalog_number: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT resource_id FROM value WHERE property_id = 10 AND value = %s LIMIT 1",
            (catalog_number,),
        )
        row = cur.fetchone()
        return row["resource_id"] if row else None


def fetch_item_metadata(conn: pymysql.Connection, item_ids: list[int], cfg: Config) -> dict[int, dict]:
    """Batch-fetch metadata for a list of item IDs. Returns {item_id: {field: value}}."""
    if not item_ids:
        return {}

    result: dict[int, dict] = {}
    for chunk in _chunks(item_ids, 500):
        placeholders = ",".join(["%s"] * len(chunk))
        sql = f"""
            SELECT v.resource_id, v.property_id, v.value
            FROM value v
            WHERE v.resource_id IN ({placeholders})
              AND v.property_id IN ({PROPERTY_IDS_STR})
            ORDER BY v.resource_id, v.property_id
        """
        with conn.cursor() as cur:
            cur.execute(sql, chunk)
            for row in cur.fetchall():
                rid = row["resource_id"]
                pid = row["property_id"]
                field = PROPERTY_MAP.get(pid)
                if not field:
                    continue

                if rid not in result:
                    result[rid] = {"id": rid}

                val = row["value"]
                if field in REPEATABLE_FIELDS:
                    result[rid].setdefault(field, [])
                    if val:
                        result[rid][field].append(val)
                else:
                    result[rid][field] = val

    # Fetch media for thumbnail URLs
    all_ids = list(result.keys())
    for chunk in _chunks(all_ids, 500):
        placeholders = ",".join(["%s"] * len(chunk))
        sql = f"""
            SELECT item_id, storage_id, extension, has_thumbnails, position
            FROM media
            WHERE item_id IN ({placeholders})
            ORDER BY item_id, position
        """
        with conn.cursor() as cur:
            cur.execute(sql, chunk)
            for row in cur.fetchall():
                rid = row["item_id"]
                if rid in result and "thumbnail_url" not in result[rid]:
                    if row["has_thumbnails"] and row["storage_id"]:
                        result[rid]["thumbnail_url"] = cfg.thumbnail_url(
                            row["storage_id"], row["extension"]
                        )
                        result[rid]["original_url"] = cfg.original_url(
                            row["storage_id"], row["extension"]
                        )
                    result[rid].setdefault("media_count", 0)
                    result[rid]["media_count"] = result[rid].get("media_count", 0) + 1

    # Fetch collection membership
    for chunk in _chunks(all_ids, 500):
        placeholders = ",".join(["%s"] * len(chunk))
        sql = f"""
            SELECT iis.item_id, r.title
            FROM item_item_set iis
            JOIN resource r ON r.id = iis.item_set_id
            WHERE iis.item_id IN ({placeholders})
        """
        with conn.cursor() as cur:
            cur.execute(sql, chunk)
            for row in cur.fetchall():
                rid = row["item_id"]
                if rid in result:
                    result[rid].setdefault("collections", [])
                    result[rid]["collections"].append(row["title"])

    # Add URLs and set defaults
    for rid, item in result.items():
        item["url"] = cfg.item_url(rid)
        item.setdefault("motifs", [])
        item.setdefault("collections", [])
        item.setdefault("media_count", 0)
        # Parse dimensions as floats
        for dim_field in ("width", "height"):
            if dim_field in item and item[dim_field] is not None:
                try:
                    item[dim_field] = float(item[dim_field])
                except (ValueError, TypeError):
                    pass

    return result


def get_item(conn: pymysql.Connection, cfg: Config, *, item_id: int | None = None, catalog_number: str | None = None) -> dict | None:
    """Get full metadata for a single item."""
    if catalog_number and not item_id:
        item_id = resolve_catalog_number(conn, catalog_number)
        if not item_id:
            return None

    if not item_id:
        return None

    items = fetch_item_metadata(conn, [item_id], cfg)
    return items.get(item_id)


def search_catalog(conn: pymysql.Connection, cfg: Config, **filters: Any) -> dict:
    """Search catalog by structured metadata filters. Returns {total_count, items}."""
    conditions: list[str] = []
    params: list[Any] = []

    base = """
        FROM resource r
        JOIN item i ON i.id = r.id
        WHERE r.is_public = 1
    """

    # Date range
    if filters.get("date_from") is not None or filters.get("date_to") is not None:
        date_sub = "r.id IN (SELECT resource_id FROM value WHERE property_id = 7"
        date_params: list[Any] = []
        if filters.get("date_from") is not None and filters.get("date_to") is not None:
            date_sub += " AND CAST(REGEXP_REPLACE(value, '^c\\\\.?\\\\s*', '') AS UNSIGNED) BETWEEN %s AND %s"
            date_params.extend([filters["date_from"], filters["date_to"]])
        elif filters.get("date_from") is not None:
            date_sub += " AND CAST(REGEXP_REPLACE(value, '^c\\\\.?\\\\s*', '') AS UNSIGNED) >= %s"
            date_params.append(filters["date_from"])
        else:
            date_sub += " AND CAST(REGEXP_REPLACE(value, '^c\\\\.?\\\\s*', '') AS UNSIGNED) <= %s"
            date_params.append(filters["date_to"])
        date_sub += ")"
        conditions.append(date_sub)
        params.extend(date_params)

    # Motifs (AND logic)
    if filters.get("motifs"):
        motifs = filters["motifs"]
        placeholders = ",".join(["%s"] * len(motifs))
        conditions.append(f"""r.id IN (
            SELECT resource_id FROM value
            WHERE property_id = 3 AND value IN ({placeholders})
            GROUP BY resource_id
            HAVING COUNT(DISTINCT value) = %s
        )""")
        params.extend(motifs)
        params.append(len(motifs))

    # Work type (exact)
    if filters.get("work_type"):
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 8 AND value = %s)")
        params.append(filters["work_type"])

    # Medium (substring)
    if filters.get("medium"):
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 26 AND value LIKE CONCAT('%%', %s, '%%'))")
        params.append(filters["medium"])

    # Support (exact)
    if filters.get("support"):
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 931 AND value = %s)")
        params.append(filters["support"])

    # Collection (substring)
    if filters.get("collection"):
        conditions.append("""r.id IN (
            SELECT iis.item_id FROM item_item_set iis
            JOIN resource rs ON rs.id = iis.item_set_id
            WHERE rs.title LIKE CONCAT('%%', %s, '%%')
        )""")
        params.append(filters["collection"])

    # Owner (substring)
    if filters.get("owner"):
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 72 AND value LIKE CONCAT('%%', %s, '%%'))")
        params.append(filters["owner"])

    # Condition (exact)
    if filters.get("condition"):
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 1579 AND value = %s)")
        params.append(filters["condition"])

    # Dimensions
    if filters.get("min_width") is not None:
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 1129 AND CAST(value AS DECIMAL(10,2)) >= %s)")
        params.append(filters["min_width"])
    if filters.get("max_width") is not None:
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 1129 AND CAST(value AS DECIMAL(10,2)) <= %s)")
        params.append(filters["max_width"])
    if filters.get("min_height") is not None:
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 603 AND CAST(value AS DECIMAL(10,2)) >= %s)")
        params.append(filters["min_height"])
    if filters.get("max_height") is not None:
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 603 AND CAST(value AS DECIMAL(10,2)) <= %s)")
        params.append(filters["max_height"])

    # Has transcription
    if filters.get("has_transcription"):
        conditions.append("r.id IN (SELECT resource_id FROM value WHERE property_id = 91 AND value IS NOT NULL AND TRIM(value) != '')")

    where_clause = base
    if conditions:
        where_clause += " AND " + " AND ".join(conditions)

    # Count
    count_sql = f"SELECT COUNT(DISTINCT r.id) AS cnt {where_clause}"
    with conn.cursor() as cur:
        cur.execute(count_sql, params)
        total_count = cur.fetchone()["cnt"]

    # Fetch IDs
    limit = min(max(int(filters.get("limit", 50)), 1), 200)
    offset = max(int(filters.get("offset", 0)), 0)

    ids_sql = f"SELECT DISTINCT r.id {where_clause} ORDER BY r.id LIMIT %s OFFSET %s"
    id_params = params + [limit, offset]
    with conn.cursor() as cur:
        cur.execute(ids_sql, id_params)
        item_ids = [row["id"] for row in cur.fetchall()]

    items = fetch_item_metadata(conn, item_ids, cfg)
    # Preserve order
    ordered = [items[iid] for iid in item_ids if iid in items]

    return {"total_count": total_count, "limit": limit, "offset": offset, "items": ordered}


def corpus_statistics(conn: pymysql.Connection, breakdown: str = "summary") -> dict:
    """Get aggregate corpus statistics."""
    base_where = "r.is_public = 1"

    result: dict[str, Any] = {}

    if breakdown == "summary":
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM resource r JOIN item i ON i.id = r.id WHERE {base_where}")
            result["total_items"] = cur.fetchone()["total"]

            cur.execute(f"""
                SELECT
                    MIN(CAST(REGEXP_REPLACE(v.value, '^c\\\\.?\\\\s*', '') AS UNSIGNED)) AS earliest,
                    MAX(CAST(REGEXP_REPLACE(v.value, '^c\\\\.?\\\\s*', '') AS UNSIGNED)) AS latest
                FROM value v
                JOIN resource r ON r.id = v.resource_id
                JOIN item i ON i.id = r.id
                WHERE v.property_id = 7 AND {base_where}
            """)
            dr = cur.fetchone()
            result["date_range"] = {"earliest": dr["earliest"], "latest": dr["latest"]}

            cur.execute(f"""
                SELECT COUNT(DISTINCT v.resource_id) AS cnt
                FROM value v
                JOIN resource r ON r.id = v.resource_id
                JOIN item i ON i.id = r.id
                WHERE v.property_id = 91 AND v.value IS NOT NULL AND TRIM(v.value) != ''
                  AND {base_where}
            """)
            result["with_transcription"] = cur.fetchone()["cnt"]

        result["breakdown_type"] = "summary"
        return result

    # Breakdown queries
    breakdown_configs = {
        "by_year": (7, "year", "CAST(REGEXP_REPLACE(v.value, '^c\\\\.?\\\\s*', '') AS UNSIGNED)"),
        "by_type": (8, "work_type", "v.value"),
        "by_motif": (3, "motif", "v.value"),
        "by_support": (931, "support", "v.value"),
        "by_medium": (26, "medium", "v.value"),
        "by_condition": (1579, "condition_val", "v.value"),
    }

    if breakdown == "by_collection":
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.title AS collection_name, COUNT(iis.item_id) AS count
                FROM item_item_set iis
                JOIN resource r ON r.id = iis.item_set_id
                GROUP BY iis.item_set_id
                ORDER BY count DESC
            """)
            rows = cur.fetchall()
        result["breakdown_type"] = "by_collection"
        result["breakdown"] = {row["collection_name"]: row["count"] for row in rows}
        return result

    if breakdown not in breakdown_configs:
        return {"error": f"Unknown breakdown type: {breakdown}"}

    prop_id, alias, expr = breakdown_configs[breakdown]
    sql = f"""
        SELECT {expr} AS {alias}, COUNT(DISTINCT v.resource_id) AS count
        FROM value v
        JOIN resource r ON r.id = v.resource_id
        JOIN item i ON i.id = r.id
        WHERE v.property_id = %s AND {base_where}
        GROUP BY {alias}
        ORDER BY count DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (prop_id,))
        rows = cur.fetchall()

    result["breakdown_type"] = breakdown
    result["breakdown"] = {row[alias]: row["count"] for row in rows if row[alias] is not None}
    return result


def fulltext_search(conn: pymysql.Connection, cfg: Config, query: str, limit: int = 20, offset: int = 0) -> dict:
    """Full-text search across transcriptions (bibo:content, property 91) using SQL LIKE.
    No external API dependency — queries MariaDB directly."""
    # Count matches
    count_sql = """
        SELECT COUNT(DISTINCT v.resource_id) AS cnt
        FROM value v
        JOIN resource r ON r.id = v.resource_id
        JOIN item i ON i.id = r.id
        WHERE v.property_id = 91
          AND v.value LIKE CONCAT('%%', %s, '%%')
          AND r.is_public = 1
    """
    with conn.cursor() as cur:
        cur.execute(count_sql, (query,))
        total_count = cur.fetchone()["cnt"]

    # Fetch matching item IDs
    ids_sql = """
        SELECT DISTINCT v.resource_id
        FROM value v
        JOIN resource r ON r.id = v.resource_id
        JOIN item i ON i.id = r.id
        WHERE v.property_id = 91
          AND v.value LIKE CONCAT('%%', %s, '%%')
          AND r.is_public = 1
        ORDER BY v.resource_id
        LIMIT %s OFFSET %s
    """
    with conn.cursor() as cur:
        cur.execute(ids_sql, (query, limit, offset))
        item_ids = [row["resource_id"] for row in cur.fetchall()]

    items = fetch_item_metadata(conn, item_ids, cfg)
    ordered = [items[iid] for iid in item_ids if iid in items]

    return {"total_count": total_count, "limit": limit, "offset": offset, "items": ordered}


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]

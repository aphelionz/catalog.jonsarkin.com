# MCP Server Plan

Technical plan for a Model Context Protocol server exposing the Jon Sarkin catalog raisonné data layer as tools for Claude Desktop, Claude Code, and future MCP-capable clients.

## 1. Architecture Decision

**Recommendation: standalone Python service** (`sarkin-mcp/`), a new top-level directory alongside `sarkin-clip/`.

### Why not extend clip-api?

clip-api is a search/embedding service. It loads PyTorch, CLIP, DINOv2, and SAM models at startup (~1.5 GB RAM on prod). The MCP server needs `mcp`, `pymysql`, and `httpx` — under 50 MB. Bundling them wastes memory when you only need one capability.

More importantly, clip-api has no MariaDB access and shouldn't. Its job is vector search. The MCP server needs both vector search (via clip-api) and structured metadata (via MariaDB). Adding DB access to clip-api would change its scope and complicate its test surface.

### Why not a sidecar/proxy?

A proxy adds indirection without adding value. The MCP server needs to join and reshape data from two backends (clip-api results enriched with MariaDB metadata). That's application logic, not proxying.

### Standalone service tradeoffs

| Pro | Con |
|-----|-----|
| Tiny image (~100 MB vs clip-api's 1.55 GB) | One more container in the stack |
| Independent deploy cycle | Extra network hop for search (sub-ms, intra-Docker) |
| Supports both stdio and SSE transport | Duplicates some Pydantic models from clip-api |
| Clean dependency boundary | |

The SimilarPieces PHP module already calls clip-api over HTTP from the Omeka container. The MCP server follows the same proven pattern.

### Directory structure

```
sarkin-mcp/
  sarkin_mcp/
    __init__.py
    server.py          # MCP entry point (stdio + SSE)
    db.py              # MariaDB connection + query helpers
    clip_client.py     # httpx client wrapping clip-api
    tools.py           # Tool definitions and handlers
    config.py          # Settings from env vars
  requirements.txt
  Dockerfile
```

---

## 2. Tool Definitions

### 2.1 `get_item`

> Get full metadata for a specific catalog item by Omeka ID or catalog number. Returns all known properties: date, type, medium, motifs, dimensions, support, condition, owner, provenance, transcription, signature, and collection membership.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `item_id` | `int` | One required | Omeka item ID |
| `catalog_number` | `string` | One required | e.g. `JS-2016-00042` |

**Data source:** MariaDB only.

**Queries:**

```sql
-- Resolve catalog number to item ID
SELECT resource_id FROM value
WHERE property_id = 10 AND value = :catalog_number
LIMIT 1;

-- Fetch all properties
SELECT v.property_id, v.value, v.type, v.value_resource_id
FROM value v
WHERE v.resource_id = :item_id
  AND v.property_id IN (1,3,4,7,8,9,10,15,26,40,51,72,91,476,603,931,1129,1343,1579,1710);

-- Media
SELECT storage_id, extension, has_thumbnails, position
FROM media WHERE item_id = :item_id ORDER BY position;

-- Collection membership
SELECT r.title
FROM item_item_set iis
JOIN resource r ON r.id = iis.item_set_id
WHERE iis.item_id = :item_id;
```

**Return schema:**

```json
{
  "id": 456,
  "catalog_number": "JS-2016-00042",
  "title": null,
  "date": "2016",
  "work_type": "Drawing",
  "medium": "Marker on paper",
  "motifs": ["Eyes", "Text Fragments", "Faces"],
  "support": "Paper",
  "dimensions": { "width": 11.0, "height": 8.5 },
  "signature": "↘",
  "condition": "Good",
  "owner": "The Jon Sarkin Estate",
  "provenance": null,
  "location": "Gloucester, MA",
  "transcription": "the cactus of the mind grows...",
  "description": "Dense marker drawing with overlapping faces...",
  "credit": null,
  "curation_note": null,
  "collections": ["Estate Collection"],
  "media_count": 1,
  "thumbnail_url": "https://catalog.jonsarkin.com/files/large/abc123.jpg",
  "url": "https://catalog.jonsarkin.com/s/catalog/item/456"
}
```

**Example queries:**
- "Tell me everything about JS-2016-00042"
- "What are the details of item 789?"
- "Look up the catalog entry for JS-2005-00123"

---

### 2.2 `search_catalog`

> Search the catalog by structured metadata filters. Supports filtering by date range, motifs, work type, medium, support, collection, owner, dimensions, and condition. Returns matching items with key metadata. Use this for questions like "all drawings from 2016–2020 with the Cactus motif" or "paintings on canvas larger than 24 inches."

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `date_from` | `int?` | — | Start year inclusive |
| `date_to` | `int?` | — | End year inclusive |
| `motifs` | `string[]?` | — | Filter by dcterms:subject (AND logic) |
| `work_type` | `string?` | — | Exact match: Drawing, Painting, Collage, Mixed Media, Sculpture, Print, Other |
| `medium` | `string?` | — | Substring match on dcterms:medium |
| `support` | `string?` | — | Exact match: Paper, Cardboard, Canvas, Board, Wood, Found Object, etc. |
| `collection` | `string?` | — | Item set name (substring) |
| `owner` | `string?` | — | Substring match on bibo:owner |
| `condition` | `string?` | — | Exact: Excellent, Good, Fair, Poor, Not Examined |
| `min_width` | `float?` | — | Minimum width in inches |
| `min_height` | `float?` | — | Minimum height in inches |
| `max_width` | `float?` | — | Maximum width in inches |
| `max_height` | `float?` | — | Maximum height in inches |
| `has_transcription` | `bool?` | — | Only items with bibo:content |
| `limit` | `int` | 50 | Max results (1–200) |
| `offset` | `int` | 0 | Pagination offset |

**Data source:** MariaDB only.

**Query strategy:**

Build the query dynamically based on which filters are active. Each filter adds a JOIN or subquery.

```sql
-- Base: all Artwork items (template 2)
SELECT DISTINCT r.id
FROM resource r
JOIN item i ON i.id = r.id
WHERE r.resource_template_id = 2
  AND r.is_public = 1

-- Date range (property 7): handle "YYYY" and "c. YYYY" formats
  AND r.id IN (
    SELECT resource_id FROM value
    WHERE property_id = 7
      AND CAST(REGEXP_REPLACE(value, '^c\\.?\\s*', '') AS UNSIGNED)
          BETWEEN :date_from AND :date_to
  )

-- Motifs (property 3): AND logic via HAVING COUNT
  AND r.id IN (
    SELECT resource_id FROM value
    WHERE property_id = 3 AND value IN (:motifs)
    GROUP BY resource_id
    HAVING COUNT(DISTINCT value) = :motif_count
  )

-- Work type (property 8): exact match
  AND r.id IN (
    SELECT resource_id FROM value
    WHERE property_id = 8 AND value = :work_type
  )

-- Medium (property 26): substring
  AND r.id IN (
    SELECT resource_id FROM value
    WHERE property_id = 26 AND value LIKE CONCAT('%', :medium, '%')
  )

-- Support (property 931): exact match
  AND r.id IN (
    SELECT resource_id FROM value
    WHERE property_id = 931 AND value = :support
  )

-- Dimensions (properties 1129, 603): cast to float
  AND r.id IN (
    SELECT resource_id FROM value
    WHERE property_id = 1129
      AND CAST(value AS DECIMAL(10,2)) BETWEEN :min_width AND :max_width
  )

-- Collection: join through item_item_set
  AND r.id IN (
    SELECT iis.item_id FROM item_item_set iis
    JOIN resource rs ON rs.id = iis.item_set_id
    WHERE rs.title LIKE CONCAT('%', :collection, '%')
  )

-- Has transcription (property 91)
  AND r.id IN (
    SELECT resource_id FROM value
    WHERE property_id = 91 AND value IS NOT NULL AND TRIM(value) != ''
  )

ORDER BY r.id
LIMIT :limit OFFSET :offset;
```

Then batch-fetch metadata for matching IDs using the shared `fetch_item_metadata()` helper (see §3).

A separate count query (same WHERE, `SELECT COUNT(DISTINCT r.id)`) provides `total_count` for pagination.

**Return schema:**

```json
{
  "total_count": 142,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "id": 456,
      "catalog_number": "JS-2016-00042",
      "date": "2016",
      "work_type": "Drawing",
      "medium": "Marker on paper",
      "motifs": ["Eyes", "Text Fragments"],
      "support": "Paper",
      "dimensions": { "width": 11.0, "height": 8.5 },
      "owner": "The Jon Sarkin Estate",
      "collection": "Estate Collection",
      "url": "https://catalog.jonsarkin.com/s/catalog/item/456"
    }
  ]
}
```

**Example queries:**
- "Show me all works with the Cactus motif from 2016–2020"
- "How many drawings on cardboard are in the estate collection?"
- "Find paintings wider than 30 inches"
- "List all sculptures in Fair or Poor condition"

---

### 2.3 `search_transcriptions`

> Full-text search across OCR transcriptions and descriptions of Jon Sarkin artworks. His work often contains dense visible text — words, phrases, names, fragments. This tool finds works containing specific words or phrases. Supports three modes: "hybrid" (semantic + lexical, best for most queries), "exact" (precise word matching), and "semantic" (meaning-based, good for conceptual queries).

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `query` | `string` | required | Search text |
| `mode` | `string` | `"hybrid"` | `"hybrid"`, `"semantic"`, or `"exact"` |
| `limit` | `int` | 20 | Max results (1–50) |
| `offset` | `int` | 0 | Pagination offset |

**Data source:** clip-api (`GET /v1/omeka/search`) + MariaDB for metadata enrichment.

**Implementation:** Proxy to clip-api's search endpoint. clip-api runs hybrid search across Qdrant text vectors and SQLite FTS, returning item IDs with scores and snippets. The MCP server then enriches each result with structured metadata from MariaDB (date, type, motifs, catalog number) via `fetch_item_metadata()`.

**Return schema:**

```json
{
  "query": "Dylan",
  "mode": "hybrid",
  "total_results": 8,
  "items": [
    {
      "id": 789,
      "catalog_number": "JS-2018-00099",
      "date": "2018",
      "work_type": "Drawing",
      "motifs": ["Text Fragments", "Names/Words"],
      "score": 0.87,
      "snippet": "...Bob Dylan said the answer is blowin...",
      "url": "https://catalog.jonsarkin.com/s/catalog/item/789"
    }
  ]
}
```

**Example queries:**
- "Find works with the word 'Dylan' written on them"
- "Search for pieces mentioning 'brain surgery'"
- "Which works have 'cactus' in the transcription?"

---

### 2.4 `find_similar`

> Find visually similar artworks to a given catalog item. Uses CLIP embeddings to match overall visual similarity — composition, color palette, density, style. Accepts either an Omeka item ID or a catalog number (JS-YYYY-NNNNN).

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `item_id` | `int?` | — | Omeka item ID (provide one) |
| `catalog_number` | `string?` | — | e.g. `JS-2005-00123` (provide one) |
| `limit` | `int` | 20 | Max results (1–100) |

**Data source:** MariaDB (catalog number resolution) + clip-api (`GET /v1/omeka/items/{id}/similar`) + MariaDB (metadata enrichment).

**Return schema:**

```json
{
  "source": {
    "id": 456,
    "catalog_number": "JS-2016-00042",
    "date": "2016",
    "work_type": "Drawing"
  },
  "similar_items": [
    {
      "id": 789,
      "catalog_number": "JS-2018-00099",
      "date": "2018",
      "work_type": "Drawing",
      "motifs": ["Eyes", "Faces"],
      "score": 0.92,
      "url": "https://catalog.jonsarkin.com/s/catalog/item/789"
    }
  ]
}
```

**Example queries:**
- "What looks like JS-2005-00123?"
- "Find works visually similar to item 456"
- "Show me pieces with a similar composition to JS-2021-T8818"

---

### 2.5 `search_by_image`

> Find catalog items visually similar to an uploaded image. Uses CLIP embeddings for global visual similarity. Useful for identifying unknown works, finding stylistic matches, or exploring the corpus from an external reference image.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `image_base64` | `string?` | — | Base64-encoded JPEG or PNG |
| `image_url` | `string?` | — | URL to fetch image from |
| `limit` | `int` | 20 | Max results (1–100) |

**Data source:** clip-api (`POST /v1/omeka/images/search`) + MariaDB (metadata enrichment).

**Implementation:** The MCP server decodes the base64 image (or fetches the URL) and POSTs it as a multipart file upload to clip-api. Enriches results with MariaDB metadata.

**Return schema:** Same structure as `find_similar.similar_items`.

**Example queries:**
- "Find works that look like this photo" (with image attachment)
- "What catalog items match this crop of a face motif?"

---

### 2.6 `iconographic_profile`

> Get the iconographic (motif) rarity profile for an artwork. Shows which of Sarkin's 12 recurring motifs appear, how common each is across the corpus, and an overall rarity score (class 1–5, where 5 is rarest). Useful for art-historical analysis of Sarkin's visual vocabulary and identifying works with unusual motif combinations.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `item_id` | `int?` | — | Omeka item ID |
| `catalog_number` | `string?` | — | e.g. `JS-2016-00042` |

**Data source:** MariaDB (catalog number resolution) + clip-api (`GET /v1/omeka/items/{id}/iconography`).

The clip-api endpoint reads precomputed rarity data stored in the Qdrant payload (`rarity_score`, `rarity_class_number`, `rarity_motif_details`). These are computed during ingest by `sarkin-clip/clip_api/rarity.py` using IDF with Laplace smoothing.

**Return schema:**

```json
{
  "id": 456,
  "catalog_number": "JS-2016-00042",
  "rarity_class": 3,
  "rarity_score": 62.4,
  "motifs": [
    {
      "motif": "Eyes",
      "corpus_count": 1847,
      "corpus_percentage": 42.1
    },
    {
      "motif": "Grids",
      "corpus_count": 312,
      "corpus_percentage": 7.1
    }
  ],
  "corpus_size": 4388
}
```

**Example queries:**
- "How rare is the motif combination on JS-2018-00099?"
- "What's the iconographic profile of item 234?"

---

### 2.7 `corpus_statistics`

> Get aggregate statistics about the Jon Sarkin catalog. Total item counts, breakdowns by work type, motif frequency distributions, date range coverage, collection sizes, and other corpus-level data. Use for research-level questions about the overall body of work.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `breakdown` | `string` | `"summary"` | One of: `summary`, `by_year`, `by_type`, `by_motif`, `by_support`, `by_medium`, `by_collection`, `by_condition` |

**Data source:** MariaDB only.

**Queries by breakdown:**

```sql
-- summary: total count
SELECT COUNT(*) AS total FROM resource r
JOIN item i ON i.id = r.id
WHERE r.resource_template_id = 2 AND r.is_public = 1;

-- summary: date range
SELECT
  MIN(CAST(REGEXP_REPLACE(v.value, '^c\\.?\\s*', '') AS UNSIGNED)) AS earliest,
  MAX(CAST(REGEXP_REPLACE(v.value, '^c\\.?\\s*', '') AS UNSIGNED)) AS latest
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 7
  AND r.resource_template_id = 2 AND r.is_public = 1;

-- summary: items with transcriptions
SELECT COUNT(DISTINCT v.resource_id) AS with_transcription
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 91 AND v.value IS NOT NULL AND TRIM(v.value) != ''
  AND r.resource_template_id = 2 AND r.is_public = 1;

-- by_year (property 7)
SELECT CAST(REGEXP_REPLACE(v.value, '^c\\.?\\s*', '') AS UNSIGNED) AS year,
       COUNT(*) AS count
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 7
  AND r.resource_template_id = 2 AND r.is_public = 1
GROUP BY year ORDER BY year;

-- by_type (property 8)
SELECT v.value AS work_type, COUNT(DISTINCT v.resource_id) AS count
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 8
  AND r.resource_template_id = 2 AND r.is_public = 1
GROUP BY v.value ORDER BY count DESC;

-- by_motif (property 3)
SELECT v.value AS motif, COUNT(DISTINCT v.resource_id) AS count
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 3
  AND r.resource_template_id = 2 AND r.is_public = 1
GROUP BY v.value ORDER BY count DESC;

-- by_support (property 931)
SELECT v.value AS support, COUNT(DISTINCT v.resource_id) AS count
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 931
  AND r.resource_template_id = 2 AND r.is_public = 1
GROUP BY v.value ORDER BY count DESC;

-- by_medium (property 26)
SELECT v.value AS medium, COUNT(DISTINCT v.resource_id) AS count
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 26
  AND r.resource_template_id = 2 AND r.is_public = 1
GROUP BY v.value ORDER BY count DESC;

-- by_collection
SELECT r.title AS collection, COUNT(iis.item_id) AS count
FROM item_item_set iis
JOIN resource r ON r.id = iis.item_set_id
GROUP BY iis.item_set_id ORDER BY count DESC;

-- by_condition (property 1579)
SELECT v.value AS condition_val, COUNT(DISTINCT v.resource_id) AS count
FROM value v
JOIN resource r ON r.id = v.resource_id
JOIN item i ON i.id = r.id
WHERE v.property_id = 1579
  AND r.resource_template_id = 2 AND r.is_public = 1
GROUP BY v.value ORDER BY count DESC;
```

**Return schema:**

```json
{
  "total_items": 4388,
  "date_range": { "earliest": 1989, "latest": 2024 },
  "with_transcription": 2104,
  "breakdown_type": "by_motif",
  "breakdown": {
    "Eyes": 1847,
    "Faces": 1623,
    "Text Fragments": 1401,
    "Names/Words": 987,
    "Hands": 756,
    "Patterns": 612,
    "Grids": 312,
    "Fish": 289,
    "Circles": 267,
    "Animals": 198,
    "Maps": 134,
    "Numbers": 89
  }
}
```

**Example queries:**
- "How many works are in the catalog?"
- "What's the distribution of work types across decades?"
- "Which motifs are most common?"
- "How many items are in each collection?"
- "Show me the year-by-year output"

---

## 3. Data Access Layer

### MariaDB access

**Connection:** `pymysql` with a connection pool. The MCP server handles one request at a time in stdio mode, so async is unnecessary. Config from env vars:

```python
DB_HOST     = os.getenv("MYSQL_HOST", "db")
DB_PORT     = int(os.getenv("MYSQL_PORT", "3306"))
DB_NAME     = os.getenv("MYSQL_DATABASE", "omeka")
DB_USER     = os.getenv("MYSQL_USER", "omeka")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "omeka")
```

**Shared helper: `fetch_item_metadata(item_ids: list[int]) -> dict[int, dict]`**

The enrichment pattern — clip-api returns item IDs + scores, MCP server adds metadata — recurs in 4 of 7 tools. A single batch helper handles it:

```sql
SELECT v.resource_id, v.property_id, v.value
FROM value v
WHERE v.resource_id IN (:ids)
  AND v.property_id IN (1,3,4,7,8,10,26,40,51,72,91,476,603,931,1129,1343,1579)
ORDER BY v.resource_id, v.property_id;
```

This follows the same pattern as RapidEditor's bulk value fetch (chunks of 500 IDs). The helper groups values by resource_id and property_id, mapping them to a structured dict.

**Property ID → field mapping** (hardcoded, from `docs/omeka-invariants.md`):

| Property ID | Field | Repeatable |
|-------------|-------|------------|
| 1 | title | no |
| 3 | motifs | yes |
| 4 | description | no |
| 7 | date | no |
| 8 | work_type | no |
| 10 | catalog_number | no |
| 26 | medium | no |
| 40 | location | no |
| 51 | provenance | no |
| 72 | owner | no |
| 91 | transcription | no |
| 476 | signature | no |
| 603 | height | no |
| 931 | support | no |
| 1129 | width | no |
| 1343 | credit | no |
| 1579 | condition | no |

**Date parsing:** Dates are stored as `"2005"` or `"c. 2005"`. Strip the `c.` prefix and parse as int. In SQL, use `REGEXP_REPLACE(value, '^c\\.?\\s*', '')` then `CAST AS UNSIGNED`.

**URL construction:**
- Item URLs: `{CATALOG_BASE_URL}/s/catalog/item/{id}` (site slug is `catalog`)
- Thumbnails: `{CATALOG_BASE_URL}/files/large/{storage_id}.{extension}`

### clip-api access

**Base URL:** `http://clip-api:8000` (Docker hostname). Configurable via `CLIP_API_URL` env var.

**Client:** `httpx.Client` with reasonable timeouts (30s for search, 60s for image upload).

**Endpoints used:**

| MCP Tool | clip-api Endpoint | Method |
|----------|-------------------|--------|
| `search_transcriptions` | `/v1/omeka/search` | GET |
| `find_similar` | `/v1/omeka/items/{id}/similar` | GET |
| `search_by_image` | `/v1/omeka/images/search` | POST (multipart) |
| `iconographic_profile` | `/v1/omeka/items/{id}/iconography` | GET |

No new clip-api routes are needed. The existing endpoints cover all vector/search operations.

### Qdrant access

**Never access Qdrant directly.** Always go through clip-api. This avoids duplicating embedding logic and collection knowledge, and keeps vector operations in one place.

### Authentication

This is an internal tool, not public-facing. No auth for initial implementation. The MCP server is only reachable from:
- stdio: the local machine running Claude Desktop
- SSE: the Docker network (no Traefik labels, no external port in prod)

If SSE is later exposed externally (e.g., via SSH tunnel), add a simple bearer token via `MCP_AUTH_TOKEN` env var.

---

## 4. MCP Protocol Implementation

### SDK

Use the Python `mcp` package (`pip install mcp`). It provides:
- `Server` class with `@server.tool()` decorator for tool registration
- stdio and SSE transport built-in
- Pydantic-based input validation
- Tool response formatting (text content blocks)

### Transport

Support both:

1. **stdio** — for Claude Desktop. The client spawns the MCP server as a subprocess and communicates over stdin/stdout. No Docker needed; run `python -m sarkin_mcp.server` directly on the host.

2. **SSE** (Server-Sent Events) — for Docker deployment and remote access. The MCP server runs as an HTTP service. Claude Code or remote clients connect to `http://mcp:9000/sse`.

The server auto-detects based on `MCP_TRANSPORT` env var (default: `stdio`).

### Response formatting

Return structured JSON that an LLM can reason over, not raw database rows. Each tool response includes:

- **Human-readable summary** as the first text content block (e.g., "Found 142 drawings matching your filters")
- **Structured JSON** as the second content block with the full result data

This lets the LLM both narrate results to the user and perform further analysis on the structured data.

```python
@server.tool()
async def search_catalog(params: SearchParams) -> list[TextContent]:
    results = _execute_search(params)
    summary = f"Found {results['total_count']} items matching your filters."
    return [
        TextContent(type="text", text=summary),
        TextContent(type="text", text=json.dumps(results, indent=2))
    ]
```

### Error handling

- MariaDB connection failures: return a clear error message, not a traceback
- clip-api unavailable: health-check first (`GET /healthz`), return degraded results (metadata-only) if search is down
- Invalid catalog numbers: return "Item not found" with the attempted lookup value

---

## 5. Deployment

### Docker Compose (dev)

Add to `docker-compose.yml`:

```yaml
mcp:
  build:
    context: ./sarkin-mcp
    dockerfile: Dockerfile
  ports:
    - "9000:9000"
  depends_on:
    db:
      condition: service_healthy
    clip-api:
      condition: service_started
  environment:
    MYSQL_HOST: db
    MYSQL_DATABASE: omeka
    MYSQL_USER: omeka
    MYSQL_PASSWORD: omeka
    CLIP_API_URL: http://clip-api:8000
    CATALOG_BASE_URL: http://localhost:8888
    MCP_TRANSPORT: sse
    MCP_PORT: 9000
  restart: unless-stopped
```

### Docker Compose (prod)

Add to `docker-compose.prod.yml`:

```yaml
mcp:
  image: aphelionz/sarkin-mcp
  restart: unless-stopped
  depends_on:
    db:
      condition: service_healthy
    clip-api:
      condition: service_started
  environment:
    MYSQL_HOST: db
    MYSQL_DATABASE: ${MYSQL_DATABASE}
    MYSQL_USER: ${MYSQL_USER}
    MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    CLIP_API_URL: http://clip-api:8000
    CATALOG_BASE_URL: https://catalog.jonsarkin.com
    MCP_TRANSPORT: sse
    MCP_PORT: 9000
```

No Traefik labels — the MCP server is not public-facing.

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sarkin_mcp/ ./sarkin_mcp/
CMD ["python", "-m", "sarkin_mcp.server"]
```

Estimated image size: ~100 MB.

### Environment variables

| Var | Default | Description |
|-----|---------|-------------|
| `MYSQL_HOST` | `db` | MariaDB hostname |
| `MYSQL_PORT` | `3306` | MariaDB port |
| `MYSQL_DATABASE` | `omeka` | Database name |
| `MYSQL_USER` | `omeka` | DB user |
| `MYSQL_PASSWORD` | `omeka` | DB password |
| `CLIP_API_URL` | `http://clip-api:8000` | clip-api base URL |
| `CATALOG_BASE_URL` | `https://catalog.jonsarkin.com` | Public site base URL |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `sse` |
| `MCP_PORT` | `9000` | SSE listen port |

### Makefile targets

```makefile
mcp-stdio:  ## Run MCP server locally in stdio mode (for Claude Desktop)
	cd sarkin-mcp && python -m sarkin_mcp.server

mcp-logs:  ## Tail MCP server Docker logs
	docker compose logs -f mcp
```

### Claude Desktop configuration

For local development with stdio transport (Docker stack must be running for MariaDB and clip-api):

```json
{
  "mcpServers": {
    "sarkin-catalog": {
      "command": "python",
      "args": ["-m", "sarkin_mcp.server"],
      "cwd": "/Users/mark/Projects/catalog.jonsarkin.com/sarkin-mcp",
      "env": {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "3306",
        "MYSQL_DATABASE": "omeka",
        "MYSQL_USER": "omeka",
        "MYSQL_PASSWORD": "omeka",
        "CLIP_API_URL": "http://localhost:8000",
        "CATALOG_BASE_URL": "http://localhost:8888"
      }
    }
  }
}
```

**Note:** The dev `docker-compose.yml` does not expose MariaDB port 3306 to the host. For stdio mode, either:
1. Add `ports: ["3306:3306"]` to the `db` service (simplest)
2. Use SSE mode with the MCP server running inside Docker

Option 1 is recommended for dev since it keeps Claude Desktop's stdio spawning simple.

### Health check

```python
@server.tool()
async def healthz():
    """Check MCP server health and connectivity to backends."""
    db_ok = _check_db()
    clip_ok = _check_clip_api()
    return {
        "status": "ok" if db_ok and clip_ok else "degraded",
        "mariadb": "connected" if db_ok else "unreachable",
        "clip_api": "connected" if clip_ok else "unreachable"
    }
```

---

## 6. Open Questions

### Image transport for `search_by_image`

MCP tool inputs are JSON. Base64-encoded images can be 5–10 MB as text. The MCP protocol supports resource URIs and image content blocks — need to verify how Claude Desktop and Claude Code actually pass image data to MCP tools. If they use `ImageContent` blocks, the tool should accept those natively. Fallback: accept base64 string with a 10 MB limit.

### Caching for `corpus_statistics`

Aggregate queries scan the full `value` table. With ~4,400 items × ~17 properties = ~75,000 value rows, these queries are fast (<100ms). But if the corpus grows significantly, add an in-memory cache with 5-minute TTL. Not needed for v1.

### Motif/segment search tools (stretch)

clip-api has `POST /v1/omeka/images/motif-search` (DINOv2 patch-level) and `POST /v1/omeka/images/segment-search` (SAM segments). These are powerful for motif discovery — upload a crop of an eye, find all works containing similar eye motifs. Worth adding as `search_by_motif_crop` and `search_by_segment` in a v2, once the base tools are validated.

### Cross-media reference lookup (future)

Colin Rhodes scenario: "Show me all works where the transcription contains names that also appear in the Jim Stories." This requires a structured index of cultural references and names across media types. That index doesn't exist yet. The MCP server could host it once built — the tool would be a MariaDB query against a `cross_references` table. Flag as future work.

### Lexical search (future)

Word frequency analysis and rare word discovery across transcriptions. The SimilarPieces module already builds a lexical corpus (word frequency map cached as JSON). The MCP server could expose this as a `lexical_search` tool. Depends on whether the cached corpus format is sufficient or if a proper lexical index table is needed.

### Density/visual complexity (stretch)

clip-api has density classification endpoints (`GET /v1/density`). Could expose as `browse_by_density` tool for queries like "show me the most visually dense works from 2018." Low priority — density is more of a browse feature than a query feature.

### `resource_template_id` filter

All queries assume template 2 ("Artwork (Jon Sarkin)"). If other resource templates are added (e.g., for exhibitions, publications), the MCP tools would need a `resource_type` parameter. Not needed now — all cataloged items use template 2.

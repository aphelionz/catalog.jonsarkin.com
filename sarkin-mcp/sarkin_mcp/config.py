from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    db_host: str = field(default_factory=lambda: os.getenv("MYSQL_HOST", "127.0.0.1"))
    db_port: int = field(default_factory=lambda: int(os.getenv("MYSQL_PORT", "3306")))
    db_name: str = field(default_factory=lambda: os.getenv("MYSQL_DATABASE", "omeka"))
    db_user: str = field(default_factory=lambda: os.getenv("MYSQL_USER", "omeka"))
    db_password: str = field(default_factory=lambda: os.getenv("MYSQL_PASSWORD", "omeka"))

    clip_api_url: str = field(default_factory=lambda: os.getenv("CLIP_API_URL", "http://localhost:8000"))
    catalog_base_url: str = field(default_factory=lambda: os.getenv("CATALOG_BASE_URL", "http://localhost:8888"))

    transport: str = field(default_factory=lambda: os.getenv("MCP_TRANSPORT", "stdio"))
    mcp_host: str = field(default_factory=lambda: os.getenv("MCP_HOST", "0.0.0.0"))
    mcp_port: int = field(default_factory=lambda: int(os.getenv("MCP_PORT", "9000")))

    def item_url(self, item_id: int) -> str:
        return f"{self.catalog_base_url}/s/catalog/item/{item_id}"

    def thumbnail_url(self, storage_id: str, extension: str) -> str:
        return f"{self.catalog_base_url}/files/large/{storage_id}.{extension}"

    def original_url(self, storage_id: str, extension: str) -> str:
        return f"{self.catalog_base_url}/files/original/{storage_id}.{extension}"

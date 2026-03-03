from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, Field


class ItemPayload(BaseModel):
    omeka_item_id: Union[int, str] = Field(..., description="Omeka item id")
    title: Optional[str] = None
    omeka_url: Optional[str] = None
    thumb_url: Optional[str] = None
    catalog_version: Optional[int] = None


class MatchItem(ItemPayload):
    score: float


class SimilarResponse(BaseModel):
    source: ItemPayload
    matches: List[MatchItem]


class SearchResult(BaseModel):
    omeka_item_id: Union[int, str] = Field(..., description="Omeka item id")
    title: Optional[str] = None
    omeka_url: Optional[str] = None
    thumb_url: Optional[str] = None
    score: float
    snippet: Optional[str] = None


class SearchResponse(BaseModel):
    q: str
    limit: int
    offset: int
    preproc_version: int
    embed_model: Optional[str] = None
    results: List[SearchResult]

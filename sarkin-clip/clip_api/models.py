from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, Field


class ItemPayload(BaseModel):
    omeka_item_id: Union[int, str] = Field(..., description="Omeka item id")
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


# ── Iconographic profile ──


class MotifDetail(BaseModel):
    motif: str
    corpus_frequency: int
    corpus_percentage: float


class IconographyResponse(BaseModel):
    omeka_item_id: Union[int, str]
    score: float
    class_number: int
    motifs: List[MotifDetail]
    corpus_size: int


class IconographyBatchItem(BaseModel):
    omeka_item_id: Union[int, str]
    class_number: int


class IconographyBatchResponse(BaseModel):
    items: List[IconographyBatchItem]


# ── Visual search (image upload) ──


class ImageSearchResponse(BaseModel):
    matches: List[MatchItem]

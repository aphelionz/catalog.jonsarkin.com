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


# ── Motif search (DINOv2 patch-level) ──


class MotifMatchItem(BaseModel):
    omeka_item_id: Union[int, str] = Field(..., description="Omeka item id")
    omeka_url: Optional[str] = None
    thumb_url: Optional[str] = None
    score: float
    patch_index: int = 0


class MotifSearchResponse(BaseModel):
    matches: List[MotifMatchItem]


# ── Single-item ingest ──


class IngestRequest(BaseModel):
    image_url: str = Field(..., description="URL of the artwork image to embed")
    title: str = ""
    description: str = ""
    subjects: List[str] = []
    year: Optional[int] = None
    curator_notes: List[str] = []
    omeka_url: str = ""
    thumb_url: str = ""


class IngestResponse(BaseModel):
    status: str
    omeka_item_id: int
    elapsed_seconds: float


# ── DINOv2-only ingest ──


class DinoIngestRequest(BaseModel):
    image_url: str = Field(..., description="URL of the artwork image to embed")
    omeka_url: str = ""
    thumb_url: str = ""


# ── SAM segment search ──


class SegmentMatchItem(BaseModel):
    omeka_item_id: Union[int, str] = Field(..., description="Omeka item id")
    omeka_url: Optional[str] = None
    thumb_url: Optional[str] = None
    score: float
    segment_index: int = 0
    segment_url: Optional[str] = None
    bbox: Optional[List[int]] = None


class SegmentSearchResponse(BaseModel):
    matches: List[SegmentMatchItem]


# ── Segment ingest ──


class SegmentIngestRequest(BaseModel):
    image_url: str = Field(..., description="URL of the artwork image to embed")
    omeka_url: str = ""
    thumb_url: str = ""

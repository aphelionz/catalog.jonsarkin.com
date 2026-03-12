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


# ── Enrichment ──


class EnrichRequest(BaseModel):
    image_url: str = Field(..., description="URL of the artwork image to analyze")
    model: str = Field("sonnet", description="Claude model: haiku, sonnet, or opus")
    field_guidance: Optional[dict] = Field(None, description="Per-field guidance text from resource template")


class UsageInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0


class EnrichResponse(BaseModel):
    transcription: Optional[str] = None
    signature: Optional[str] = None
    date: Optional[str] = None
    medium: Optional[str] = None
    support: Optional[str] = None
    work_type: Optional[str] = None
    motifs: List[str] = []
    condition_notes: Optional[str] = None
    usage: Optional[UsageInfo] = None


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

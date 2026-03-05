"""Iconographic rarity scoring based on motif inverse document frequency."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

# ── Controlled vocabulary split ──
# Visual motifs are detected per-piece by Claude during enrichment.
# Box categories are derived from physical storage and shared by every
# item in that box, so they carry less iconographic signal.

VISUAL_MOTIFS = frozenset({
    "Eyes", "Fish", "Faces", "Hands", "Text Fragments", "Grids",
    "Circles", "Patterns", "Animals", "Names/Words", "Maps", "Numbers",
})

BOX_CATEGORIES = frozenset({
    "Desert", "Comic", "Portraits", "Ladies", "Creature", "Pop Culture",
    "Super Artist", "Boat", "Cardboard Artist", "Ocean", "Tree", "Vehicle",
    "Building", "Spiral/Mouth", "Nipple", "Brancusi", "Skull", "Window",
    "MRI", "Heart", "CBM", "Pencil",
})

VISUAL_WEIGHT = 1.0
BOX_WEIGHT = 0.7
DEFAULT_WEIGHT = 0.85

# Distribution class thresholds (score → class number)
CLASS_THRESHOLDS = [(20, 1), (40, 2), (60, 3), (80, 4), (100, 5)]


@dataclass(frozen=True)
class CorpusStats:
    """Pre-computed corpus-wide motif frequency statistics."""
    total_items: int
    motif_counts: Dict[str, int]  # motif → number of items containing it


@dataclass(frozen=True)
class MotifDetail:
    """Frequency data for a single motif on an item."""
    motif: str
    corpus_frequency: int
    corpus_percentage: float
    weighted_idf: float


@dataclass(frozen=True)
class ItemRarity:
    """Rarity analysis result for a single item."""
    score: float  # 0–100 normalized
    class_number: int  # 1–5
    motif_details: List[MotifDetail] = field(default_factory=list)
    corpus_size: int = 0


def motif_weight(motif: str) -> float:
    """Weight factor by motif category."""
    if motif in VISUAL_MOTIFS:
        return VISUAL_WEIGHT
    if motif in BOX_CATEGORIES:
        return BOX_WEIGHT
    return DEFAULT_WEIGHT


def compute_idf(doc_freq: int, total: int) -> float:
    """IDF with Laplace smoothing: log((N+1) / (1 + df))."""
    return math.log((total + 1) / (1 + doc_freq))


def score_to_class(score: float) -> int:
    """Map a 0–100 score to distribution class 1–5."""
    for threshold, cls in CLASS_THRESHOLDS:
        if score <= threshold:
            return cls
    return 5


def compute_item_rarity(
    subjects: List[str],
    stats: CorpusStats,
) -> ItemRarity:
    """Compute iconographic rarity for a single item."""
    if not subjects or stats.total_items == 0:
        return ItemRarity(score=0.0, class_number=1, corpus_size=stats.total_items)

    details: List[MotifDetail] = []
    weighted_idfs: List[float] = []

    for motif in subjects:
        doc_freq = stats.motif_counts.get(motif, 0)
        idf = compute_idf(doc_freq, stats.total_items)
        w = motif_weight(motif)
        w_idf = idf * w
        weighted_idfs.append(w_idf)

        pct = (doc_freq / stats.total_items) * 100
        details.append(MotifDetail(
            motif=motif,
            corpus_frequency=doc_freq,
            corpus_percentage=round(pct, 1),
            weighted_idf=round(w_idf, 4),
        ))

    # Sort rarest first (highest weighted IDF)
    details.sort(key=lambda d: -d.weighted_idf)

    # Geometric mean of weighted IDFs
    product = 1.0
    for val in weighted_idfs:
        product *= max(val, 0.001)
    geo_mean = product ** (1.0 / len(weighted_idfs))

    # Normalize to 0–100
    max_idf = math.log(stats.total_items + 1)  # theoretical max (df=0)
    raw_score = (geo_mean / max_idf) * 100 if max_idf > 0 else 0.0
    score = max(0.0, min(100.0, round(raw_score, 1)))

    return ItemRarity(
        score=score,
        class_number=score_to_class(score),
        motif_details=details,
        corpus_size=stats.total_items,
    )


def build_corpus_stats(items_subjects: Dict[int, List[str]]) -> CorpusStats:
    """Build corpus statistics from a mapping of item_id → subjects list."""
    motif_counts: Dict[str, int] = {}
    for subjects in items_subjects.values():
        for motif in set(subjects):  # deduplicate per item
            motif_counts[motif] = motif_counts.get(motif, 0) + 1
    return CorpusStats(
        total_items=len(items_subjects),
        motif_counts=motif_counts,
    )

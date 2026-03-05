"""Unit tests for iconographic rarity scoring — pure functions, no I/O."""

from __future__ import annotations

import math

import pytest

from clip_api.rarity import (
    BOX_WEIGHT,
    VISUAL_WEIGHT,
    CorpusStats,
    build_corpus_stats,
    compute_idf,
    compute_item_rarity,
    motif_weight,
    score_to_class,
)


# ── motif_weight ──


def test_visual_motif_weight():
    assert motif_weight("Eyes") == VISUAL_WEIGHT
    assert motif_weight("Fish") == VISUAL_WEIGHT


def test_box_category_weight():
    assert motif_weight("Brancusi") == BOX_WEIGHT
    assert motif_weight("Desert") == BOX_WEIGHT


def test_unknown_motif_weight():
    assert motif_weight("SomethingNew") == 0.85


# ── compute_idf ──


def test_idf_zero_doc_freq():
    """Motif appearing in no items has maximum IDF."""
    assert compute_idf(0, 1000) == math.log(1001 / 1)


def test_idf_all_items():
    """Motif in every item has near-zero IDF."""
    val = compute_idf(1000, 1000)
    assert val == pytest.approx(math.log(1001 / 1001))
    assert val < 0.001


def test_idf_partial():
    assert compute_idf(100, 1000) == pytest.approx(math.log(1001 / 101))


# ── score_to_class ──


def test_class_boundaries():
    assert score_to_class(0) == 1
    assert score_to_class(20) == 1
    assert score_to_class(20.1) == 2
    assert score_to_class(40) == 2
    assert score_to_class(40.1) == 3
    assert score_to_class(60) == 3
    assert score_to_class(60.1) == 4
    assert score_to_class(80) == 4
    assert score_to_class(80.1) == 5
    assert score_to_class(100) == 5


# ── build_corpus_stats ──


def test_build_corpus_stats_empty():
    stats = build_corpus_stats({})
    assert stats.total_items == 0
    assert stats.motif_counts == {}


def test_build_corpus_stats_counts():
    data = {
        1: ["Eyes", "Fish"],
        2: ["Eyes", "Brancusi"],
        3: ["Eyes"],
    }
    stats = build_corpus_stats(data)
    assert stats.total_items == 3
    assert stats.motif_counts["Eyes"] == 3
    assert stats.motif_counts["Fish"] == 1
    assert stats.motif_counts["Brancusi"] == 1


def test_build_corpus_stats_deduplicates_per_item():
    """Duplicate motifs on one item count as 1."""
    data = {1: ["Eyes", "Eyes", "Eyes"]}
    stats = build_corpus_stats(data)
    assert stats.motif_counts["Eyes"] == 1


# ── compute_item_rarity ──


@pytest.fixture()
def sample_stats() -> CorpusStats:
    """Corpus of 1000 items with varied motif frequencies."""
    return CorpusStats(
        total_items=1000,
        motif_counts={
            "Eyes": 800,       # very common
            "Fish": 500,       # common
            "Text Fragments": 300,
            "Grids": 100,
            "Brancusi": 5,     # very rare (box category)
            "MRI": 3,          # very rare (box category)
            "Skull": 20,       # rare (box category)
        },
    )


def test_no_subjects(sample_stats: CorpusStats):
    result = compute_item_rarity([], sample_stats)
    assert result.score == 0.0
    assert result.class_number == 1
    assert result.motif_details == []
    assert result.corpus_size == 1000


def test_common_motifs_low_score(sample_stats: CorpusStats):
    result = compute_item_rarity(["Eyes", "Fish"], sample_stats)
    assert result.score < 30  # should be low
    assert result.class_number <= 2


def test_rare_motifs_high_score(sample_stats: CorpusStats):
    result = compute_item_rarity(["Brancusi", "MRI"], sample_stats)
    assert result.score > 40  # should be notably higher
    assert result.class_number >= 3


def test_mixed_motifs_middle_score(sample_stats: CorpusStats):
    result = compute_item_rarity(["Eyes", "Brancusi"], sample_stats)
    # Geometric mean pulls toward center — not as high as pure rare
    common_only = compute_item_rarity(["Eyes"], sample_stats)
    rare_only = compute_item_rarity(["Brancusi"], sample_stats)
    assert common_only.score < result.score < rare_only.score


def test_details_sorted_rarest_first(sample_stats: CorpusStats):
    result = compute_item_rarity(["Eyes", "Fish", "Brancusi"], sample_stats)
    idfs = [d.weighted_idf for d in result.motif_details]
    assert idfs == sorted(idfs, reverse=True)


def test_details_corpus_percentage(sample_stats: CorpusStats):
    result = compute_item_rarity(["Eyes"], sample_stats)
    detail = result.motif_details[0]
    assert detail.motif == "Eyes"
    assert detail.corpus_frequency == 800
    assert detail.corpus_percentage == 80.0


def test_score_bounded_0_100(sample_stats: CorpusStats):
    result = compute_item_rarity(["Eyes"], sample_stats)
    assert 0.0 <= result.score <= 100.0

    result2 = compute_item_rarity(["MRI"], sample_stats)
    assert 0.0 <= result2.score <= 100.0


def test_unknown_motif_handled(sample_stats: CorpusStats):
    """A motif not in the corpus stats (count=0) gets maximum IDF."""
    result = compute_item_rarity(["NeverSeen"], sample_stats)
    assert result.score > 80  # should be very high — unique motif
    assert result.class_number >= 4


def test_corpus_size_in_result(sample_stats: CorpusStats):
    result = compute_item_rarity(["Eyes"], sample_stats)
    assert result.corpus_size == 1000


def test_visual_weighted_higher_than_box():
    """Same frequency, visual motif should produce higher weighted IDF."""
    stats = CorpusStats(total_items=100, motif_counts={"Eyes": 10, "Desert": 10})
    vis = compute_item_rarity(["Eyes"], stats)
    box = compute_item_rarity(["Desert"], stats)
    assert vis.score > box.score


def test_empty_corpus():
    stats = CorpusStats(total_items=0, motif_counts={})
    result = compute_item_rarity(["Eyes"], stats)
    assert result.score == 0.0
    assert result.class_number == 1

"""niches.estimate_size + DEFAULT_SIZE_THRESHOLDS."""

import pytest
from niches import (
    DEFAULT_SIZE_THRESHOLDS,
    NICHE_SIZE_OVERRIDES,
    estimate_size,
    thresholds_for_niche,
)


def _biz(**kwargs):
    base = {"review_count": 50, "rating_count": 100, "branch_count": 1}
    base.update(kwargs)
    return base


def test_sweet_spot_default():
    assert estimate_size(_biz(review_count=50)) == "sweet_spot"
    assert estimate_size(_biz(review_count=15)) == "sweet_spot"
    assert estimate_size(_biz(review_count=300)) == "sweet_spot"


def test_micro_below_min_reviews():
    # < min_reviews AND < min_reviews * 2 in rating_count → micro
    assert estimate_size(_biz(review_count=5, rating_count=10)) == "micro"
    assert estimate_size(_biz(review_count=0, rating_count=0)) == "micro"


def test_large_by_reviews():
    assert estimate_size(_biz(review_count=400)) == "large"
    assert estimate_size(_biz(review_count=301)) == "large"


def test_large_by_rating_count():
    assert estimate_size(_biz(review_count=100, rating_count=700)) == "large"


def test_large_chain_by_branches():
    # >3 branches → large_chain regardless of review count
    assert estimate_size(_biz(branch_count=5)) == "large_chain"
    assert estimate_size(_biz(review_count=50, branch_count=10)) == "large_chain"


def test_branch_count_3_is_still_sweet_spot():
    # Boundary check: max_branches=3 means 3 is OK, 4 is not
    assert estimate_size(_biz(review_count=50, branch_count=3)) == "sweet_spot"
    assert estimate_size(_biz(review_count=50, branch_count=4)) == "large_chain"


def test_unknown_for_low_review_count_but_high_rating():
    # 8 reviews but 25+ ratings — not clearly micro, not in sweet spot either
    result = estimate_size(_biz(review_count=8, rating_count=25))
    assert result in {"unknown", "micro"}


def test_custom_thresholds():
    """Allow override via niche-specific thresholds."""
    strict = {"min_reviews": 50, "max_reviews": 100, "max_rating_count": 200, "max_branches": 1}
    # rating_count also needs to be below 2x min_reviews to be classified as micro
    assert estimate_size(_biz(review_count=30, rating_count=70), thresholds=strict) == "micro"
    assert estimate_size(_biz(review_count=75), thresholds=strict) == "sweet_spot"
    assert estimate_size(_biz(review_count=150), thresholds=strict) == "large"
    assert estimate_size(_biz(review_count=75, branch_count=2), thresholds=strict) == "large_chain"


def test_thresholds_for_niche_returns_default_for_unknown_slug():
    t = thresholds_for_niche("hair_beauty")
    assert t == DEFAULT_SIZE_THRESHOLDS


def test_thresholds_for_niche_applies_override():
    NICHE_SIZE_OVERRIDES["__test_niche"] = {"min_reviews": 1}
    try:
        t = thresholds_for_niche("__test_niche")
        assert t["min_reviews"] == 1
        # Other thresholds stay default
        assert t["max_reviews"] == DEFAULT_SIZE_THRESHOLDS["max_reviews"]
    finally:
        del NICHE_SIZE_OVERRIDES["__test_niche"]


def test_default_thresholds_are_4_to_10_employees_calibrated():
    """Sanity: defaults match the 4-10 employee target."""
    assert DEFAULT_SIZE_THRESHOLDS["min_reviews"] == 10
    assert DEFAULT_SIZE_THRESHOLDS["max_reviews"] == 300
    assert DEFAULT_SIZE_THRESHOLDS["max_branches"] == 3

"""owner_confidence: cross-card frequency + Serper role hints + bucketing.

Serper is mocked. SQLite isn't touched (we pass a pre-built phone_freq_map).
"""

from unittest.mock import patch
import pytest
import owner_confidence as oc


# ─── score_cross_card_frequency ──────────────────────────────────────────────

def test_cross_card_unique_phone_scores_positive():
    score, _ = oc.score_cross_card_frequency(1)
    assert score == 2


def test_cross_card_two_cards_ambiguous():
    score, _ = oc.score_cross_card_frequency(2)
    assert score == 0


def test_cross_card_three_or_more_penalised():
    assert oc.score_cross_card_frequency(3)[0] == -3
    assert oc.score_cross_card_frequency(7)[0] == -3


# ─── bucket ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,expected", [
    (5, "high"),
    (3, "high"),
    (2, "medium"),
    (1, "medium"),
    (0, "unknown"),
    (-1, "unknown"),
    (-2, "low"),
    (-5, "low"),
])
def test_bucket_thresholds(score, expected):
    assert oc.bucket(score) == expected


# ─── score_serper_role (mocked) ──────────────────────────────────────────────

def test_serper_finds_owner_keyword():
    def fake(q, k, num):
        return {"organic": [
            {"title": "О компании", "snippet": "Директор Иван Петров, тел +7 701 234 5678"},
        ]}
    with patch.object(oc, "_serper_search", side_effect=fake):
        score, why = oc.score_serper_role("+77012345678", "ABC Tour", "Алматы", "fakekey")
    assert score == 2
    assert "owner-keyword" in why


def test_serper_finds_admin_keyword():
    def fake(q, k, num):
        return {"organic": [
            {"title": "Контакты", "snippet": "Ресепшн +7 701 234 5678, режим работы 9-18"},
        ]}
    with patch.object(oc, "_serper_search", side_effect=fake):
        score, why = oc.score_serper_role("+77012345678", "ABC Tour", "Алматы", "fakekey")
    assert score == -3
    assert "admin-keyword" in why


def test_serper_no_role_keywords():
    def fake(q, k, num):
        return {"organic": [
            {"title": "Турпакеты", "snippet": "Цена 200 000 тенге, вылет 1 июня"},
        ]}
    with patch.object(oc, "_serper_search", side_effect=fake):
        score, why = oc.score_serper_role("+77012345678", "ABC Tour", "Алматы", "fakekey")
    assert score == 0


def test_serper_owner_AND_agent_both_apply():
    """If both signals appear in the blob, both contribute (additive scoring)."""
    def fake(q, k, num):
        return {"organic": [
            {"title": "OLX объявление", "snippet": "Директор Иван, тел +7 701 234 5678. agent."},
        ]}
    with patch.object(oc, "_serper_search", side_effect=fake):
        score, why = oc.score_serper_role("+77012345678", "ABC Tour", "Алматы", "fakekey")
    # +2 owner, -1 agent (OLX matches 'olx' regex)
    assert score == 1
    assert "owner-keyword" in why and "agent" in why


def test_serper_skipped_without_key():
    score, why = oc.score_serper_role("+77012345678", "X", "Алматы", serper_key="")
    assert score == 0
    assert "not available" in why


# ─── compute_owner_confidence (integration, no DB needed) ───────────────────

def test_compute_unique_phone_no_serper_is_medium():
    lead = {"phone": "+77012345678", "business_name": "ABC", "city_ru": "Алматы"}
    freq_map = {"+77012345678": 1}
    result = oc.compute_owner_confidence(lead, freq_map, serper_key="", do_serper=False)
    assert result["score"] == 2
    assert result["bucket"] == "medium"
    assert result["card_count"] == 1


def test_compute_multi_card_is_low():
    lead = {"phone": "+77012345678", "business_name": "ABC", "city_ru": "Алматы"}
    freq_map = {"+77012345678": 5}  # appears on 5 cards
    result = oc.compute_owner_confidence(lead, freq_map, serper_key="", do_serper=False)
    assert result["score"] == -3
    assert result["bucket"] == "low"


def test_compute_unique_plus_serper_owner_is_high():
    lead = {"phone": "+77012345678", "business_name": "ABC", "city_ru": "Алматы"}
    freq_map = {"+77012345678": 1}

    def fake(q, k, num):
        return {"organic": [
            {"title": "Anex Tour", "snippet": "директор +7 701 234 5678"},
        ]}
    with patch.object(oc, "_serper_search", side_effect=fake):
        result = oc.compute_owner_confidence(lead, freq_map,
                                              serper_key="fake", do_serper=True)
    assert result["score"] == 4  # +2 cross-card + +2 owner-keyword
    assert result["bucket"] == "high"


def test_compute_phone_not_in_map_defaults_to_1():
    """Phones not in freq_map (new lead, never indexed) default to count=1 → +2."""
    lead = {"phone": "+77019999999", "business_name": "ABC", "city_ru": "Алматы"}
    freq_map = {"+77012345678": 1}  # different phone
    result = oc.compute_owner_confidence(lead, freq_map, serper_key="", do_serper=False)
    assert result["card_count"] == 1
    assert result["score"] == 2  # treated as unique


def test_compute_empty_phone_yields_unknown_neutral():
    """Empty phone → no signal → unknown bucket (not pos / not neg)."""
    lead = {"phone": "", "business_name": "ABC", "city_ru": "Алматы"}
    result = oc.compute_owner_confidence(lead, {}, serper_key="", do_serper=False)
    assert result["card_count"] == 0
    assert result["score"] == 0
    assert result["bucket"] == "unknown"

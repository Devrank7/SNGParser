"""find_owner: Russian name extraction + IG handle picking from Serper results.

All network calls mocked.
"""

from unittest.mock import patch
import pytest
import find_owner as fo


# ─── _pick_owner_name ─────────────────────────────────────────────────────────

def test_pick_name_owner_near_director_word():
    blob = "Парикмахерская «Стиль» — директор Иван Иванов рассказал в интервью..."
    assert fo._pick_owner_name(blob) == "Иван Иванов"


def test_pick_name_with_patronymic():
    blob = "Основатель салона Мария Сергеевна Петрова открыла студию в 2020."
    assert fo._pick_owner_name(blob) == "Мария Сергеевна Петрова"


def test_pick_name_picks_closer_to_hint():
    blob = ("Анна Сидорова — клиент. "
            "Директор Олег Кузнецов открыл новый филиал.")
    # Анна Сидорова has no nearby owner-title; Олег Кузнецов is right next to "Директор"
    assert fo._pick_owner_name(blob) == "Олег Кузнецов"


def test_pick_name_no_hint_returns_empty():
    blob = "Мария Петрова и Анна Сидорова часто посещают этот салон."
    # Russian names present, but no "директор/owner/founder" anywhere
    assert fo._pick_owner_name(blob) == ""


def test_pick_name_skips_city_tokens():
    blob = "Директор салона в Алматы Казахстан рассказал нашему изданию."
    # "Алматы Казахстан" matches the regex but is in SKIP_NAME_TOKENS
    assert fo._pick_owner_name(blob) == ""


def test_pick_name_empty_blob():
    assert fo._pick_owner_name("") == ""


def test_pick_name_only_latin_names_returns_empty():
    blob = "Owner John Smith founded the place"
    # Regex looks for Cyrillic capital-cased pairs only
    assert fo._pick_owner_name(blob) == ""


# ─── _pick_ig_handle ─────────────────────────────────────────────────────────

def test_pick_ig_handle_from_organic_link():
    serp = {"organic": [
        {"link": "https://instagram.com/maria_ivanova", "title": "..."},
    ]}
    assert fo._pick_ig_handle(serp) == "maria_ivanova"


def test_pick_ig_handle_skips_non_user_paths():
    serp = {"organic": [
        {"link": "https://instagram.com/p/CqAbCdE123/", "title": "post"},
        {"link": "https://instagram.com/reel/xyz/", "title": "reel"},
        {"link": "https://instagram.com/maria_ivanova/", "title": "profile"},
    ]}
    assert fo._pick_ig_handle(serp) == "maria_ivanova"


def test_pick_ig_handle_no_instagram_links():
    serp = {"organic": [
        {"link": "https://facebook.com/maria", "title": "fb"},
        {"link": "https://vk.com/maria", "title": "vk"},
    ]}
    assert fo._pick_ig_handle(serp) == ""


def test_pick_ig_handle_empty_organic():
    assert fo._pick_ig_handle({"organic": []}) == ""
    assert fo._pick_ig_handle({}) == ""


def test_pick_ig_handle_with_query_string():
    serp = {"organic": [
        {"link": "https://www.instagram.com/maria.ivanova/?hl=ru", "title": "..."},
    ]}
    assert fo._pick_ig_handle(serp) == "maria.ivanova"


# ─── find_owner full cascade (mocked Serper) ─────────────────────────────────

def test_find_owner_no_serper_key_returns_empty():
    biz = {"name": "Test Salon"}
    r = fo.find_owner(biz, "Алматы", serper_key="")
    assert r["owner_name"] == ""
    assert r["found_via"] is None


def test_find_owner_no_business_name_returns_empty():
    r = fo.find_owner({"name": ""}, "Алматы", serper_key="fakekey")
    assert r["owner_name"] == ""


def test_find_owner_step1_returns_name_step2_returns_handle():
    """Serper #1 finds the name, Serper #2 finds the IG handle."""
    def fake_serper(query, key, num=10):
        if "директор" in query or "founder" in query.lower():
            return {"organic": [
                {"title": "Stilist studio",
                 "snippet": "Директор Анна Иванова открыла салон в 2022"},
            ]}
        if "site:instagram.com" in query.lower():
            return {"organic": [
                {"link": "https://instagram.com/anna_ivanova_stilist"},
            ]}
        return {}

    biz = {"name": "Stilist studio"}
    with patch.object(fo, "_serper", side_effect=fake_serper):
        r = fo.find_owner(biz, "Алматы", serper_key="fake")

    assert r["owner_name"] == "Анна Иванова"
    assert r["owner_instagram"] == "anna_ivanova_stilist"
    assert r["owner_ig_source"] == "serper-auto"
    assert r["found_via"] == "serper_then_ig"


def test_find_owner_step1_finds_name_step2_no_handle():
    """Name found, but no IG account surfaces — return name only."""
    def fake_serper(query, key, num=10):
        if "директор" in query:
            return {"organic": [
                {"title": "X", "snippet": "Founder Иван Иванов запустил студию"},
            ]}
        return {"organic": []}  # site:instagram.com returns nothing

    biz = {"name": "PowerHouse Gym"}
    with patch.object(fo, "_serper", side_effect=fake_serper):
        r = fo.find_owner(biz, "Алматы", serper_key="fake")

    assert r["owner_name"] == "Иван Иванов"
    assert r["owner_instagram"] == ""
    assert r["owner_ig_source"] == ""
    assert r["found_via"] == "serper"


def test_find_owner_step1_finds_no_name_short_circuits():
    """No name in Serper #1 → don't even run Serper #2."""
    call_count = [0]

    def fake_serper(query, key, num=10):
        call_count[0] += 1
        return {"organic": [
            {"title": "Random article", "snippet": "Lots of text but no Russian name pattern"},
        ]}

    biz = {"name": "Empty Place"}
    with patch.object(fo, "_serper", side_effect=fake_serper):
        r = fo.find_owner(biz, "Алматы", serper_key="fake")

    assert r["owner_name"] == ""
    assert r["found_via"] is None
    assert call_count[0] == 1  # only Serper #1 was called, not #2

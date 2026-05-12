"""data_sources.ApifyDataSource._normalize handles the m_mamaev actor output shape.

We feed in synthetic raw items mimicking what we observed live, plus edge
shapes (missing fields, list-valued strings, IG hidden under socials.other).
"""

from data_sources import ApifyDataSource


def test_normalize_full_record():
    raw = {
        "id": "70000001066223318",
        "shortName": "Moi Master",
        "title": "Moi Master, ногтевая студия",
        "address": "улица Льва Толстого, 8",
        "phoneValue": ["+996558399967"],
        "phoneText": ["+996 558‒39‒99‒67"],
        "email": ["moi.master@95mail.ru"],
        "website": None,
        "socials": {
            "whatsapp": ["https://wa.me/996557330237"],
            "other": ["https://instagram.com/moi.master.kg"],
        },
        "rubrics": ["Ногтевые студии", "Брови"],
        "url": "https://2gis.kg/bishkek/firm/70000001066223318",
    }
    b = ApifyDataSource._normalize(raw)
    assert b["twogis_id"] == "70000001066223318"
    assert b["name"] == "Moi Master"  # prefers shortName
    assert b["address"] == "улица Льва Толстого, 8"
    assert b["phones"] == ["+996558399967"]  # phoneValue chosen over phoneText
    assert b["emails"] == ["moi.master@95mail.ru"]
    assert b["website"] == ""  # None → ""
    assert b["instagram"] == "moi.master.kg"  # extracted from socials.other
    assert b["other_socials"].get("whatsapp", "").startswith("https://wa.me/")
    assert b["category"] == "Ногтевые студии"
    assert b["twogis_url"].endswith("/firm/70000001066223318")


def test_normalize_falls_back_to_title_when_no_shortname():
    raw = {"id": "1", "title": "Full Title, тип бизнеса"}
    b = ApifyDataSource._normalize(raw)
    assert b["name"] == "Full Title, тип бизнеса"


def test_normalize_missing_optional_fields():
    raw = {"id": "abc123"}
    b = ApifyDataSource._normalize(raw)
    assert b["twogis_id"] == "abc123"
    assert b["name"] == ""
    assert b["phones"] == []
    assert b["emails"] == []
    assert b["website"] == ""
    assert b["instagram"] == ""
    assert b["other_socials"] == {}


def test_normalize_website_as_list():
    """Edge case: some actor versions return website as ['https://x.com']."""
    raw = {"id": "1", "website": ["https://example.kz"]}
    b = ApifyDataSource._normalize(raw)
    assert b["website"] == "https://example.kz"


def test_normalize_emails_as_string():
    """Edge case: actor might return a single email as a bare string."""
    raw = {"id": "1", "email": "single@example.kz"}
    b = ApifyDataSource._normalize(raw)
    assert b["emails"] == ["single@example.kz"]


def test_normalize_instagram_with_query_string_strips_it():
    """Instagram URL with ?utm or /?hl=ru should yield clean handle."""
    raw = {
        "id": "1",
        "socials": {"other": ["https://instagram.com/parizat.nailstudio/?hl=ru"]},
    }
    b = ApifyDataSource._normalize(raw)
    assert b["instagram"] == "parizat.nailstudio"


def test_normalize_socials_categorized_correctly():
    """Each social platform pattern is detected from its URL."""
    raw = {
        "id": "1",
        "socials": {"other": [
            "https://facebook.com/biz",
            "https://t.me/biz_channel",
            "https://vk.com/biz",
            "https://youtube.com/@biz",
            "https://tiktok.com/@biz",
            "https://instagram.com/biz",
        ]},
    }
    b = ApifyDataSource._normalize(raw)
    assert b["instagram"] == "biz"
    assert "facebook.com/biz" in b["other_socials"]["facebook"]
    assert "t.me/biz_channel" in b["other_socials"]["telegram"]
    assert "vk.com/biz" in b["other_socials"]["vk"]
    assert "youtube.com/@biz" in b["other_socials"]["youtube"]
    assert "tiktok.com/@biz" in b["other_socials"]["tiktok"]


def test_normalize_first_instagram_url_wins_if_multiple():
    raw = {
        "id": "1",
        "socials": {"other": [
            "https://instagram.com/first_one",
            "https://instagram.com/second_one",
        ]},
    }
    b = ApifyDataSource._normalize(raw)
    assert b["instagram"] == "first_one"


def test_normalize_keeps_original_in_raw():
    raw = {"id": "1", "extra": "preserve-me"}
    b = ApifyDataSource._normalize(raw)
    assert b["raw"] is raw  # same object reference, useful for debugging

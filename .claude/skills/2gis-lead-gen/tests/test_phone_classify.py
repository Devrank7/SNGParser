"""phone_classify: KZ/KG mobile vs landline detection."""

import pytest
from phone_classify import classify, normalize, pick_best_mobile


# ─── normalize ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("+7 727 222 3344", "+77272223344"),
    ("8 701 555 1234", "+77015551234"),       # KZ "8 7XX" → "+7 7XX"
    ("87015551234", "+77015551234"),          # no spaces
    ("+996 312 900 100", "+996312900100"),
    ("+996 555 123 456", "+996555123456"),
    ("0555123456", "+996555123456"),          # KG leading-zero local
    ("+1 (555) 123-4567", "+15551234567"),    # USA — should normalize, not mangle
    ("", ""),
    ("not a phone", ""),
    ("(701) 555-1234", "+7015551234"),         # ambiguous — won't add country code
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# ─── classify: Kazakhstan ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_type", [
    # Mobile prefixes
    ("+77001112233", "mobile"),  # 700 — Beeline
    ("+77011112233", "mobile"),  # 701
    ("+77081112233", "mobile"),  # 708
    ("+77471112233", "mobile"),  # 747 — Tele2 (ex-Altel)
    ("+77711112233", "mobile"),  # 771 — Activ
    ("+77781112233", "mobile"),  # 778
    # Landline (city codes)
    ("+77272223344", "landline"),  # Almaty 727
    ("+77172223344", "landline"),  # Astana 7172 starts with 717 (not in mobile list)
    ("+73272223344", "landline"),  # arbitrary 327 — not in mobile list
])
def test_classify_kz(raw, expected_type):
    result = classify(raw)
    assert result["type"] == expected_type
    assert result["country"] == "KZ"
    assert result["normalized"] == raw


# ─── classify: Kyrgyzstan ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_type", [
    # Mobile
    ("+996500123456", "mobile"),  # O! 500
    ("+996555123456", "mobile"),  # MegaCom 555
    ("+996700123456", "mobile"),  # Beeline KG 700
    ("+996770123456", "mobile"),  # 770
    ("+996990123456", "mobile"),  # NurTelecom 990
    ("+996999123456", "mobile"),  # 999
    # Landline
    ("+996312900100", "landline"),  # Bishkek
    ("+996392212345", "landline"),  # Osh 3922 starts with 392 (not mobile)
])
def test_classify_kg(raw, expected_type):
    result = classify(raw)
    assert result["type"] == expected_type
    assert result["country"] == "KG"


# ─── classify: edge cases ─────────────────────────────────────────────────────

def test_classify_empty():
    r = classify("")
    assert r["type"] == "unknown"
    assert r["country"] is None
    assert r["normalized"] == ""


def test_classify_garbage():
    r = classify("hello world")
    assert r["type"] == "unknown"
    assert r["country"] is None


def test_classify_unrecognized_country():
    # USA number — neither KZ nor KG prefix
    r = classify("+15551234567")
    assert r["type"] == "unknown"
    assert r["country"] is None


def test_classify_normalizes_then_evaluates():
    # User-formatted KZ mobile in messy form
    r = classify("8 (701) 555-12-34")
    assert r["type"] == "mobile"
    assert r["country"] == "KZ"
    assert r["normalized"] == "+77015551234"


# ─── Regression: ITU/regulator-confirmed NON-mobile KZ blocks ────────────────
# These prefixes look mobile (start with 7xx) but are actually VoIP / satellite.
# Until 2026-05-12 we were misclassifying them as mobile and sending WhatsApp
# messages into the void.

@pytest.mark.parametrize("raw", [
    "+77501234567",  # dial-up / VoIP
    "+77511234567",  # VoIP access codes
    "+77601234567",  # Kulan satellite
    "+77611234567",
    "+77621234567",
    "+77631234567",  # Arna commercial IP
    "+77641234567",
])
def test_kz_voip_satellite_not_mobile(raw):
    r = classify(raw)
    assert r["type"] == "landline", f"{raw} must be classified non-mobile (VoIP/satellite)"
    assert r["country"] == "KZ"


# ─── Regression: KG Sky Mobile / Beeline 22x block ───────────────────────────
# 22x was missing from our list entirely — we were classifying valid Beeline KG
# mobiles as landline and dropping those leads from the phone tier.

@pytest.mark.parametrize("raw", [
    "+996220123456",
    "+996225123456",
    "+996229123456",
])
def test_kg_220_block_is_mobile(raw):
    r = classify(raw)
    assert r["type"] == "mobile", f"{raw} must be classified mobile (Beeline KG 22x)"
    assert r["country"] == "KG"


# ─── pick_best_mobile ─────────────────────────────────────────────────────────

def test_pick_best_mobile_picks_first_mobile():
    phones = ["+77272223344", "+77011234567"]  # landline first, mobile second
    picked = pick_best_mobile(phones)
    assert picked is not None
    assert picked["type"] == "mobile"
    assert picked["normalized"] == "+77011234567"


def test_pick_best_mobile_returns_none_when_no_mobile():
    phones = ["+77272223344", "+996312900100"]  # both landline
    assert pick_best_mobile(phones) is None


def test_pick_best_mobile_empty_list():
    assert pick_best_mobile([]) is None


def test_pick_best_mobile_with_garbage():
    phones = ["not a phone", "", "+77019998877"]
    picked = pick_best_mobile(phones)
    assert picked is not None
    assert picked["type"] == "mobile"

"""website_check: 3-way "has website?" detection.

DNS resolution is mocked so tests stay offline and deterministic.
"""

from unittest.mock import patch
import pytest
import website_check as wc


# ─── _extract_root_domain ─────────────────────────────────────────────────────

@pytest.mark.parametrize("url_or_text,expected", [
    ("https://salon.kz", "salon.kz"),
    ("http://www.salon.kz/about", "salon.kz"),
    ("salon.kz", "salon.kz"),
    ("https://SUB.SALON.kz/", "sub.salon.kz"),
    ("", ""),
    ("just text no url", ""),
    ("https://linktr.ee/mysalon", "linktr.ee"),
])
def test_extract_root_domain(url_or_text, expected):
    assert wc._extract_root_domain(url_or_text) == expected


# ─── _is_real_domain ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("host,expected", [
    ("salon.kz", True),
    ("mybiz.kg", True),
    ("nailstudio-almaty.com", True),
    ("instagram.com", False),               # social — not a real website
    ("linktr.ee", False),                   # link-in-bio service
    ("mysalon.linktr.ee", False),           # subdomain of placeholder still placeholder
    ("taplink.cc", False),
    ("wa.me", False),                       # WhatsApp link
    ("fb.com", False),
    ("", False),
    ("no-dot", False),                      # no TLD
])
def test_is_real_domain(host, expected):
    assert wc._is_real_domain(host) is expected


# ─── check_website_field ──────────────────────────────────────────────────────

def test_check_website_field_real_site():
    r = wc.check_website_field("https://mysalon.kz")
    assert r["has_website"] is True
    assert r["evidence"] == "2gis_website_field"
    assert r["domain"] == "mysalon.kz"


def test_check_website_field_linktree_not_a_site():
    r = wc.check_website_field("https://linktr.ee/mysalon")
    assert r["has_website"] is False
    assert r["evidence"] is None


def test_check_website_field_empty():
    r = wc.check_website_field("")
    assert r["has_website"] is False


def test_check_website_field_instagram_url_ignored():
    r = wc.check_website_field("https://instagram.com/mysalon")
    assert r["has_website"] is False


# ─── check_instagram_bio ──────────────────────────────────────────────────────

def test_check_ig_bio_external_url_real_site():
    profile = {"biography": "best nails in town", "external_url": "https://mysalon.kz"}
    r = wc.check_instagram_bio(profile)
    assert r["has_website"] is True
    assert r["evidence"] == "ig_bio_link"
    assert r["domain"] == "mysalon.kz"


def test_check_ig_bio_external_url_linktree_doesnt_count():
    profile = {"biography": "all my links 👇", "external_url": "https://linktr.ee/me"}
    r = wc.check_instagram_bio(profile)
    assert r["has_website"] is False


def test_check_ig_bio_url_inside_biography_text():
    profile = {"biography": "Запись: https://salon.kg/book ✨", "external_url": ""}
    r = wc.check_instagram_bio(profile)
    assert r["has_website"] is True
    assert r["evidence"] == "ig_bio_link"


def test_check_ig_bio_no_profile():
    r = wc.check_instagram_bio(None)
    assert r["has_website"] is False


def test_check_ig_bio_no_urls_anywhere():
    profile = {"biography": "Маникюр Бишкек запись @nailstudio", "external_url": ""}
    r = wc.check_instagram_bio(profile)
    assert r["has_website"] is False


# ─── check_corporate_email (DNS mocked) ──────────────────────────────────────

def test_check_email_corp_domain_resolves():
    with patch.object(wc, "_resolves", return_value=True):
        r = wc.check_corporate_email(["info@mysalon.kz"], do_dns=True)
    assert r["has_website"] is True
    assert r["evidence"] == "corporate_email"
    assert r["domain"] == "mysalon.kz"


def test_check_email_corp_domain_does_not_resolve():
    with patch.object(wc, "_resolves", return_value=False):
        r = wc.check_corporate_email(["info@fakedomain12345.kz"], do_dns=True)
    assert r["has_website"] is False


def test_check_email_freemail_ignored():
    # gmail / mail.ru / yandex are NOT corporate
    r = wc.check_corporate_email(
        ["mybiz@gmail.com", "owner@mail.ru", "info@yandex.kz"],
        do_dns=False,
    )
    assert r["has_website"] is False


def test_check_email_skips_invalid():
    r = wc.check_corporate_email(["", "not-an-email", "x@"])
    assert r["has_website"] is False


def test_check_email_picks_first_corp():
    with patch.object(wc, "_resolves", return_value=True):
        r = wc.check_corporate_email(
            ["user@gmail.com", "info@realsite.kz", "x@othersite.kz"],
            do_dns=True,
        )
    # Should skip gmail and stop at realsite.kz
    assert r["has_website"] is True
    assert r["domain"] == "realsite.kz"


# ─── check_business (full 3-way short-circuit) ────────────────────────────────

def test_check_business_short_circuits_on_2gis_field():
    """If 2GIS website is real, we don't even look at IG bio / email."""
    biz = {"website": "https://mysalon.kz", "emails": ["info@gmail.com"]}
    r = wc.check_business(biz, ig_profile=None, do_dns=False)
    assert r["has_website"] is True
    assert r["evidence"] == "2gis_website_field"


def test_check_business_falls_through_to_ig_bio():
    biz = {"website": "", "emails": []}
    profile = {"external_url": "https://mybiz.kg", "biography": ""}
    r = wc.check_business(biz, ig_profile=profile, do_dns=False)
    assert r["has_website"] is True
    assert r["evidence"] == "ig_bio_link"


def test_check_business_falls_through_to_email():
    biz = {"website": "", "emails": ["info@realbiz.kz"]}
    with patch.object(wc, "_resolves", return_value=True):
        r = wc.check_business(biz, ig_profile=None, do_dns=True)
    assert r["has_website"] is True
    assert r["evidence"] == "corporate_email"


def test_check_business_no_website_anywhere():
    """The desirable lead case — no website signal in any of the 3 channels."""
    biz = {"website": "", "emails": ["mybiz@gmail.com"]}  # freemail = no domain proof
    profile = {"external_url": "https://linktr.ee/me", "biography": ""}
    r = wc.check_business(biz, ig_profile=profile, do_dns=False)
    assert r["has_website"] is False
    assert r["evidence"] is None

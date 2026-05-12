"""Three-way 'does this business have a website?' check.

Order (any pass → has website, skip business):
  1. 2GIS `website` field is non-empty AND not a social-media-only link.
  2. Instagram bio externalUrl points to a real domain (not linktr.ee / taplink etc -- these are still "no real website").
  3. Corporate email domain (anything@theirdomain.kz) where that domain resolves & isn't a freemail.

The point: if any of these is true, the business already has online presence
and is unlikely to buy a basic website. We skip them.
"""

import re
import socket
from typing import Optional
from urllib.parse import urlparse

# Domains we DON'T count as "having a website" — these are placeholder/link-in-bio
# services or social platforms that small businesses use as a website stand-in.
SOCIAL_PLACEHOLDER_DOMAINS = {
    "linktr.ee", "lnk.bio", "taplink.cc", "taplink.com", "milkshake.app",
    "bio.fm", "campsite.bio", "beacons.ai", "linkin.bio", "later.com",
    "instagram.com", "facebook.com", "fb.com", "fb.me", "vk.com", "vk.ru",
    "t.me", "telegram.me", "tiktok.com", "youtube.com", "wa.me",
    "api.whatsapp.com", "tenor.com",
}

# Freemail / public providers — an email at one of these does NOT prove the
# business owns a domain.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com",
    "mail.ru", "list.ru", "bk.ru", "inbox.ru",
    "yandex.ru", "yandex.kz", "yandex.kg", "ya.ru",
    "yahoo.com", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com",
    "rambler.ru",
}

URL_RE = re.compile(r"https?://[^\s)>\"']+", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+)\b")


def _extract_root_domain(url_or_text: str) -> str:
    if not url_or_text:
        return ""
    s = url_or_text.strip()
    if "://" not in s:
        s = "http://" + s
    try:
        host = urlparse(s).hostname or ""
    except Exception:
        host = ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    # A real hostname has at least one dot and no whitespace. Plain prose
    # ("just some words") would otherwise leak through as a fake hostname.
    if "." not in host or " " in host:
        return ""
    return host


def _is_real_domain(host: str) -> bool:
    if not host or "." not in host:
        return False
    if host in SOCIAL_PLACEHOLDER_DOMAINS:
        return False
    # Sub-of-social (e.g. mybiz.linktr.ee) → still placeholder.
    for sd in SOCIAL_PLACEHOLDER_DOMAINS:
        if host.endswith("." + sd):
            return False
    return True


def _resolves(host: str, timeout: float = 2.0) -> bool:
    """Cheap DNS check — does this domain actually resolve?"""
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(host)
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)


def check_website_field(twogis_website: str) -> dict:
    host = _extract_root_domain(twogis_website)
    if host and _is_real_domain(host):
        return {"has_website": True, "evidence": "2gis_website_field", "domain": host, "url": twogis_website}
    return {"has_website": False, "evidence": None, "domain": host, "url": twogis_website}


def check_instagram_bio(profile: Optional[dict]) -> dict:
    """profile is from ApifyDataSource.fetch_instagram_profile (or None)."""
    if not profile:
        return {"has_website": False, "evidence": None}
    candidates = []
    if profile.get("external_url"):
        candidates.append(profile["external_url"])
    candidates.extend(profile.get("external_urls", []) or [])
    # Also scan the biography text for http(s) URLs.
    bio = profile.get("biography", "") or ""
    candidates.extend(URL_RE.findall(bio))
    for c in candidates:
        host = _extract_root_domain(c if isinstance(c, str) else c.get("url", ""))
        if host and _is_real_domain(host):
            return {"has_website": True, "evidence": "ig_bio_link", "domain": host, "url": c}
    return {"has_website": False, "evidence": None}


def check_corporate_email(emails: list, do_dns: bool = True) -> dict:
    for e in emails or []:
        if "@" not in e:
            continue
        domain = e.split("@", 1)[1].strip().lower().rstrip(".")
        if not domain or domain in FREEMAIL_DOMAINS:
            continue
        if not _is_real_domain(domain):
            continue
        if do_dns and not _resolves(domain):
            continue
        return {"has_website": True, "evidence": "corporate_email", "domain": domain, "email": e}
    return {"has_website": False, "evidence": None}


def check_business(business: dict, ig_profile: Optional[dict] = None, do_dns: bool = True) -> dict:
    """Combine all three checks. Returns the FIRST positive (short-circuit)."""
    r = check_website_field(business.get("website", ""))
    if r["has_website"]:
        return r
    r = check_instagram_bio(ig_profile)
    if r["has_website"]:
        return r
    r = check_corporate_email(business.get("emails", []), do_dns=do_dns)
    if r["has_website"]:
        return r
    return {"has_website": False, "evidence": None}

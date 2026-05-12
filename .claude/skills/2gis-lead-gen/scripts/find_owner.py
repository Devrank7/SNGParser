"""Owner discovery via Serper.dev — two cheap Google Search calls, no Instagram scraping.

Pipeline (revised 2026-05-12):
  1. Serper query: `"{business}" {city} директор|founder|owner|владелец`
     Parse snippets/titles for a Russian "Имя Фамилия" near owner-title hints.
  2. If a name was found, follow-up Serper query: `"{name}" {city} site:instagram.com`
     Take the first instagram.com URL whose handle isn't a generic path
     (p, reel, explore, etc.). This handle is the BEST-GUESS — Serper can be
     wrong (homonyms, employees, randoms). The handle is always written to
     Google Sheets with `owner_ig_source = "serper-auto"` so the manager
     knows to verify it manually before reaching out.

What we DON'T do (and why):
  • No Instagram bio fetching (would cost ~$2.30/1K profiles for low value).
  • No mobile-phone scraping from bios (noisy + tier 1 already gives us a
    phone when 2GIS has one; otherwise the manager handles outreach via IG).

Return shape:
  {
    "owner_name": str,
    "owner_phone": "",            # always empty now — kept for back-compat
    "owner_instagram": str,       # personal handle if Serper found one
    "owner_ig_source": "serper-auto" | "",
    "found_via": "serper" | "serper_then_ig" | None
  }
"""

import json
import re
import sys
import urllib.request

SERPER_URL = "https://google.serper.dev/search"

RUS_NAME_RE = re.compile(
    r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?\b"
)

OWNER_TITLE_HINTS = [
    "директор", "владелец", "владелица", "founder", "ceo", "co-founder",
    "основатель", "основательница", "учредитель", "руководитель",
    "owner", "proprietor",
]

# Names that look right but aren't people.
SKIP_NAME_TOKENS = {
    "алматы", "бишкек", "астана", "ош", "казахстан", "кыргызстан",
    "россия", "москва", "санкт", "петербург",
}

# Instagram URL paths that aren't user handles.
IG_NON_HANDLE_PATHS = {"p", "reel", "reels", "explore", "tv", "stories", "accounts", "direct"}


def _serper(query: str, api_key: str, num: int = 10) -> dict:
    body = json.dumps({"q": query, "num": num, "gl": "kz", "hl": "ru"}).encode()
    req = urllib.request.Request(
        SERPER_URL, data=body, method="POST",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[serper] query failed ({query!r}): {e}", file=sys.stderr)
        return {}


def _pick_owner_name(blob: str) -> str:
    if not blob:
        return ""
    blob_lower = blob.lower()
    # Owner-title words ("Директор", "Основатель") start with a capital letter
    # and look like a first-name to the regex. Skip any match whose first
    # token is one of them — otherwise "Директор Олег Кузнецов" gets captured
    # as a three-word name.
    title_words_lower = {h.lower() for h in OWNER_TITLE_HINTS}
    best, best_distance = None, 10**9
    for m in RUS_NAME_RE.finditer(blob):
        groups = [g for g in m.groups() if g]
        if not groups:
            continue
        # If the regex greedily grabbed a title word as the first token
        # (e.g. "Директор Олег Кузнецов" → ["Директор","Олег","Кузнецов"]),
        # drop the title and keep the rest as the candidate name.
        if groups[0].lower() in title_words_lower:
            groups = groups[1:]
        if len(groups) < 2:  # need at least Имя+Фамилия
            continue
        if any(w.lower() in SKIP_NAME_TOKENS for w in groups):
            continue
        full = " ".join(groups)
        for hint in OWNER_TITLE_HINTS:
            i = blob_lower.find(hint, max(0, m.start() - 200), m.end() + 200)
            if i != -1:
                d = min(abs(i - m.start()), abs(i - m.end()))
                if d < best_distance:
                    best_distance, best = d, full
    return best or ""


def _pick_ig_handle(serper_result: dict) -> str:
    """Pull the first usable IG handle from a Serper result's organic links."""
    for r in serper_result.get("organic", []) or []:
        url = r.get("link", "") or ""
        m = re.search(r"instagram\.com/([a-zA-Z0-9._]{2,30})", url)
        if not m:
            continue
        handle = m.group(1)
        if handle.lower() in IG_NON_HANDLE_PATHS:
            continue
        return handle
    return ""


def find_owner(business: dict, city_ru: str, data_source=None, serper_key: str = "",
               ig_cache: dict = None) -> dict:
    """Two Serper calls — name first, then optional IG handle. No IG bio fetching.

    `data_source` and `ig_cache` are accepted for back-compat but unused.
    """
    if not serper_key:
        return _empty_result()
    name = business.get("name", "")
    if not name:
        return _empty_result()

    # Step 1 — find the owner's name.
    q1 = f'"{name}" {city_ru} директор OR founder OR owner OR владелец'
    res1 = _serper(q1, serper_key, num=10)
    blob = " ".join(
        (r.get("title", "") + " " + r.get("snippet", ""))
        for r in (res1.get("organic", []) or [])
    )
    owner_name = _pick_owner_name(blob)
    if not owner_name:
        return _empty_result()

    # Step 2 — try to find their personal IG handle.
    q2 = f'"{owner_name}" {city_ru} site:instagram.com'
    res2 = _serper(q2, serper_key, num=5)
    handle = _pick_ig_handle(res2)

    return {
        "owner_name": owner_name,
        "owner_phone": "",
        "owner_instagram": handle,
        "owner_ig_source": "serper-auto" if handle else "",
        "found_via": "serper_then_ig" if handle else "serper",
    }


def _empty_result() -> dict:
    return {
        "owner_name": "",
        "owner_phone": "",
        "owner_instagram": "",
        "owner_ig_source": "",
        "found_via": None,
    }

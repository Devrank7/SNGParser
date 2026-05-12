"""Pluggable 2GIS data sources.

Resolution order at runtime:
  1. TWOGIS_API_KEY set in .env.local → Direct2GisDataSource (free)
  2. APIFY_API_KEY set in .env.local → ApifyDataSource (~$4.50 / 1K)
  3. neither → raise

Both sources expose the same interface:

    source.search(city_slug, niche_queries, max_results) -> List[Business]

Where each Business dict has the unified keys defined in `BUSINESS_FIELDS`.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from typing import List, Optional

from niches import CITIES


BUSINESS_FIELDS = [
    "twogis_id",         # unique business id
    "name",
    "address",
    "phones",            # list of raw phone strings
    "website",           # str or ""
    "emails",            # list of email strings
    "instagram",         # company instagram username or ""
    "other_socials",     # dict {"vk": "...", "telegram": "...", "whatsapp": "..."}
    "category",
    "twogis_url",        # canonical 2gis.kz/kg link if available
    # Size signals — used by run.py to filter micro / large businesses out.
    "review_count",      # 2GIS review count (proxy for traffic / employees)
    "rating_count",      # 2GIS rating count (similar proxy)
    "branch_count",      # number of branches for this brand (1 = single shop)
    "raw",               # original record from upstream, for debugging
]


def _empty_business():
    return {
        "twogis_id": "",
        "name": "",
        "address": "",
        "phones": [],
        "website": "",
        "emails": [],
        "instagram": "",
        "other_socials": {},
        "category": "",
        "twogis_url": "",
        "review_count": 0,
        "rating_count": 0,
        "branch_count": 1,
        "raw": None,
    }


# =====================================================================
# Apify source
# =====================================================================
# Primary 2GIS actor: m_mamaev/2gis-places-scraper.
#   - 22K+ runs, 489 users, 5.0 rating — well-tested.
#   - No actor-internal free-tier gate (zen-studio has one and locks after 1 run).
#   - PAY_PER_EVENT; charges per item + extra per "add contacts" event.
#   - Returns `phoneValue`, `email`, `website`, `socials` (whatsapp/instagram under "other").
#   - Server-side "without website" filter is NOT supported; we filter client-side.

APIFY_2GIS_ACTOR = "m_mamaev~2gis-places-scraper"
APIFY_PROFILE_ACTOR = "apify~instagram-profile-scraper"
APIFY_BASE = "https://api.apify.com/v2"

# Domain mapping per supported country.
COUNTRY_TO_2GIS_DOMAIN = {
    "KZ": "2gis.kz",
    "KG": "2gis.kg",
}

# Per-result cost (USD). m_mamaev pricing per public marketplace info.
# Used only for estimates; real spend comes from Apify balance API.
COST_PER_PLACE = 0.002   # ~$2 per 1000 places
COST_PER_CONTACT = 0.0008  # extra when includeContacts=true


def _apify_request(method: str, path: str, token: str, body=None, timeout: int = 600):
    url = f"{APIFY_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _apify_run_sync(actor: str, token: str, payload: dict, timeout: int = 900) -> list:
    """Run an actor synchronously and return its dataset items."""
    # Apify URL-encodes actor slugs with ~ between user and actor name.
    path = f"/acts/{actor}/run-sync-get-dataset-items?token={token}"
    url = APIFY_BASE + path
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class ApifyDataSource:
    name = "apify"
    cost_per_1k_places = (COST_PER_PLACE + COST_PER_CONTACT) * 1000  # ~$2.80 / 1K

    def __init__(self, token: str):
        self.token = token

    def get_balance_usd(self) -> Optional[float]:
        """Return remaining monthly free credit in USD (best-effort)."""
        try:
            data = _apify_request("GET", f"/users/me/limits?token={self.token}", self.token)
            # The limits response includes "monthlyUsageUsd" and the plan's "maxMonthlyUsageUsd".
            current = data.get("data", {}).get("current", {})
            limits = data.get("data", {}).get("limits", {})
            spent = float(current.get("monthlyUsageUsd", 0))
            cap = float(limits.get("maxMonthlyUsageUsd", 5.0))
            return max(0.0, cap - spent)
        except Exception as e:
            print(f"[apify] balance lookup failed: {e}", file=sys.stderr)
            return None

    def search(self, city_slug: str, niche_queries: List[str], max_results: int) -> List[dict]:
        city = CITIES[city_slug]
        # max_results is the TOTAL we want; actor's maxItems is per query.
        per_query = max(20, max_results // max(1, len(niche_queries)))
        domain = COUNTRY_TO_2GIS_DOMAIN.get(city["country"], "")
        # m_mamaev has no `filterWithoutWebsite` switch — we filter client-side.
        # We DO need includeContacts=true to get phones/emails/socials.
        payload = {
            "query": niche_queries,
            "locationQuery": city["name_ru"],
            "domain": domain,
            "language": "ru",
            "maxItems": per_query,
            "includeContacts": True,
            "maxReviewsPerPlace": 0,
            "maxMediaPerPlace": 0,
        }
        print(f"[apify] running {APIFY_2GIS_ACTOR} on {domain} for {city['name_ru']} × "
              f"{niche_queries} × {per_query}/query (includeContacts=true)",
              file=sys.stderr)
        try:
            items = _apify_run_sync(APIFY_2GIS_ACTOR, self.token, payload, timeout=1200)
        except Exception as e:
            raise RuntimeError(f"Apify 2GIS actor failed: {e}")

        print(f"[apify] received {len(items)} raw items", file=sys.stderr)
        out = []
        for i, item in enumerate(items):
            try:
                out.append(self._normalize(item))
            except Exception as e:
                print(f"[apify] item {i} normalize failed: {e}; keys={list(item.keys())[:10]}",
                      file=sys.stderr)
        return out

    @staticmethod
    def _normalize_ig_profile(p: dict, fallback_username: str = "") -> dict:
        return {
            "username": p.get("username", fallback_username),
            "full_name": p.get("fullName", "") or p.get("full_name", ""),
            "biography": p.get("biography", "") or "",
            "external_url": p.get("externalUrl", "") or p.get("external_url", "") or "",
            "external_urls": p.get("externalUrls", []) or [],
            "followers": p.get("followersCount", 0) or p.get("followers_count", 0),
            "is_business": bool(p.get("isBusinessAccount") or p.get("is_business")),
            "raw": p,
        }

    def fetch_instagram_profile(self, username: str) -> Optional[dict]:
        """Single-profile fetch — convenience wrapper around the batch path."""
        result = self.fetch_instagram_profiles([username])
        return result.get(username.strip().lstrip("@")) if username else None

    def fetch_instagram_profiles(self, usernames: List[str]) -> dict:
        """Batch-fetch IG profiles. Returns {handle_lower: profile_dict}.

        Each Apify actor-run costs $0.007 to start; bundling N handles into one
        run cuts cost by ~N×. Apify recommends batches under ~100 to keep run
        time predictable. We chunk into batches of 50.
        """
        clean = sorted({u.strip().lstrip("@").lower() for u in usernames if u and u.strip()})
        if not clean:
            return {}

        out = {}
        BATCH = 50
        for i in range(0, len(clean), BATCH):
            chunk = clean[i:i + BATCH]
            try:
                items = _apify_run_sync(APIFY_PROFILE_ACTOR, self.token,
                                        {"usernames": chunk}, timeout=600)
            except Exception as e:
                print(f"[apify] IG batch {i}..{i+len(chunk)} failed: {e}", file=sys.stderr)
                continue
            for it in items:
                h = (it.get("username") or "").lstrip("@").lower()
                if h:
                    out[h] = self._normalize_ig_profile(it, h)
            print(f"[apify] IG batch {i+1}..{i+len(chunk)} → got {sum(1 for h in chunk if h in out)}/{len(chunk)}",
                  file=sys.stderr)
        return out

    @staticmethod
    def _normalize(item: dict) -> dict:
        """Normalize a m_mamaev/2gis-places-scraper output item."""
        b = _empty_business()
        b["twogis_id"] = str(item.get("id") or "")
        b["name"] = item.get("shortName") or item.get("title") or ""
        b["address"] = item.get("address") or ""

        # Phones — prefer the raw E.164-ish phoneValue over formatted phoneText.
        phones = item.get("phoneValue") or item.get("phoneText") or []
        if isinstance(phones, str):
            phones = [phones]
        b["phones"] = [str(p) for p in phones if p]

        # Website — can be None or string.
        web = item.get("website")
        if isinstance(web, list):
            web = web[0] if web else ""
        b["website"] = str(web).strip() if web else ""

        # Emails — usually a list of strings.
        emails = item.get("email") or item.get("emails") or []
        if isinstance(emails, str):
            emails = [emails]
        b["emails"] = [str(e) for e in emails if e]

        # Socials. m_mamaev returns:
        #   {"whatsapp": [...], "telegram": [...], "instagram": [...], "other": [...]}
        # Instagram URLs sometimes appear in "other" — scan all lists.
        socials = item.get("socials") or {}
        instagram_url = None
        if isinstance(socials, dict):
            for key, val in socials.items():
                items_list = val if isinstance(val, list) else [val]
                for v in items_list:
                    if not v or not isinstance(v, str):
                        continue
                    lower = v.lower()
                    if "instagram.com/" in lower and not instagram_url:
                        instagram_url = v
                    elif "facebook.com" in lower:
                        b["other_socials"].setdefault("facebook", v)
                    elif "wa.me" in lower or "whatsapp" in lower:
                        b["other_socials"].setdefault("whatsapp", v)
                    elif "t.me" in lower or "telegram" in lower:
                        b["other_socials"].setdefault("telegram", v)
                    elif "vk.com" in lower or "vk.ru" in lower:
                        b["other_socials"].setdefault("vk", v)
                    elif "youtube.com" in lower or "youtu.be" in lower:
                        b["other_socials"].setdefault("youtube", v)
                    elif "tiktok.com" in lower:
                        b["other_socials"].setdefault("tiktok", v)
        if instagram_url:
            # Strip query string + trailing slash BEFORE splitting on `/`.
            # Otherwise `instagram.com/parizat.nailstudio/?hl=ru` ends with
            # `?hl=ru` after the last `/`, and the handle disappears.
            clean = instagram_url.split("?")[0].rstrip("/")
            b["instagram"] = clean.split("/")[-1].lstrip("@")

        rubrics = item.get("rubrics") or []
        b["category"] = rubrics[0] if rubrics else (item.get("category") or "")
        b["twogis_url"] = item.get("url") or ""

        # Size signals: reviews + ratings + branch count
        b["review_count"] = int(item.get("reviewsCount") or 0)
        b["rating_count"] = int(item.get("ratingCount") or 0)
        brand = item.get("brand") or {}
        if isinstance(brand, dict):
            b["branch_count"] = int(brand.get("branchCount") or 1)

        b["raw"] = item
        return b


# =====================================================================
# Direct 2GIS Catalog API source (free if you have a key with contact_groups)
# =====================================================================

TWOGIS_BASE = "https://catalog.api.2gis.com"


class Direct2GisDataSource:
    name = "direct_2gis"
    cost_per_1k_places = 0.0

    def __init__(self, token: str, apify_token: Optional[str] = None):
        # We still need Apify for Instagram profile bio lookup, since 2GIS
        # doesn't return IG bio (only the IG handle).
        self.token = token
        self.apify_token = apify_token

    def get_balance_usd(self) -> Optional[float]:
        return None  # Direct API doesn't expose remaining balance.

    def search(self, city_slug: str, niche_queries: List[str], max_results: int) -> List[dict]:
        city = CITIES[city_slug]
        results = []
        per_query = max(1, max_results // max(1, len(niche_queries)))
        for q in niche_queries:
            results.extend(self._search_one(q, city["twogis_city_id"], per_query))
            if len(results) >= max_results:
                break
        # Deduplicate by twogis_id within this run.
        seen, deduped = set(), []
        for b in results:
            if b["twogis_id"] and b["twogis_id"] not in seen:
                seen.add(b["twogis_id"])
                deduped.append(b)
        return deduped[:max_results]

    def _search_one(self, query: str, city_id: str, max_n: int) -> List[dict]:
        out = []
        page = 1
        while len(out) < max_n and page <= 20:  # 2gis caps deep pagination
            params = {
                "q": query,
                "city_id": city_id,
                "fields": "items.point,items.contact_groups,items.org,items.address,items.adm_div,items.rubrics,items.external_content",
                "page": page,
                "page_size": 50,
                "key": self.token,
            }
            url = f"{TWOGIS_BASE}/3.0/items?" + urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = json.loads(resp.read())
            except Exception as e:
                print(f"[direct_2gis] page {page} failed: {e}", file=sys.stderr)
                break
            meta = data.get("meta", {})
            if meta.get("code") not in (200, 0):
                err = meta.get("error", {}).get("message", "unknown")
                print(f"[direct_2gis] API error: {err}", file=sys.stderr)
                break
            items = data.get("result", {}).get("items", [])
            if not items:
                break
            out.extend(self._normalize(it) for it in items)
            page += 1
            time.sleep(0.2)  # be polite
        return out

    def fetch_instagram_profile(self, username: str) -> Optional[dict]:
        """Direct 2GIS doesn't fetch IG. Delegate to Apify if available, else None."""
        if not self.apify_token:
            return None
        return ApifyDataSource(self.apify_token).fetch_instagram_profile(username)

    @staticmethod
    def _normalize(item: dict) -> dict:
        b = _empty_business()
        b["twogis_id"] = str(item.get("id") or "")
        b["name"] = item.get("name", "")
        b["address"] = (item.get("address_name") or
                        item.get("address", {}).get("name", "") if isinstance(item.get("address"), dict) else "") or ""
        # contact_groups → flatten phones/websites/emails/socials.
        for group in item.get("contact_groups", []) or []:
            for c in group.get("contacts", []) or []:
                t = c.get("type")
                v = c.get("value") or c.get("text") or ""
                if not v:
                    continue
                if t == "phone":
                    b["phones"].append(v)
                elif t == "email":
                    b["emails"].append(v)
                elif t == "website":
                    if not b["website"]:
                        b["website"] = v
                elif t == "instagram":
                    b["instagram"] = v.rstrip("/").split("/")[-1].lstrip("@")
                elif t in ("vk", "facebook", "telegram", "whatsapp", "youtube"):
                    b["other_socials"][t] = v
        # Category
        rubrics = item.get("rubrics", []) or []
        if rubrics:
            b["category"] = rubrics[0].get("name", "")
        b["raw"] = item
        return b


# =====================================================================
# Factory
# =====================================================================

def get_data_source(env: dict) -> "DataSource":
    twogis_key = env.get("TWOGIS_API_KEY", "").strip()
    apify_key = env.get("APIFY_API_KEY", "").strip()
    if twogis_key:
        return Direct2GisDataSource(twogis_key, apify_token=apify_key or None)
    if apify_key:
        return ApifyDataSource(apify_key)
    raise RuntimeError(
        "No 2GIS data source configured. Set APIFY_API_KEY or TWOGIS_API_KEY in .env.local."
    )

"""Niche slug → 2GIS search query strings (Russian).

Each niche maps to a list of query terms — the data source can use any/all of
them and merge results. Multiple terms expand recall (e.g. "парикмахерская"
misses places listed only as "барбершоп").
"""

NICHES = {
    "hair_beauty": {
        "name_ru": "Парикмахерские / салоны красоты",
        "queries": [
            "парикмахерская",
            "салон красоты",
            "барбершоп",
            "beauty salon",
        ],
        "tam_almaty": 2945,
        "tam_bishkek": 1538,
    },
    "nail_cosmetic": {
        "name_ru": "Маникюр / косметология",
        "queries": [
            "маникюр",
            "ногтевая студия",
            "косметология",
            "nail studio",
        ],
        "tam_almaty": 784,
        "tam_bishkek": 1415,
    },
    "fitness": {
        "name_ru": "Фитнес / тренажёрные залы",
        "queries": [
            "фитнес-клуб",
            "тренажёрный зал",
            "фитнес центр",
        ],
        "tam_almaty": 788,
        "tam_bishkek": 164,
    },
    "travel": {
        "name_ru": "Турфирмы / турагентства",
        "queries": [
            "турагентство",
            "туристическое агентство",
            "турфирма",
        ],
        "tam_almaty": 765,
        "tam_bishkek": 429,
    },
}


CITIES = {
    "almaty": {
        "name_ru": "Алматы",
        "country": "KZ",
        # 2GIS region/city ID for Almaty (used by direct API; Apify resolves by name).
        "twogis_city_id": "9430000000020902",
    },
    "bishkek": {
        "name_ru": "Бишкек",
        "country": "KG",
        # 2GIS region/city ID for Bishkek.
        "twogis_city_id": "15763234351708425",
    },
}


def resolve_niche(slug_or_query: str):
    """Return (slug, queries, name_ru) — or treat as free-form query."""
    if slug_or_query in NICHES:
        n = NICHES[slug_or_query]
        return slug_or_query, n["queries"], n["name_ru"]
    # Free-form: use as single query string.
    return f"custom:{slug_or_query}", [slug_or_query], slug_or_query


def resolve_city(city_input: str):
    """Return (slug, name_ru, country) for almaty/bishkek or by Russian name."""
    s = city_input.strip().lower()
    if s in CITIES:
        c = CITIES[s]
        return s, c["name_ru"], c["country"]
    # Match by Russian name
    for slug, c in CITIES.items():
        if c["name_ru"].lower() == s:
            return slug, c["name_ru"], c["country"]
    raise ValueError(f"Unknown city: {city_input!r}. Supported: almaty, bishkek.")


# =====================================================================
# Company size thresholds — used by run.py to filter out micro and large
# businesses early in the pipeline.
#
# Our sweet spot is **4-10 employees**: enough budget for a $300-400 site,
# no in-house marketing team that would build it themselves, and a real
# DM-overload problem to solve.
#
# Proxy signals from 2GIS:
#   review_count  — #reviews on the business card
#   rating_count  — #ratings (similar magnitude as reviews)
#   branch_count  — number of branches under the same brand (1 = single shop)
#
# Defaults below were calibrated against a real 80-lead Almaty hair-beauty
# run on 2026-05-12 — 70% of leads landed in "sweet_spot" with these limits,
# matching field intuition.
# =====================================================================

DEFAULT_SIZE_THRESHOLDS = {
    "min_reviews": 10,        # below this = micro (1-3 person operation)
    "max_reviews": 300,       # above this = large established business
    "max_rating_count": 600,  # secondary cap (high rating_count + low reviews is suspicious)
    "max_branches": 3,        # 4+ branches = network/chain, not our target
}

# Per-niche overrides. Keep empty by default — only override if a niche
# is structurally different (e.g. travel agencies have far fewer reviews
# than hair salons on average).
NICHE_SIZE_OVERRIDES = {
    # "travel": {"min_reviews": 5, "max_reviews": 200, ...}  # placeholder
}


def thresholds_for_niche(niche_slug: str) -> dict:
    """Return effective size thresholds for a niche (default + overrides)."""
    t = dict(DEFAULT_SIZE_THRESHOLDS)
    override = NICHE_SIZE_OVERRIDES.get(niche_slug, {})
    t.update(override)
    return t


def estimate_size(business: dict, thresholds: dict = None) -> str:
    """Classify a business by proxy size signals.

    Returns one of: 'micro', 'sweet_spot', 'large', 'large_chain', 'unknown'.

    Use this both in the live filter (to skip non-sweet-spot leads) and in
    the post-hoc Sheets labelling for reviewer transparency.
    """
    t = thresholds or DEFAULT_SIZE_THRESHOLDS
    rc = business.get("review_count") or 0
    rcount = business.get("rating_count") or 0
    branches = business.get("branch_count") or 1

    if branches > t["max_branches"]:
        return "large_chain"
    if rc > t["max_reviews"] or rcount > t["max_rating_count"]:
        return "large"
    if rc < t["min_reviews"] and rcount < t["min_reviews"] * 2:
        return "micro"
    if t["min_reviews"] <= rc <= t["max_reviews"]:
        return "sweet_spot"
    return "unknown"

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

"""Owner-vs-employee confidence scoring (Tier 1 implementation).

Earlier research established that no single API can answer "is this phone
the owner's personal line, or an employee's?" reliably for KZ/KG SMBs.
Instead we score each lead by **adding multiple weak signals**:

Tier 1 (this module — free, from data we already have):
  • cross_card_frequency   — phone appears on N 2GIS cards in our DB
                              (1 → likely single-owner; 3+ → likely agent)
  • serper_role_hint       — Serper search "{phone} {city}" finds words
                              like «директор» / «ресепшн» near the number
                              in classifieds / OLX / Lalafo / 2GIS reviews

Future tiers (deferred):
  • Tier 2: Maytapi WhatsApp isBusiness ($0.25/100), Egov.kz director match
  • Tier 3: Kompra.kz paid director DB
  • Tier 4: GetContact unofficial (gray ToS)

Output bucketing:
  total_score ≥ +3   →  "high"     (probably personal owner)
  +1 ≤ score < +3   →  "medium"   (some positive signal, unverified)
  -1 ≤ score < +1   →  "unknown"  (no strong signal either way)
  score < -1         →  "low"      (likely admin / agent / employee)
"""

import json
import re
import sys
import urllib.request
from typing import Optional


SERPER_URL = "https://google.serper.dev/search"

# Words near a phone in search results that hint role.
ROLE_HINT_OWNER = re.compile(
    r"\b(директор|владел[еа]ц|владелица|основатель|основательница|founder|owner|"
    r"учредитель|со-основатель|со-учредитель|руководитель|собственник)\b",
    re.IGNORECASE,
)
ROLE_HINT_ADMIN = re.compile(
    r"\b(ресепшн|приёмная|приемная|администратор|секретарь|оператор|"
    r"менеджер по продажам|колл-центр|справочная|информация)\b",
    re.IGNORECASE,
)
ROLE_HINT_AGENT = re.compile(
    r"\b(агент|риелтор|посредник|услуги|объявление|olx|lalafo|avito)\b",
    re.IGNORECASE,
)


def _serper_search(query: str, api_key: str, num: int = 10) -> dict:
    body = json.dumps({"q": query, "num": num, "gl": "kz", "hl": "ru"}).encode()
    req = urllib.request.Request(
        SERPER_URL, data=body, method="POST",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[serper] confidence query failed ({query!r}): {e}", file=sys.stderr)
        return {}


def score_cross_card_frequency(card_count: int) -> tuple:
    """Cross-card frequency component. Returns (score, label_explanation)."""
    if card_count <= 0:
        # No phone in DB / empty phone — neutral (no signal either way)
        return 0, "no phone in DB"
    if card_count == 1:
        return 2, "phone unique to 1 card"
    if card_count == 2:
        return 0, "phone on 2 cards (ambiguous)"
    return -3, f"phone on {card_count}+ cards (likely agent/broker)"


def score_serper_role(phone: str, business_name: str, city_ru: str,
                     serper_key: str) -> tuple:
    """Look up phone + business + city in classifieds. Score by role hints."""
    if not serper_key or not phone:
        return 0, "serper not available"

    # Format variants — same phone written differently across sites
    digits = re.sub(r"\D", "", phone)
    variants = [phone]
    if digits.startswith("7") and len(digits) == 11:
        # KZ: +7 701 234 56 78  /  8 701 234 56 78  /  87012345678
        variants.append(f"8 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}")
        variants.append(f"+7 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}")
    elif digits.startswith("996") and len(digits) == 12:
        variants.append(f"0 {digits[3:6]} {digits[6:8]} {digits[8:10]} {digits[10:12]}")
        variants.append(f"+996 {digits[3:6]} {digits[6:8]} {digits[8:10]} {digits[10:12]}")

    # One Serper query — wide net. Each variant in OR.
    qb = " OR ".join(f'"{v}"' for v in variants[:3])
    query = f'({qb}) {business_name[:30]}'
    res = _serper_search(query, serper_key, num=10)
    if not res:
        return 0, "serper returned nothing"

    blob = ""
    for r in res.get("organic", []) or []:
        blob += " " + (r.get("title") or "") + " " + (r.get("snippet") or "")
    blob_lc = blob.lower()

    owner_hit = ROLE_HINT_OWNER.search(blob_lc)
    admin_hit = ROLE_HINT_ADMIN.search(blob_lc)
    agent_hit = ROLE_HINT_AGENT.search(blob_lc)

    # Score: heavy minus for explicit admin/agent label, plus for owner-near-phone
    score = 0
    label_parts = []
    if owner_hit:
        score += 2
        label_parts.append(f"owner-keyword '{owner_hit.group(1)}' near phone")
    if admin_hit:
        score -= 3
        label_parts.append(f"admin-keyword '{admin_hit.group(1)}' near phone")
    if agent_hit:
        score -= 1
        label_parts.append(f"agent context '{agent_hit.group(1)}'")
    if not (owner_hit or admin_hit or agent_hit):
        return 0, "no role keywords in serper results"
    return score, "; ".join(label_parts)


def bucket(score: int) -> str:
    """Convert numeric score into a human-readable bucket."""
    if score >= 3:
        return "high"
    if score >= 1:
        return "medium"
    if score >= -1:
        return "unknown"
    return "low"


def compute_owner_confidence(
    lead: dict,
    phone_freq_map: dict,
    serper_key: str = "",
    do_serper: bool = True,
) -> dict:
    """Compute the Tier-1 owner-confidence score for one lead.

    Inputs:
      lead              — lead dict with phone, business_name, city_ru
      phone_freq_map    — pre-computed {phone: occurrence_count} from
                          dedup_db.phones_with_frequency() or equivalent.
                          Passed in so worker threads don't hit SQLite
                          (sqlite3 connections aren't thread-safe).
      serper_key        — Serper.dev API key
      do_serper         — disable for offline / dry-run mode

    Returns:
      {
        "score":      int,
        "bucket":     "high" | "medium" | "unknown" | "low",
        "signals":    [list of (signal_name, signal_score, explanation)],
        "card_count": int,
      }
    """
    phone = (lead.get("phone") or "").strip()
    signals = []
    total = 0

    # Signal 1 — cross-card frequency (from pre-loaded map, no DB hit)
    card_count = phone_freq_map.get(phone, 1) if phone else 0
    s, why = score_cross_card_frequency(card_count)
    signals.append(("cross_card_freq", s, why))
    total += s

    # Signal 2 — Serper role hint
    if do_serper:
        s, why = score_serper_role(
            phone,
            lead.get("business_name", ""),
            lead.get("city_ru", ""),
            serper_key,
        )
        signals.append(("serper_role", s, why))
        total += s
    else:
        signals.append(("serper_role", 0, "skipped (do_serper=False)"))

    return {
        "score": total,
        "bucket": bucket(total),
        "card_count": card_count,
        "signals": signals,
    }

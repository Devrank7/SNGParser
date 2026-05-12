"""Classify Kazakh and Kyrgyz phone numbers as mobile / landline / unknown.

Mobile prefix tables are authoritative as of 2026 per the public KZ/KG number
plans. Mobiles in both countries are 7-digit subscriber numbers, so we match
the first 3 digits after the country code.

Functions accept any input — international (+7 727 ...), national (8 727 ...),
hyphenated, parenthesized — and normalize to E.164 before classifying.
"""

import re
from typing import Optional

# Kazakhstan: country code +7, mobile area codes (the XXX in 7XXX).
# Source: ITU TSB Operational Bulletin + Kazakhstan numbering plan (2024-2025).
#
# IMPORTANT: 750, 751 and 760-764 are NOT mobile despite the 7xx prefix.
#   750-751: dial-up / VoIP access codes (no GSM)
#   760-764: Kulan satellite + commercial IP networks (763 = Arna)
# Sending WhatsApp to those numbers is wasted bandwidth.
KZ_MOBILE_PREFIXES = {
    "700", "701", "702", "703", "704", "705", "706", "707", "708",  # Beeline/Kcell/Tele2/Activ shared pool
    "747",                                                          # Tele2 (was Altel)
    "771", "772", "773", "774", "775", "776", "777", "778",         # Activ/Kcell/Beeline shared pool
}

# Kyrgyzstan: country code +996, mobile area codes.
# Source: ITU + State Communications Agency (Kyrgyz Ministry of Digital Development).
KG_MOBILE_PREFIXES = {
    "220", "221", "222", "223", "224", "225", "226", "227", "228", "229",  # Sky Mobile / Beeline KG
    "500", "501", "502", "505", "507", "509",                              # Nur Telecom (O!)
    "550", "551", "552", "553", "554", "555", "556", "557", "558", "559",  # Alfa Telecom (MegaCom)
    "700", "701", "702", "703", "704", "705", "706", "707", "708", "709",  # Nur Telecom (O!)
    "770", "771", "772", "773", "774", "775", "776", "777", "778", "779",  # Sky Mobile / MegaCom
    "990", "996", "997", "998", "999",                                     # Alfa Telecom (MegaCom)
}


def normalize(phone: str) -> str:
    """Strip everything to digits and try to produce an E.164-ish string."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    # KZ "8 XXX..." → "+7 XXX..."
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    # KG with leading 0 (rare) → strip
    if digits.startswith("0") and len(digits) == 10:
        digits = "996" + digits[1:]
    return "+" + digits


def classify(phone: str) -> dict:
    """Return {'normalized': str, 'type': 'mobile'|'landline'|'unknown', 'country': 'KZ'|'KG'|None}."""
    norm = normalize(phone)
    if not norm:
        return {"normalized": "", "type": "unknown", "country": None}

    digits = norm.lstrip("+")
    # KZ: +7 + 10 digits = 11 total. Mobile if first 3 of subscriber part is in table.
    if digits.startswith("7") and len(digits) == 11:
        subscriber_prefix = digits[1:4]  # the XXX in 7XXX
        if subscriber_prefix in KZ_MOBILE_PREFIXES:
            return {"normalized": norm, "type": "mobile", "country": "KZ"}
        return {"normalized": norm, "type": "landline", "country": "KZ"}

    # KG: +996 + 9 digits = 12 total.
    if digits.startswith("996") and len(digits) == 12:
        subscriber_prefix = digits[3:6]
        if subscriber_prefix in KG_MOBILE_PREFIXES:
            return {"normalized": norm, "type": "mobile", "country": "KG"}
        return {"normalized": norm, "type": "landline", "country": "KG"}

    return {"normalized": norm, "type": "unknown", "country": None}


def pick_best_mobile(phones: list) -> Optional[dict]:
    """Given a list of raw phone strings, return the first one classified as mobile."""
    for p in phones:
        c = classify(p)
        if c["type"] == "mobile":
            return c
    return None


if __name__ == "__main__":
    # quick sanity
    tests = [
        "+7 727 222 3344",  # Almaty landline
        "8 701 555 1234",   # KZ mobile (Beeline)
        "+996 312 900 100", # Bishkek landline
        "+996 555 123 456", # KG mobile (O!)
        "0555 12 34 56",    # KG mobile leading zero
        "garbage",
    ]
    for t in tests:
        print(f"{t!r:30s} → {classify(t)}")

"""Build the user-prompt context block for one lead.

Pure function — takes a lead dict (from sheets_io.read_leads_without_message)
and turns it into the structured Russian text the LLM expects in user prompt.
No I/O, no API calls.
"""

from typing import Optional


CHANNEL_FOR_TIER = {
    "phone": "WhatsApp",
    "owner_ig": "Instagram DM",
    "company_ig": "Instagram DM",
}


def channel_for(lead: dict) -> str:
    return CHANNEL_FOR_TIER.get(lead.get("contact_method", ""), "WhatsApp")


def _city_country(city: str) -> str:
    c = (city or "").strip().lower()
    if "алмат" in c:
        return "Алматы, Казахстан"
    if "бишкек" in c:
        return "Бишкек, Кыргызстан"
    return city or ""


def build_user_prompt(lead: dict) -> str:
    """Format the lead into the Russian context block the system prompt expects."""
    lines = ["Данные лида:"]
    lines.append(f"  Канал: {channel_for(lead)}")
    lines.append(f"  Название бизнеса: {lead.get('business_name', '')}")

    city_full = _city_country(lead.get("city", ""))
    if city_full:
        lines.append(f"  Город: {city_full}")
    if lead.get("address"):
        lines.append(f"  Адрес: {lead['address']}")
    if lead.get("niche"):
        lines.append(f"  Ниша: {lead['niche']}")

    if lead.get("owner_name"):
        lines.append(f"  Имя владельца: {lead['owner_name']}")
    else:
        lines.append("  Имя владельца: НЕ ИЗВЕСТНО — обращайся к бизнесу, не к человеку.")

    if lead.get("contact_method") == "phone":
        lines.append(f"  Tier: phone (есть мобильный — пиши тёплый WhatsApp message)")
        lines.append(f"  Телефон: {lead.get('phone', '')}")
    elif lead.get("contact_method") == "owner_ig":
        lines.append("  Tier: owner_ig (Serper нашёл предположительно личный IG владельца — это handle мы используем)")
        if lead.get("owner_instagram"):
            lines.append(f"  Личный IG владельца: @{lead['owner_instagram']}")
        if lead.get("owner_ig_source"):
            lines.append(f"  Источник handle'а: {lead['owner_ig_source']} (помни — handle может быть угадан, тон должен быть как при первом касании, чтобы не выдать что это автомат)")
    elif lead.get("contact_method") == "company_ig":
        lines.append("  Tier: company_ig (личного IG не нашли — пиши в IG-аккаунт компании, обращайся к бизнесу)")

    if lead.get("company_instagram"):
        lines.append(f"  IG компании: @{lead['company_instagram']}")

    if lead.get("twogis_url"):
        lines.append(f"  Ссылка 2ГИС: {lead['twogis_url']}")

    lines.append("")
    lines.append("Напиши гиперперсонализированное сообщение по 5-частной структуре. Верни ТОЛЬКО JSON.")
    return "\n".join(lines)

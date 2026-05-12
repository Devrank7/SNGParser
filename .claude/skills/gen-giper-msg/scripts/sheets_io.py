"""Google Sheets I/O for gen-giper-msg.

Reads leads from any sheet that has 2gis-lead-gen-style columns (fuzzy-matched
by name, not position, so column order and minor renames don't break us) and
writes generated messages back into 4 target columns appended to the end if
they don't already exist.

Required source columns (fuzzy-matched, case-insensitive):
  - Business Name
  - Address
  - Contact Method     (values: phone | owner_ig | company_ig)
  - Phone
  - Owner Name
  - Owner Instagram
  - Company Instagram
  - City
  - Niche

Target columns we append if missing:
  - Initial Message
  - Channel            (WhatsApp | Instagram DM)
  - Message Status     (draft | approved | rejected | validation_failed)
  - Reviewed By
"""

import re
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parent.parent.parent / "_shared"
sys.path.insert(0, str(SHARED.parent))
from _shared.sheets import get_sheets_service, get_sheet_title  # type: ignore


# Fuzzy patterns for source columns. Each canonical key maps to header
# strings we'll accept (lowercased compare).
SOURCE_COLUMN_PATTERNS = {
    "business_name": ["business name", "название бизнеса", "название", "name"],
    "address": ["address", "адрес"],
    "contact_method": ["contact method", "метод контакта", "канал"],
    "phone": ["phone", "телефон"],
    "phone_type": ["phone type", "тип телефона"],
    "owner_name": ["owner name", "имя владельца", "владелец"],
    "owner_instagram": ["owner instagram", "instagram владельца", "ig владельца"],
    "owner_ig_source": ["owner ig source", "источник ig владельца"],
    "company_instagram": ["company instagram", "instagram компании", "ig компании"],
    "city": ["city", "город"],
    "niche": ["niche", "ниша"],
    "twogis_url": ["2gis url", "2gis", "url"],
}

TARGET_COLUMNS = ["Initial Message", "Channel", "Message Status", "Reviewed By"]

SHEET_URL_RE = re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)")


def parse_sheet_id(url_or_id: str) -> str:
    if not url_or_id:
        return ""
    m = SHEET_URL_RE.search(url_or_id)
    return m.group(1) if m else url_or_id.strip()


def _col_letter(idx: int) -> str:
    """0-indexed column number → A1 letter."""
    out = ""
    n = idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _build_header_index(headers: list) -> dict:
    """Return {canonical_key: col_idx} for source columns, fuzzy-matched."""
    idx = {}
    for ci, h in enumerate(headers):
        if not h:
            continue
        h_low = h.strip().lower()
        for key, patterns in SOURCE_COLUMN_PATTERNS.items():
            if key in idx:
                continue
            if any(p in h_low or h_low in p for p in patterns):
                idx[key] = ci
                break
    return idx


def _target_column_index(headers: list) -> dict:
    """Return {target_col_name: col_idx} for any target columns already present."""
    out = {}
    for ci, h in enumerate(headers):
        if h in TARGET_COLUMNS:
            out[h] = ci
    return out


def validate_and_prepare(sheet_id: str) -> dict:
    """Verify access, scan headers, append missing target columns. Idempotent."""
    svc = get_sheets_service()
    try:
        tab = get_sheet_title(svc, sheet_id)
    except Exception as e:
        return {"ok": False, "error": f"Cannot read sheet: {e}"}

    # Pull header row.
    res = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1:Z1"
    ).execute()
    headers = (res.get("values") or [[]])[0]

    if not headers:
        return {"ok": False, "error": "Sheet has no header row — please run 2gis-lead-gen first or fill headers manually."}

    src_idx = _build_header_index(headers)
    required = {"business_name", "contact_method"}
    missing = sorted(required - set(src_idx.keys()))
    if missing:
        return {
            "ok": False,
            "error": f"Required source columns missing: {missing}",
            "headers_found": headers,
        }

    # Add target columns at the end if not present.
    tgt_idx = _target_column_index(headers)
    new_headers = headers[:]
    added = []
    for col in TARGET_COLUMNS:
        if col not in tgt_idx:
            new_headers.append(col)
            tgt_idx[col] = len(new_headers) - 1
            added.append(col)
    if added:
        last_col = _col_letter(len(new_headers) - 1)
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1:{last_col}1",
            valueInputOption="RAW",
            body={"values": [new_headers]},
        ).execute()

    # Count leads with/without messages.
    last_col_letter = _col_letter(max(tgt_idx.values()))
    data_res = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A2:{last_col_letter}",
    ).execute()
    rows = data_res.get("values", [])

    msg_col = tgt_idx["Initial Message"]
    with_msg = sum(1 for r in rows if len(r) > msg_col and (r[msg_col] or "").strip())
    total = sum(1 for r in rows if any(c for c in r))

    return {
        "ok": True,
        "tab": tab,
        "total_leads": total,
        "leads_with_message_already": with_msg,
        "leads_to_process": total - with_msg,
        "added_target_columns": added,
        "source_columns_mapped": src_idx,
        "target_columns": tgt_idx,
    }


def read_leads_without_message(sheet_id: str, limit: int = None,
                                tier_filter: str = "all") -> list:
    """Return [{row_number, lead_dict, sheet_indexes}, ...] for leads needing a message.

    tier_filter: 'all' | 'phone' | 'ig'  (ig matches both owner_ig and company_ig).
    """
    svc = get_sheets_service()
    tab = get_sheet_title(svc, sheet_id)
    res = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1:ZZ"
    ).execute()
    rows = res.get("values", [])
    if not rows:
        return []
    headers = rows[0]
    src_idx = _build_header_index(headers)
    tgt_idx = _target_column_index(headers)
    msg_col = tgt_idx.get("Initial Message")

    out = []
    for i, row in enumerate(rows[1:], start=2):  # row 2 = first data row
        if not any(c for c in row):
            continue
        # Skip rows that already have a message.
        if msg_col is not None and len(row) > msg_col and (row[msg_col] or "").strip():
            continue

        def get(key):
            ci = src_idx.get(key)
            if ci is None or ci >= len(row):
                return ""
            return (row[ci] or "").strip()

        contact_method = get("contact_method").lower()
        if tier_filter == "phone" and contact_method != "phone":
            continue
        if tier_filter == "ig" and contact_method not in ("owner_ig", "company_ig"):
            continue

        lead = {
            "row_number": i,
            "business_name": get("business_name"),
            "address": get("address"),
            "city": get("city"),
            "niche": get("niche"),
            "contact_method": contact_method,
            "phone": get("phone"),
            "phone_type": get("phone_type"),
            "owner_name": get("owner_name"),
            "owner_instagram": get("owner_instagram").lstrip("@"),
            "owner_ig_source": get("owner_ig_source"),
            "company_instagram": get("company_instagram").lstrip("@"),
            "twogis_url": get("twogis_url"),
        }
        out.append(lead)

    if limit:
        out = out[:limit]
    return out


def write_message(sheet_id: str, row_number: int, message: str,
                  channel: str, status: str = "draft") -> None:
    """Update Initial Message / Channel / Message Status for a specific row."""
    svc = get_sheets_service()
    tab = get_sheet_title(svc, sheet_id)
    # Re-read headers to find the target column letters.
    h_res = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1:ZZ1"
    ).execute()
    headers = (h_res.get("values") or [[]])[0]
    tgt_idx = _target_column_index(headers)
    if "Initial Message" not in tgt_idx:
        raise RuntimeError("Sheet does not have 'Initial Message' column. Run validate first.")

    updates = []
    for col_name, value in (
        ("Initial Message", message),
        ("Channel", channel),
        ("Message Status", status),
    ):
        ci = tgt_idx.get(col_name)
        if ci is None:
            continue
        cell = f"'{tab}'!{_col_letter(ci)}{row_number}"
        updates.append({"range": cell, "values": [[value]]})

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()

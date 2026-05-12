"""Append lead rows to a Google Sheets via the shared service-account.

We follow the demo-sender convention: read the existing header row, match
our canonical column names to whatever the sheet has (with light fuzzy
matching), and append new rows in the order the sheet expects. If the sheet
is completely empty, we write our default header row first.
"""

import sys
import re
from pathlib import Path

# Add _shared to path so we can reuse the service account loader.
SHARED = Path(__file__).resolve().parent.parent.parent / "_shared"
sys.path.insert(0, str(SHARED.parent))  # parent of _shared so `from _shared.sheets import` works
from _shared.sheets import get_sheets_service, get_sheet_title  # type: ignore


DEFAULT_HEADERS = [
    "Discovered At",
    "City",
    "Niche",
    "Business Name",
    "Address",
    "Contact Method",
    "Phone",
    "Phone Type",
    "Owner Name",
    "Owner Instagram",
    "Owner IG Source",
    "Company Instagram",
    "2GIS URL",
    "Data Source",
]


SHEET_URL_RE = re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)")


def parse_sheet_id(url_or_id: str) -> str:
    if not url_or_id:
        return ""
    m = SHEET_URL_RE.search(url_or_id)
    if m:
        return m.group(1)
    return url_or_id.strip()


def validate_sheet(sheet_id: str) -> dict:
    """Check that we can read/write the sheet. Return diagnostic info."""
    try:
        svc = get_sheets_service()
        title = get_sheet_title(svc, sheet_id)
    except SystemExit:
        # _shared.sheets calls sys.exit on auth errors; re-raise as exception.
        return {"ok": False, "error": "Service-account access denied. Share the sheet with "
                                       "aisheets@aisheets-486216.iam.gserviceaccount.com (Editor)."}
    except Exception as e:
        return {"ok": False, "error": f"Sheet validation failed: {e}"}

    # Read existing headers (row 1).
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{title}'!A1:Z1",
        ).execute()
        existing = (result.get("values") or [[]])[0]
    except Exception as e:
        return {"ok": False, "error": f"Failed to read header row: {e}"}

    if not existing:
        return {"ok": True, "tab": title, "headers": [], "needs_header_write": True}
    # Compare to default — allow length to differ but report mismatch.
    return {
        "ok": True,
        "tab": title,
        "headers": existing,
        "needs_header_write": False,
        "matches_default": existing[: len(DEFAULT_HEADERS)] == DEFAULT_HEADERS,
    }


def _write_headers(svc, sheet_id: str, tab: str):
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="RAW",
        body={"values": [DEFAULT_HEADERS]},
    ).execute()


def append_leads(sheet_id: str, leads: list) -> dict:
    """Append leads, writing the header row first if the sheet is empty.

    Each lead dict should have the keys defined in run.py's lead schema.
    Returns {"appended": N, "first_row": int, "tab": str}.
    """
    if not leads:
        return {"appended": 0, "first_row": None, "tab": None}

    svc = get_sheets_service()
    tab = get_sheet_title(svc, sheet_id)

    # Header bootstrap if empty.
    existing = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A1:Z1",
    ).execute().get("values") or []
    if not existing:
        _write_headers(svc, sheet_id, tab)

    rows = [[
        lead.get("discovered_at", ""),
        lead.get("city_ru", ""),
        lead.get("niche_ru", ""),
        lead.get("name", ""),
        lead.get("address", ""),
        lead.get("contact_method", ""),
        lead.get("phone", ""),
        lead.get("phone_type", ""),
        lead.get("owner_name", ""),
        f"@{lead['owner_instagram']}" if lead.get("owner_instagram") else "",
        lead.get("owner_ig_source", ""),
        f"@{lead['company_instagram']}" if lead.get("company_instagram") else "",
        lead.get("twogis_url", ""),
        lead.get("data_source", ""),
    ] for lead in leads]

    res = svc.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

    updated_range = res.get("updates", {}).get("updatedRange", "")
    # updatedRange looks like "'Sheet1'!A24:M28" — pull the first row number.
    first_row = None
    m = re.search(r"![A-Z]+(\d+):", updated_range)
    if m:
        first_row = int(m.group(1))
    return {"appended": len(rows), "first_row": first_row, "tab": tab,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/"}

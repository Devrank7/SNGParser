"""Shared Google Sheets utilities: API access, column matching, cell updates."""

import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .config import SERVICE_ACCOUNT_FILE

# Column name patterns (case-insensitive fuzzy match).
# Each key maps to a list of possible header strings.
COLUMN_PATTERNS = {
    # --- Email skill columns ---
    "start_message": [
        "start message", "start_message", "startmessage",
        "message", "initial message", "first message",
        "стартовое сообщение", "сообщение", "текст",
        "body", "email body", "email text", "content",
    ],
    "email": [
        "email", "e-mail", "email address", "emailaddress",
        "почта", "емейл", "мейл", "recipient",
    ],
    "demo": [
        "demo", "demo link", "demo url", "demo_link", "demo_url",
        "демо", "ссылка на демо", "демо ссылка", "preview", "preview link",
    ],
    "written": [
        "written", "sent", "отправлено", "статус", "status",
        "done", "completed",
    ],
    # --- Instagram skill columns ---
    "instagram": [
        "instagram", "ig", "инстаграм", "инста",
        "instagram link", "ig link", "instagram username",
        "ig username", "аккаунт инстаграм", "instagram profile",
    ],
    "system_prompt": [
        "system prompt", "system_prompt", "systemprompt",
        "промпт", "системный промпт", "prompt",
        "ai prompt", "bot prompt", "контекст",
    ],
    "video": [
        "video", "видео", "video path", "video file",
        "demo video", "демо видео",
    ],
    # --- Personalization columns ---
    "business_name": [
        "business name", "business_name", "company name", "company_name",
        "salon name", "salon_name", "clinic name", "clinic_name",
        "название бизнеса", "название салона", "название компании",
    ],
    "owner_name": [
        "owner", "owner name", "owner_name", "contact name", "contact_name",
        "имя владельца", "владелец", "контактное лицо",
    ],
    "website": [
        "website", "site url", "web site", "web url",
        "сайт", "веб-сайт",
    ],
    "city": [
        "city", "город", "location", "локация",
    ],
    "phone": [
        "phone", "phone number", "телефон", "номер телефона",
    ],
    "niche": [
        "niche", "industry", "ниша", "индустрия", "тип бизнеса",
    ],
    "emulator_link": [
        "emulator link", "emulator_link", "emulatorlink",
        "ссылка на эмулятор", "эмулятор", "emulator url",
        "emulator", "demo emulator",
    ],
    # --- WhatsApp outreach columns ---
    "whatsapp_demo": [
        "whatsapp demo", "whatsapp_demo", "wa demo", "wa_demo",
        "ватсап демо", "whatsapp demo link",
    ],
    "wa_sent": [
        "wa sent", "wa_sent", "whatsapp sent", "wa status",
        "отправлено wa", "wa done",
    ],
    # --- WhatsApp message generation columns ---
    "company_info": [
        "company info", "company_info", "companyinfo",
        "lead info", "lead_info", "prospect info", "prospect_info",
        "описание компании", "информация", "info", "about",
    ],
    "follow_up_message": [
        "follow up", "follow_up", "followup",
        "follow up message", "follow_up_message",
        "второе сообщение", "фоллоуап",
    ],
    "language": [
        "language", "lang", "язык", "idioma",
    ],
    "pain_point": [
        "pain point", "pain_point", "painpoint",
        "боль", "проблема", "pain",
    ],
}


def get_sheets_service():
    """Create Google Sheets API service using service account."""
    if not SERVICE_ACCOUNT_FILE.exists():
        print(f"ERROR: Service account file not found at {SERVICE_ACCOUNT_FILE}")
        sys.exit(1)

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = service_account.Credentials.from_service_account_file(
        str(SERVICE_ACCOUNT_FILE), scopes=scopes
    )
    return build("sheets", "v4", credentials=credentials)


def read_sheet(service, spreadsheet_id: str, range_name: str = "A1:Z") -> list:
    """Read all data from the first sheet."""
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_title = spreadsheet["sheets"][0]["properties"]["title"]
        full_range = f"'{sheet_title}'!{range_name}"

        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=full_range)
            .execute()
        )
        return result.get("values", [])
    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg or "not found" in error_msg.lower():
            print(
                "ERROR: Cannot access spreadsheet. Share it with: "
                "aisheets@aisheets-486216.iam.gserviceaccount.com"
            )
        else:
            print(f"ERROR: Failed to read sheet: {e}")
        sys.exit(1)


def get_sheet_title(service, spreadsheet_id: str) -> str:
    """Get the title of the first sheet in a spreadsheet."""
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return spreadsheet["sheets"][0]["properties"]["title"]


def match_column(header: str, pattern_key: str) -> bool:
    """Check if a column header matches a pattern key.

    Uses substring matching: the pattern must appear inside the header.
    Short headers (<=3 chars) require an exact match to avoid false positives.
    """
    header_lower = header.lower().strip()
    if not header_lower:
        return False
    for pattern in COLUMN_PATTERNS[pattern_key]:
        if header_lower == pattern:
            return True
        if len(header_lower) > 3 and pattern in header_lower:
            return True
    return False


def find_columns(headers: list) -> dict:
    """Find column indices from headers using fuzzy matching.

    Two-pass approach: exact matches first (higher priority), then substring.
    """
    columns = {}
    assigned_indices = set()

    # Pass 1: exact matches only
    for i, header in enumerate(headers):
        header_lower = header.lower().strip()
        if not header_lower:
            continue
        for key in COLUMN_PATTERNS:
            if key in columns:
                continue
            if header_lower in COLUMN_PATTERNS[key]:
                columns[key] = {"index": i, "name": header}
                assigned_indices.add(i)
                break

    # Pass 2: substring matches for remaining columns
    for i, header in enumerate(headers):
        if i in assigned_indices:
            continue
        for key in COLUMN_PATTERNS:
            if key in columns:
                continue
            if match_column(header, key):
                columns[key] = {"index": i, "name": header}
                assigned_indices.add(i)
                break

    return columns


def update_sheet_cell(
    service, spreadsheet_id: str, sheet_title: str,
    row_index: int, col_index: int, value: str,
):
    """Update a single cell in the sheet."""
    col_letter = _col_index_to_letter(col_index)
    cell_range = f"'{sheet_title}'!{col_letter}{row_index + 1}"
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=cell_range,
        valueInputOption="RAW",
        body={"values": [[value]]},
    ).execute()


def _col_index_to_letter(col_index: int) -> str:
    """Convert 0-based column index to spreadsheet letter (0->A, 25->Z, 26->AA, etc.)."""
    col_letter = ""
    temp = col_index
    while temp >= 0:
        col_letter = chr(65 + (temp % 26)) + col_letter
        temp = temp // 26 - 1
    return col_letter


def add_column_if_missing(
    service, spreadsheet_id: str, sheet_title: str,
    headers: list, column_name: str,
) -> int:
    """Add a column with the given name if it doesn't exist. Returns column index."""
    col_index = len(headers)
    col_letter = _col_index_to_letter(col_index)
    cell_range = f"'{sheet_title}'!{col_letter}1"
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=cell_range,
        valueInputOption="RAW",
        body={"values": [[column_name]]},
    ).execute()
    return col_index
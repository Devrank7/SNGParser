"""Slim _shared package for SNGParser skills (2gis-lead-gen, gen-giper-msg).

Only the modules these skills actually import are wired up here. We
intentionally do NOT re-export analytics / suppression / instagram / etc.
that live in the DemoSender copy of _shared — those are used by other
skills (demo-sender, ig-outreach) which are not part of this project.
"""

from .config import load_env, PROJECT_ROOT, ENV_FILE, SERVICE_ACCOUNT_FILE
from .sheets import get_sheets_service, read_sheet, get_sheet_title
from .telegram import send_telegram_report, send_telegram_text

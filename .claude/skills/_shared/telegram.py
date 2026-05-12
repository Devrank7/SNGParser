"""Shared Telegram reporting utility."""

import json
import ssl
import urllib.request
from collections import defaultdict
from datetime import datetime

# SSL context for macOS Python (certifi fallback)
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()
    _SSL_CONTEXT.check_hostname = False
    _SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _escape_html(text: str) -> str:
    """Escape special characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram_report(env_vars: dict, report: dict, title: str = "DemoSender"):
    """Send a formatted report to Telegram.

    Args:
        env_vars: Environment variables dict (needs TELEGRAM_BOT_TOKEN, TELEGRAM_REPORT_CHAT_ID).
        report: Dict with keys: sent, failed, remaining, by_mailbox (dict), errors (list),
                daily_limit_reached (bool). All keys are optional.
        title: Report title (e.g. "DemoSender", "IG Demo Video", "IG Outreach").
    """
    token = env_vars.get("TELEGRAM_BOT_TOKEN")
    chat_ids_str = env_vars.get("TELEGRAM_REPORT_CHAT_ID", "")

    if not token or not chat_ids_str:
        print("WARNING: Telegram not configured. Skipping report.")
        return

    chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    text = f"\U0001f4e7 <b>{_escape_html(title)} — Отчёт</b>\n\n\U0001f550 {timestamp}\n"
    text += f"\n\u2705 Отправлено: {report.get('sent', 0)}"
    text += f"\n\u274c Ошибки: {report.get('failed', 0)}"
    text += f"\n\u23f3 Осталось: {report.get('remaining', 0)}"

    by_source = report.get("by_mailbox", {})
    if by_source:
        text += "\n\n\U0001f4ec По источникам:"
        for source, count in by_source.items():
            text += f"\n  \u2022 {_escape_html(str(source))}: {count}"

    errors = report.get("errors", [])
    if errors:
        text += "\n\n\u26a0\ufe0f Ошибки:"
        for error in errors[:10]:
            text += f"\n  \u2022 {_escape_html(str(error))}"

    if report.get("daily_limit_reached"):
        text += "\n\n\U0001f512 Достигнут дневной лимит. Продолжение в следующей сессии."

    for chat_id in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10, context=_SSL_CONTEXT)
            print(f"  Telegram report sent to chat {chat_id}")
        except Exception as e:
            print(f"  WARNING: Failed to send Telegram report to {chat_id}: {e}")

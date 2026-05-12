"""Call the local `claude` CLI as a subprocess, parse JSON output, validate, retry.

We rely on the user's existing Claude Code OAuth (from their Max subscription) —
no API key required. `claude --print --output-format json --system-prompt ...`
runs a one-shot non-interactive completion.

Critical: CLAUDECODE env var MUST be unset in the subprocess, otherwise claude
refuses to launch nested-session children.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


SKILL_DIR = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT_PATH = SKILL_DIR / "templates" / "senior_closer_prompt.md"
EXAMPLES_PATH = SKILL_DIR / "templates" / "examples.md"


# ─── Stop-word blacklist (case-insensitive substring check) ───────────────────
STOP_PHRASES = [
    "уникальное предложение",
    "только сегодня",
    "ограниченное предложение",
    "лучшее качество",
    "лучшая цена",
    "наша компания",
    "наша команда",
    "не упустите возможность",
    "качественный сайт",
    "профессиональный сайт",
    "профессиональная команда",
    "индивидуальный подход",
    "комплексное решение",
    "повысим конверсию",
    "увеличим продажи",
    "хочу сотрудничать",
    "уважаемый клиент",
    "пишу вам, потому что",
]

REQUIRED_JSON_KEYS = ["hook", "observation", "problem", "value", "ask"]

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


# ─── Prompt assembly ──────────────────────────────────────────────────────────

def load_system_prompt() -> str:
    base = SYSTEM_PROMPT_PATH.read_text()
    examples = EXAMPLES_PATH.read_text() if EXAMPLES_PATH.exists() else ""
    if examples:
        return base + "\n\n---\n\n# Few-Shot Examples\n\n" + examples
    return base


# ─── Subprocess to claude CLI ─────────────────────────────────────────────────

def call_claude(user_prompt: str, system_prompt: str, model: str = "claude-sonnet-4-6",
                timeout_sec: int = 120) -> dict:
    """Invoke `claude --print` and return the parsed top-level JSON envelope."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # critical — disables the nested-session block

    cmd = [
        "claude", "--print",
        "--model", model,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--fallback-model", "claude-haiku-4-5",
    ]

    proc = subprocess.run(
        cmd,
        input=user_prompt,
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude output not JSON: {e}; stdout head: {proc.stdout[:200]}")
    return envelope


def extract_message_json(envelope: dict) -> dict:
    """Pull the model's text from the envelope and parse it as our 5-part JSON."""
    text = envelope.get("result", "") or ""
    # The model often wraps JSON in ```json ... ``` fences. Strip them.
    m = JSON_FENCE_RE.search(text)
    payload = m.group(1) if m else text.strip()
    return json.loads(payload)


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_message(parts: dict, lead: dict) -> Optional[str]:
    """Return None if valid, or a short reason string if invalid (used for retry feedback)."""
    # 1. Required keys present and non-empty.
    for k in REQUIRED_JSON_KEYS:
        if not parts.get(k, "").strip():
            return f"Поле '{k}' пустое — заполни все 5 частей."

    full = " ".join(parts[k].strip() for k in REQUIRED_JSON_KEYS)
    word_count = len(full.split())

    # 2. Length.
    if word_count < 40:
        return f"Слишком коротко ({word_count} слов). Цель — 70-90 слов."
    if word_count > 130:
        return f"Слишком длинно ({word_count} слов). Цель — 70-90 слов, сократи."

    # 3. Hook length.
    hook_words = len(parts["hook"].split())
    if hook_words > 18:
        return f"Hook слишком длинный ({hook_words} слов). Цель — 5-15 слов."

    # 4. Stop phrases.
    low = full.lower()
    for sp in STOP_PHRASES:
        if sp in low:
            return f"Использована запрещённая фраза: '{sp}'. Перепиши без неё."

    # 5. Business name should appear somewhere in the message (proves personalization).
    biz = (lead.get("business_name") or "").strip()
    if biz and biz.lower() not in low:
        # Allow partial match — sometimes the business name is multi-word and only part fits naturally.
        short = biz.split(",")[0].strip().lower()  # "Parizat nail studio, ногтевая студия" → "Parizat nail studio"
        if short and short not in low:
            return f"Не упомянуто название бизнеса ({biz!r}). Гиперперсонализация требует его в hook или observation."

    # 6. Owner name presence rules.
    owner = (lead.get("owner_name") or "").strip()
    if owner:
        # If we have a name, it MUST appear in the message somewhere (usually hook).
        first_name = owner.split()[0].lower()
        if first_name not in low:
            return f"Имя владельца '{owner}' не использовано. Если имя дано — оно должно быть в hook."
    else:
        # If we don't have a name, the message must NOT invent one. Hard to detect cleanly,
        # but flag if "Здравствуйте, [SomeName]" pattern appears.
        hook = parts["hook"]
        m = re.search(r"(?:Здравствуйте|Привет),?\s+([А-ЯЁ][а-яё]+)", hook)
        if m and m.group(1).lower() not in {"коллег", "коллеги"}:
            return f"Имя владельца НЕ дано в контексте, но в hook появилось '{m.group(1)}'. Не выдумывай имена."

    return None  # valid


def assemble_final(parts: dict) -> str:
    """Stitch the 5 parts into the message a human will read."""
    return (
        f"{parts['hook'].strip()}\n\n"
        f"{parts['observation'].strip()} {parts['problem'].strip()}\n\n"
        f"{parts['value'].strip()}\n\n"
        f"{parts['ask'].strip()}"
    )


# ─── Public entry point ──────────────────────────────────────────────────────

def generate_for_lead(lead: dict, user_prompt: str, model: str = "claude-sonnet-4-6",
                      max_attempts: int = 3) -> dict:
    """Run generate-validate-retry loop for a single lead.

    Returns {"ok": bool, "message": str, "parts": dict, "attempts": int,
             "channel": str, "validation_error": str | None,
             "envelope": dict, "duration_ms": int}
    """
    system_prompt = load_system_prompt()
    last_error = None
    last_envelope = None
    extended_user = user_prompt
    total_duration_ms = 0

    for attempt in range(1, max_attempts + 1):
        envelope = call_claude(extended_user, system_prompt, model=model)
        last_envelope = envelope
        total_duration_ms += int(envelope.get("duration_ms", 0))
        try:
            parts = extract_message_json(envelope)
        except Exception as e:
            last_error = f"Не удалось распарсить JSON ответа: {e}"
        else:
            err = validate_message(parts, lead)
            if not err:
                from personalize import channel_for
                return {
                    "ok": True,
                    "message": assemble_final(parts),
                    "parts": parts,
                    "attempts": attempt,
                    "channel": channel_for(lead),
                    "validation_error": None,
                    "envelope": envelope,
                    "duration_ms": total_duration_ms,
                }
            last_error = err

        # Append corrective feedback for the next attempt.
        extended_user = (
            user_prompt + "\n\n[Предыдущая попытка не прошла валидацию: " + last_error +
            "] Перепиши, исправив проблему. Верни ТОЛЬКО JSON."
        )
        print(f"[gen] attempt {attempt} failed: {last_error}", file=sys.stderr)

    from personalize import channel_for
    return {
        "ok": False,
        "message": "",
        "parts": {},
        "attempts": max_attempts,
        "channel": channel_for(lead),
        "validation_error": last_error,
        "envelope": last_envelope,
        "duration_ms": total_duration_ms,
    }

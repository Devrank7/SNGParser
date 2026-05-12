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
    # Corporate / sales-y buzzwords
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
    # Bridge phrases AI overuses
    "стоит отметить",
    "хочется отметить",
    "хочу обратить ваше внимание",
    "позвольте представить",
    # Marketing jargon (manager: "словарь маркетолога, не человека")
    "конверси",          # конверсия / конверсий / повышение конверсии
    "воронк",            # воронка / воронку
    "точки контакта",
    "целевая аудитория",
    "юзабилити",
    "оптимизаци",        # оптимизация / оптимизировать
    "повышение охвата",
    "инструмент роста",  # частая buzz-фраза в наших старых текстах
    # Diagnose phrases (manager: "тон врача и обвинение")
    "вы теряете",
    "теряете клиентов",
    "клиенты теряются",
    "клиентки теряются",
    "вы упускаете",
    "вы страдаете",
    "проблема в том",
    "минус для бизнеса",
    "у вас проблема",
    # Padding / pressure words (manager: "давит даже когда не хочется")
    "к сожалению",
    "беда в том",
    "неэффективно",
    # Round-2 manager fixes
    "пишу по",          # call-center opener
    "профиль живой",
    "профиль активный",
    "аккаунт активный",
    "контент качественный",
    "без обязательств",  # sales mini-commitment phrase
    "простой сайт",
    "простые сайты",
    "простые сайт",
]

REQUIRED_JSON_KEYS = ["hook", "observation", "problem", "value", "outcome", "ask"]

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Em-dash is the single biggest AI-tell in Russian-language LLM output.
# We require simple punctuation (period, comma, colon, parens) instead.
EM_DASH_CHARS = ("—", "–", " — ", " – ")

# AI loves «не A, а B» / «не только X, но и Y» — humans rarely write that way
# in cold messages. Trigger on a few common shapes.
AI_PARALLEL_PATTERNS = [
    re.compile(r"\bне\s+просто\s+\w+\s*,\s*а\b", re.IGNORECASE),
    re.compile(r"\bне\s+только\s+\w+\s*,\s*но\s+и\b", re.IGNORECASE),
    re.compile(r"\bне\s+\w+\s*,\s*а\s+\w+\b", re.IGNORECASE),
]

# Verb-chain pattern: 3+ verbs in present tense joined by commas.
# Example: "находит, ищет, листает" / "заходит, смотрит, открывает, выбирает".
# This is the agitate-problem template from copywriting courses — the manager
# called it out specifically: "Так пишут в учебниках по копирайтингу. Живые
# люди так не разговаривают."
# We detect runs of 3+ words ending in present-tense verb endings separated
# by ", ".
VERB_CHAIN_RE = re.compile(
    r"\b\w+(?:ет|ёт|ит|ат|ят|ает|яет|ует|ыт)\s*,\s*"
    r"\w+(?:ет|ёт|ит|ат|ят|ает|яет|ует|ыт)\s*,\s*"
    r"\w+(?:ет|ёт|ит|ат|ят|ает|яет|ует|ыт)\b",
    re.IGNORECASE,
)

# "Клиентка/клиент + verb of action" — the fictional persona acting.
# The manager: "ты пишешь клиентка, но её нет. Это абстракция. Получатель
# чувствует подвох."
FICTIONAL_PERSONA_RE = re.compile(
    r"\b(клиентк[аиуо]|клиент[аыу]?|покупател[ьяи])\s+"
    r"(ищ[её]т|листает|сравнивает|открывает|выбирает|уходит|переходит|"
    r"находит|смотрит|заход[иеи]т|просматривает|пишет|спрашивает|переспрашивает)\b",
    re.IGNORECASE,
)

# Round-2 manager fixes:

# "5 минут" / "минут пять" — узнаваемая sales-техника маленького обязательства.
FIVE_MIN_RE = re.compile(
    r"\b(пять|5)\s*минут\b|\bминут\s*(пять|5)\b",
    re.IGNORECASE,
)

# "Мы делаем [что-то]" в value — звучит как реклама компании.
# Можно "у нас есть формат" / "делаем такие сайты" / "этот формат собираем".
WE_DO_RE = re.compile(
    r"\bмы\s+делаем\s+\w+",
    re.IGNORECASE,
)

# Price patterns — manager strategy: убрать цену из первого сообщения.
# Ловим: "$300", "от $300", "162 000 ₸", "от 162к", "162000 тенге".
PRICE_RE = re.compile(
    r"(\$\s*\d+"                                                # $300, $ 300
    r"|от\s+\$\d+"                                              # от $300
    r"|\bот\s+\d{2,3}\s?[кк]\b"                                 # от 162к
    r"|\b\d{1,3}[\s ]?\d{3}\s*₸"                            # 162 000 ₸
    r"|\b\d{1,3}[\s ]?\d{3}\s*тенге"                        # 162 000 тенге
    r"|\b\d+\s*тысяч[а-я]*\s*тенге)",
    re.IGNORECASE,
)

# Timeframe patterns — same reasoning as price, manager moved both to second message.
TIMEFRAME_RE = re.compile(
    r"\b(за\s+неделю|неделя\s+на\s+запуск|на\s+неделю|"
    r"за\s+\d+\s*[-–]\s*\d+\s*дн|"
    r"за\s+\d+\s*дн|"
    r"\d+\s*[-–]\s*\d+\s*дней\b|"
    r"за\s+\d+\s+нед\w*|"
    r"\d+\s*[-–]\s*\d+\s+недел[ья]|"
    r"запуск\s+за\b)",
    re.IGNORECASE,
)

# Double-action observation: "нашёл X. зашёл Y." or "увидел X. посмотрел Y." —
# manager: "звучит как отчёт о работе". One observation source per message.
DOUBLE_ACTION_RE = re.compile(
    r"\b(нашёл|нашла|обнаружил|увидел|наткнулся|познакомился)\s+\w+"
    r"[^.!?]{0,80}[.!]\s*"
    r"(зашёл|заглянул|открыл|посмотрел|изучил)\b",
    re.IGNORECASE,
)


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

    # 2. Length — 6-part structure with outcome field expands target.
    # Hard bounds 55-110, sweet spot 70-90.
    if word_count < 55:
        return (f"Слишком коротко ({word_count} слов). Цель 70-90 слов. "
                f"Не хватает конкретики в outcome — что увидит клиентка и что изменится для владельца.")
    if word_count > 110:
        return (f"Слишком длинно ({word_count} слов). Цель 70-90 слов. "
                f"Режь на короткие фразы (одна мысль — одно предложение, до 18 слов).")

    # 3. Hook length.
    hook_words = len(parts["hook"].split())
    if hook_words > 12:
        return f"Hook слишком длинный ({hook_words} слов). Цель 5-10 слов."

    # 4. Stop phrases.
    low = full.lower()
    for sp in STOP_PHRASES:
        if sp in low:
            return (f"Использована запрещённая фраза: '{sp}'. "
                    f"Это диагноз, давление, маркетинговый жаргон или корпоративный шаблон. "
                    f"Перепиши простыми словами обычного человека.")

    # 4a. Em-dash — the single biggest AI-tell in Russian LLM output.
    for ch in EM_DASH_CHARS:
        if ch in full:
            return ("Использован em-dash ('—' или '–'). НЕ ИСПОЛЬЗУЙ длинное тире вообще. "
                    "Замени на точку, запятую, двоеточие или скобки. "
                    "Короткое тире для диапазонов (например '7-10 дней') допустимо.")

    # 4b. AI parallel structures ("не A, а B" / "не только X, но и Y").
    for pat in AI_PARALLEL_PATTERNS:
        m = pat.search(full)
        if m:
            return (f"Использована AI-симметрия: '{m.group(0)}'. "
                    f"Не используй конструкции 'не A, а B' / 'не только X, но и Y'. "
                    f"Перепиши простым прямым предложением.")

    # 4c-d. Verb-chain + fictional-persona patterns were removed in V5 because they
    # fire false-positives on legitimate AFTER-installation UX scenarios that the
    # manager explicitly requested ("Клиентка заходит, видит, выбирает, записывается").
    # Distinguishing BEFORE-fiction (bad) from AFTER-UX (good) is not reliably
    # doable with regex. We now rely on the prompt + few-shot examples to keep
    # the model on the right side. The constants are kept (above) for future use.

    # 4e. "5 минут" / "минут пять" — sales mini-commitment technique.
    m = FIVE_MIN_RE.search(full)
    if m:
        return (f"Использовано '{m.group(0)}' — узнаваемая sales-техника "
                f"маленького обязательства. Уберите. Просто 'Скинуть пример?' "
                f"или 'Могу показать как мог бы выглядеть'.")

    # 4f. "Мы делаем [сайт]" в value — звучит как реклама компании.
    m = WE_DO_RE.search(full)
    if m:
        return (f"Использовано '{m.group(0)}' — звучит как реклама компании. "
                f"Замените на 'У нас есть формат: сайт с прайсом...' или "
                f"'Делаем такие сайты для салонов: ...' или 'Этот формат собираем: ...'.")

    # 4g. Price in first message — manager strategy: только во втором сообщении.
    m = PRICE_RE.search(full)
    if m:
        return (f"Указана цена ('{m.group(0)}') в первом сообщении. "
                f"Это превращает диалог в коммерческий до того как человек заинтересовался. "
                f"Уберите цену. Её можно назвать во втором сообщении после положительного ответа.")

    # 4h. Timeframe in first message — same reasoning as price.
    m = TIMEFRAME_RE.search(full)
    if m:
        return (f"Указан срок запуска ('{m.group(0)}') в первом сообщении. "
                f"Это коммерческое уточнение, такое же как цена. Уберите, обсудим срок во втором сообщении.")

    # 4i. Double-action observation ("Нашёл вас на 2ГИС. Зашёл в Instagram").
    m = DOUBLE_ACTION_RE.search(full)
    if m:
        return (f"Двойное действие в observation: '{m.group(0)[:80]}...'. "
                f"Звучит как отчёт о работе. Упомяните что-то одно: либо 2ГИС, либо Instagram, не оба.")

    # 5. Personalization proof: business name OR its first significant word
    #    OR the company IG handle must appear somewhere in the message.
    #
    # Special case: if the business has a fully generic name (only generic
    # niche words: "Студия ногтей", "Beauty Salon"), Sonnet legitimately can't
    # quote it as a brand and the strict check 3x retries forever. For those
    # we relax — require either an IG handle OR an address/district anchor.
    biz = (lead.get("business_name") or "").strip()
    if biz:
        GENERIC_NAME_WORDS = {
            "студия", "салон", "красоты", "красота", "ногтей", "ногтевая",
            "beauty", "salon", "studio", "nail", "nails", "barbershop",
            "парикмахерская", "парикмахерская,", "маникюр", "косметология",
            "фитнес", "тренажёрный", "тренажерный", "тренажёрного", "зал",
            "турагентство", "турфирма", "тур", "агентство",
        }
        short = biz.split(",")[0].strip().lower()
        words = [w.strip(".,()").lower() for w in short.split() if w.strip(".,()")]
        is_generic = bool(words) and all(
            w in GENERIC_NAME_WORDS or len(w) <= 2 for w in words
        )

        proofs = [biz.lower(), short]
        first_word = words[0] if words else ""
        if first_word and len(first_word) >= 3 and first_word not in GENERIC_NAME_WORDS:
            proofs.append(first_word)
        company_ig = (lead.get("company_instagram") or "").lstrip("@").lower()
        if company_ig:
            proofs.append(company_ig)
        owner_ig = (lead.get("owner_instagram") or "").lstrip("@").lower()
        if owner_ig:
            proofs.append(owner_ig)

        if is_generic:
            # Generic name — accept IG handle OR address anchor as proof.
            address = (lead.get("address") or "").lower()
            if address:
                # Pick a distinctive address fragment (street/district name).
                # Skip stopwords like "улица", "дом", numbers.
                addr_words = re.findall(r"[а-яёa-z]{4,}", address)
                addr_words = [w for w in addr_words
                              if w not in {"улица", "проспект", "район", "микрорайон",
                                           "переулок", "бульвар", "дом"}]
                proofs.extend(addr_words[:3])
            # Also accept any explicit IG handle in message text.
            if not any(p and p in low for p in proofs):
                return (f"Generic business name ({biz!r}) requires either a Company IG handle "
                        f"or a specific street/district from the address to appear in the message. "
                        f"Не упомянут ни IG, ни кусочек адреса. Помести один из этих якорей "
                        f"в hook или observation.")
        else:
            if not any(p and p in low for p in proofs):
                return (f"Не упомянуто название бизнеса ({biz!r}), его бренд-часть или IG-handle. "
                        f"Гиперперсонализация требует хотя бы одного из них в hook или observation.")

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
    """Stitch the 6 parts into the message a human will read.

    Paragraph structure:
      1. hook
      2. observation + problem (one paragraph, observation feeds the soft question)
      3. value (what we offer — features, no price/timeframe)
      4. outcome (what changes after install — UX + owner relief)
      5. ask (soft request)
    """
    return (
        f"{parts['hook'].strip()}\n\n"
        f"{parts['observation'].strip()} {parts['problem'].strip()}\n\n"
        f"{parts['value'].strip()}\n\n"
        f"{parts['outcome'].strip()}\n\n"
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

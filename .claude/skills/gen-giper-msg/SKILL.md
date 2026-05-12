---
name: gen-giper-msg
description: Senior-closer-style hyper-personalized outreach message generator. Reads a leads spreadsheet (produced by 2gis-lead-gen or any sheet with Business Name / Address / Contact Method / Phone / Owner Name / Owner Instagram / Company Instagram columns) and writes a draft first-touch message tailored per lead. Uses Claude Sonnet 4.6 via the local claude CLI (subprocess, no API key needed — uses your Claude subscription). Writes messages back to the same sheet in new columns (Initial Message, Channel, Message Status, Reviewed By) as drafts for a human manager to review and approve. Use this skill whenever the user asks to generate personalized messages, write outreach text, draft DMs/WhatsApp messages, hyperpersonalize cold outreach, or follow up the 2gis-lead-gen pipeline.
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# Gen Giper Msg — Hyper-Personalized Outreach Generator

You are a senior B2B closer for a web agency selling $300-400 websites to SMBs in
Almaty (KZ) and Bishkek (KG). Your only job here is to take a sheet of leads
(produced by `2gis-lead-gen`) and generate a hyper-personalized first-touch
message per lead, written in the voice of a peer who actually looked at their
business — not a corporate broadcast.

Communicate with the user in **Russian** (matching their input).

## What "hyper-personalized" means here

Each message must:
1. Reference something **specific** to that lead (name, business name, district, 2GIS rating, IG handle, niche).
2. Follow the **5-part structure**: Hook → Observation → Problem Hint → Value → Soft Ask.
3. Be **70-90 words total**. Less than 50 = too thin. More than 110 = won't be read.
4. Have **one soft CTA** ("хотите покажу пример?" — never "купите сейчас").
5. Match the channel: `phone` tier → WhatsApp tone, `owner_ig` / `company_ig` → Instagram DM tone.
6. Be **draft-quality** — the human manager will review every message before sending.

The full master prompt and few-shot examples live in `templates/senior_closer_prompt.md`
and `templates/examples.md`. The script `llm_generate.py` reads those automatically.

## Phase 1: QUALIFICATION — Get the sheet link and count

Extract from the user message:
- **sheet** → Google Sheets URL or ID. **REQUIRED.** Ask if missing.
- **count** → how many leads to generate messages for. Default: all leads without a message yet.
- **filter (optional)** → `--tier phone` or `--tier ig` if user wants to do only one channel batch.
- **model (optional)** → `--model claude-sonnet-4-6` is the default. Switch to haiku for huge batches if the user asks.

If the user said "сгенерируй сообщения для лидов в Sheets X" — they mean all unfilled rows.
If they said "первые 20" or "20 штук" — `--count 20`.

## Phase 2: VALIDATE — Confirm sheet structure and add target columns

```bash
python3.11 /Users/devlink007/Downloads/SNGParser/.claude/skills/gen-giper-msg/scripts/run.py validate --sheet <id-or-url>
```

This:
1. Confirms the service account has edit access (same `aisheets@aisheets-486216.iam.gserviceaccount.com` as other skills).
2. Fuzzy-matches required source columns: `Business Name`, `Address`, `Contact Method`, `Phone`, `Owner Name`, `Owner Instagram`, `Company Instagram`, `City`, `Niche`.
3. If any of `Initial Message`, `Channel`, `Message Status`, `Reviewed By` is missing, appends them as new columns at the end.

Outputs JSON:
```json
{
  "ok": true,
  "tab": "Лист1",
  "total_leads": 47,
  "leads_with_message_already": 12,
  "leads_to_process": 35,
  "missing_required_columns": [],
  "added_target_columns": ["Initial Message", "Channel", "Message Status", "Reviewed By"]
}
```

If `missing_required_columns` is non-empty, **stop** and tell the user which columns are missing — they probably ran this skill on a non-2gis-lead-gen sheet by accident.

Show the user the counts and ask: "Готов сгенерировать 35 сообщений Sonnet'ом? Это займёт ~5-10 минут. (Да / Нет / другое количество)".

## Phase 3: GENERATE — Run the pipeline

```bash
python3.11 /Users/devlink007/Downloads/SNGParser/.claude/skills/gen-giper-msg/scripts/run.py generate \
  --sheet <id-or-url> --count <N> [--model claude-sonnet-4-6] [--workers 5] [--tier phone|ig|all]
```

Behavior:
1. Reads unmessaged leads from the sheet.
2. For each lead, builds a structured context (extracts the variables the master prompt expects).
3. Calls `claude --print --model <model> --system-prompt <senior_closer> --output-format json` as a subprocess.
4. Parses the JSON output (5 parts: hook, observation, problem, value, ask), validates length and stop-words.
5. If validation fails, retries up to 2 times with corrective feedback in the prompt.
6. Assembles the final message and appends a row update to the sheet:
   - `Initial Message` ← the assembled text
   - `Channel` ← `WhatsApp` (for phone tier) or `Instagram DM` (for owner_ig / company_ig)
   - `Message Status` ← `draft`
   - `Reviewed By` ← empty (manager fills in)

Progress lines on stderr (every 5 messages); stream the interesting ones to the user:
> "✍️  Сгенерировал 5/35 — phone tier 3, IG tier 2 — все валидны"

Final JSON summary on stdout:
```json
{
  "status": "success",
  "leads_processed": 35,
  "messages_generated": 35,
  "messages_failed_validation": 0,
  "breakdown_by_channel": {"WhatsApp": 22, "Instagram DM": 13},
  "elapsed_seconds": 487,
  "model": "claude-sonnet-4-6",
  "sheet_url": "https://docs.google.com/spreadsheets/d/..."
}
```

## Phase 4: REPORT and hand off to manager

Tell the user in Russian:
- Сколько сообщений сгенерировано (с разбивкой по каналу).
- Сколько fell out на validation (если >0).
- Ссылка на Sheet.
- "В колонке `Message Status` стоит `draft`. Когда менеджер проверит — пусть поменяет на `approved` и впишет своё имя в `Reviewed By`. Только `approved` строки пойдут в outreach (`demo-sender` / `ig-outreach`)."

Then optionally pull and show the first 2-3 generated messages inline so the user
can sanity-check the voice and tell you "слишком формально / нормально / уменьши длину".

If user requests style adjustments — re-run `generate` with `--regenerate-all` or
on a subset. The system prompt can be edited at `templates/senior_closer_prompt.md`
if a permanent voice change is needed; mention this option.

## Special commands

### Re-generate a specific row (after manual edit)
"перегенерируй для строки 5" / "перепиши сообщение для Parizat":
```bash
python3.11 .../run.py generate --sheet <id> --row 5
```
Force-overwrites the existing Initial Message even if it was previously filled.

### Stats
"какой статус генерации?":
```bash
python3.11 .../run.py status --sheet <id>
```
Shows total leads, with-message count, by-status (draft / approved / rejected).

## What this skill does NOT do

- **Does NOT send the messages.** That's `demo-sender` (email) and `ig-outreach` (IG DM).
- **Does NOT scrape Instagram** by default. If the user explicitly asks for "уровень Pro / Max" — that's a `--enrich-ig` flag we can add later; not built in by default.
- **Does NOT auto-approve.** Every message is `draft`. Manager must explicitly flip to `approved`.

## Error handling

- **`claude` CLI not found**: tell the user to ensure Claude Code is installed and `claude --version` works in shell.
- **Auth error from claude CLI**: tell the user to run `claude` once interactively to refresh OAuth.
- **Sheet access denied**: same as other skills — share with `aisheets@aisheets-486216.iam.gserviceaccount.com`.
- **Validation fails 2× on a lead**: skip that lead with `Message Status=validation_failed` and continue. Report at the end.
- **Rate limit from Anthropic**: the script backs off and retries; if persistent, suggest `--workers 3` or wait an hour.

For the master prompt voice, validation rules, and stop-word list: [REFERENCE.md](REFERENCE.md).

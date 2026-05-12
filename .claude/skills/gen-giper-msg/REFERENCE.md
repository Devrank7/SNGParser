# Gen Giper Msg — Reference

Operational reference for the `gen-giper-msg` skill.

## Files

```
gen-giper-msg/
├── SKILL.md                              ← agent instructions (entry point)
├── REFERENCE.md                          ← this file
├── templates/
│   ├── senior_closer_prompt.md          ← master system prompt (voice, structure, rules)
│   └── examples.md                       ← few-shot examples (5 good + 3 anti-examples)
├── evals/
│   └── evals.json                        ← test prompts for future skill-creator benchmarks
└── scripts/
    ├── run.py                            ← CLI (validate / generate / status)
    ├── sheets_io.py                      ← fuzzy column matching + Sheets read/write
    ├── personalize.py                    ← per-lead user-prompt builder
    └── llm_generate.py                   ← claude --print subprocess + parse + validate + retry
```

## Auth model (the unusual part)

**No API key.** The skill calls the `claude` CLI as a subprocess, which uses the
user's existing Claude Code OAuth credentials (Max subscription). Cost goes
through the subscription, not Anthropic API billing.

Two critical setup details the script handles automatically:

1. **`CLAUDECODE` env var is unset** in the subprocess. If it's set, `claude`
   refuses to launch as a child of an active Claude Code session and the
   subprocess errors out with "Claude Code cannot be launched inside another
   Claude Code session."
2. **`--print` mode** is non-interactive. We pipe the user prompt via stdin and
   the system prompt via `--system-prompt`.

If the OAuth token expires (Claude Code rotates it via refresh), the user might
need to run `claude` interactively once to refresh. The skill detects this via
the CLI returning an auth error and surfaces it.

## Pipeline data flow

```
[user request: "сгенерируй сообщения для лидов в Sheet X"]
   ↓
Phase 1: agent parses (sheet, count, tier filter, model)
   ↓
Phase 2: run.py validate --sheet ID
   ↓ idempotent: adds missing target columns to sheet
   ↓ reports counts to user, agent asks for confirmation
   ↓
Phase 3: run.py generate --sheet ID --count N
   ↓ sheets_io.read_leads_without_message() pulls rows where Initial Message is empty
   ↓ for each lead (ThreadPoolExecutor, default 5 workers):
   │     personalize.build_user_prompt(lead) → Russian context block
   │     llm_generate.call_claude(...) → subprocess "claude --print"
   │     llm_generate.extract_message_json() → strip ```json ... ``` fences if any
   │     llm_generate.validate_message() → length / stop-words / name presence
   │         on failure: retry up to 3 times with corrective feedback
   │     on success: assemble_final() → 4-paragraph plain text
   │     sheets_io.write_message() → batchUpdate to Initial Message + Channel + Message Status
   ↓ JSON summary on stdout
   ↓
Phase 4: agent reports counts + first 2-3 messages inline for review
```

## Sheet schema

### Source columns (read from)

Fuzzy-matched by name (case-insensitive substring). Order doesn't matter.

| Canonical key | Header strings accepted |
|---|---|
| `business_name` | Business Name, Название бизнеса, Название, Name |
| `address` | Address, Адрес |
| `contact_method` | Contact Method, Метод контакта, Канал |
| `phone` | Phone, Телефон |
| `owner_name` | Owner Name, Имя владельца, Владелец |
| `owner_instagram` | Owner Instagram, Instagram владельца |
| `company_instagram` | Company Instagram, Instagram компании |
| `city` | City, Город |
| `niche` | Niche, Ниша |
| `twogis_url` | 2GIS URL, 2gis, URL |

**Required**: `business_name`, `contact_method`. Others are optional but the
more we have, the more personalized the message.

### Target columns (written by us)

Appended to the end of the header row if not already present.

| Column | Values |
|---|---|
| **Initial Message** | The assembled 4-paragraph message text |
| **Channel** | `WhatsApp` (phone tier) or `Instagram DM` (owner_ig / company_ig tier) |
| **Message Status** | `draft` (new) / `approved` / `rejected` / `validation_failed` |
| **Reviewed By** | Manager's name (filled in after they review) |

## Master system prompt — at a glance

Full file: [templates/senior_closer_prompt.md](templates/senior_closer_prompt.md)

Key constraints encoded:
- 5-part JSON output: `hook`, `observation`, `problem`, `value`, `ask`
- Total length 70-90 words (validator allows 40-130)
- Hook ≤ 15 words
- Stop-words list (corporate buzzwords, AI tells) — see below
- Don't invent facts not in the context
- Match the channel (WhatsApp warmth vs. IG DM lightness)
- Match the niche (problem hint adapted: beauty / fitness / travel / barber)

## Stop-words blacklist (enforced by validator)

Case-insensitive substring match — any of these in the message → retry:

- "уникальное предложение"
- "только сегодня", "ограниченное предложение"
- "лучшее качество", "лучшая цена"
- "наша компания", "наша команда"
- "не упустите возможность"
- "качественный сайт", "профессиональный сайт"
- "профессиональная команда"
- "индивидуальный подход"
- "комплексное решение"
- "повысим конверсию", "увеличим продажи"
- "хочу сотрудничать"
- "уважаемый клиент"
- "пишу вам, потому что"

## Cost & rate limit

With Max subscription, calls are free (covered by subscription) but rate-limited.

- **Model**: `claude-sonnet-4-6` (default) — best quality. Switch to
  `claude-haiku-4-5` via `--model` for very large batches (200+) if rate
  limits bite.
- **First call** populates a prompt cache (`cache_creation_input_tokens ≈ 19K`)
  — costs more on the meter ($0.02). Subsequent calls hit cache (`cache_read`),
  ~$0.001-0.005 each.
- **Real measured cost on a 5-message smoke test**: ~$0.13 USD total (cache
  primed once, then 4 cache hits).
- **Workers**: default 5 — keeps us comfortably below rate limit ceilings
  on Max-20x tier. Increase to 10 if you have headroom.

## CLI examples

```bash
# Validate a sheet (also adds missing target columns)
python3.11 .../scripts/run.py validate --sheet 194zVZg90O9B586E9XvdSlvKj2ojAyPn8pReJu53RgBQ

# Status report
python3.11 .../scripts/run.py status --sheet ...

# Generate messages for all unmessaged leads
python3.11 .../scripts/run.py generate --sheet ... --count 35

# Smaller batch, cheaper model
python3.11 .../scripts/run.py generate --sheet ... --count 200 --model claude-haiku-4-5

# Only phone-tier leads (WhatsApp messages only)
python3.11 .../scripts/run.py generate --sheet ... --tier phone

# Strict retry budget — give up faster on validation failures
python3.11 .../scripts/run.py generate --sheet ... --max-attempts 2
```

## Troubleshooting

### `claude: command not found` in subprocess
The `claude` CLI must be on PATH for whichever shell `subprocess.run` spawns.
On macOS with Claude Code GUI installed, it lives in `/usr/local/bin/claude`
or similar — should Just Work.

### Auth error from claude CLI
Run `claude` once interactively in a regular terminal to refresh OAuth. The
script doesn't trigger refresh on its own.

### "Claude Code cannot be launched inside another Claude Code session"
The `CLAUDECODE` env var was leaked into the subprocess. We unset it
explicitly in `llm_generate.call_claude` — if you see this, check that env
copy logic hasn't been changed.

### Validation failures dominating
Look at `failed_examples` in the JSON summary. Most common causes:
- Model decided not to mention the business name (validator catches this)
- Model invented an owner name when none was given (validator catches this)
- Output too short (<40 words) because lead context was very sparse

If a specific niche or city consistently fails, add an example for it to
`templates/examples.md` and re-run.

### Rate limit (`429`)
Drop `--workers` to 3, or split the batch across multiple sessions / time.
Max-20x tier allows ~200 requests/hour comfortably.

## Hand-off to outreach skills

After messages are generated and the manager approves them (changes
`Message Status` from `draft` → `approved`):

- **WhatsApp messages** (Channel = `WhatsApp`) → not yet automated; manual
  for now or use a separate sender skill.
- **Instagram DM** (Channel = `Instagram DM`) → can feed into `ig-outreach`
  with a sheet filter on `Message Status = approved`.

Email outreach is `demo-sender`, which lives on a different sheet (with
email addresses), not the lead-gen output.

## What this skill explicitly does NOT do

- It does NOT enrich with Instagram bio or posts by default (Lite mode). If
  hyperpersonalization quality is insufficient on Lite, we can add
  `--enrich-ig` and `--enrich-posts` flags later — these would use the
  same Apify actor that `2gis-lead-gen` already wires up.
- It does NOT send any messages. All output is `draft`.
- It does NOT auto-approve or auto-rerun. Manager always has the final say.
- It does NOT use the Claude Python SDK or Agent SDK — those would require
  an API key. We use the local `claude` CLI subprocess to leverage the user's
  Max subscription instead.

# 2GIS Lead Gen — Reference

Operational reference for the `2gis-lead-gen` skill. See [SKILL.md](SKILL.md) for the
high-level agent instructions.

## Files

```
2gis-lead-gen/
├── SKILL.md              ← agent instructions (entry point)
├── REFERENCE.md          ← this file
├── db/
│   └── leads.db          ← SQLite dedup store (auto-created)
└── scripts/
    ├── run.py            ← main CLI
    ├── niches.py         ← city + niche tables
    ├── data_sources.py   ← Apify + Direct 2GIS abstraction
    ├── website_check.py  ← 3-way "has website" filter
    ├── phone_classify.py ← KZ/KG mobile vs landline classifier
    ├── find_owner.py     ← Serper + IG bio cascade for owner discovery
    ├── dedup_db.py       ← SQLite operations
    └── sheets_writer.py  ← Google Sheets append
```

## Environment variables

Stored in `/Users/devlink007/Downloads/SNGParser/.env.local`. Loaded by `_shared/config.py`.

| Var | Required? | Purpose |
|-----|-----------|---------|
| `APIFY_API_KEY` | yes (unless `TWOGIS_API_KEY` set) | Apify token for `m_mamaev/2gis-places-scraper` and `apify/instagram-profile-scraper` |
| `SERPER_API_KEY` | yes | Serper.dev Google Search API for owner-name discovery |
| `TWOGIS_API_KEY` | optional | If set, switches to direct 2GIS Catalog API (free) instead of Apify. **Must include `contact_groups` permission** — request at [dev.2gis.com/api](https://dev.2gis.com/api). |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | yes (set in .env.local) | Path to service account JSON — defaults to `./service_account.json` |
| `TELEGRAM_REPORT_CHAT_ID` | optional | Comma-separated chat IDs for run-completion reports |
| `TELEGRAM_BOT_TOKEN` | optional | Bot token for the report (already present from other skills) |

## Pipeline data flow

```
user request (Russian)
   │
   ▼
[Claude parses city, niche, count, sheet]
   │
   ▼
run.py source-status
   │  → JSON: data_source, est_cost, balance, already_in_db
   ▼
[Claude shows estimate & asks for confirmation]
   │
   ▼
run.py validate-sheet
   │  → ok | permission_denied | header_mismatch
   ▼
run.py search ─────────────────────────────────────────────────┐
   │                                                            │
   │  DataSource.search(city, queries, max=count*4)             │
   │     └─ Apify: m_mamaev/2gis-places-scraper                 │
   │        with domain=2gis.kz/kg, includeContacts=true        │
   │                                                            │
   │  SQLite dedup filter (skip twogis_id already known)        │
   │                                                            │
   │  Pre-fetch ALL company IG profiles in ONE Apify batch      │
   │  (used by website_check only — we don't mine bios for      │
   │  the owner's contact)                                      │
   │                                                            │
   │  For each candidate (parallel, ThreadPoolExecutor):        │
   │     1. website_check.check_business():                     │
   │        a. 2GIS website field non-empty + real domain       │
   │        b. IG externalUrl points to a real domain           │
   │        c. corporate email at non-freemail resolvable domain│
   │        if ANY pass → skip (business has online presence)   │
   │     2. phone_classify.pick_best_mobile(phones from 2GIS)   │
   │        if mobile found → contact_method=phone, commit      │
   │     3. else if biz.instagram present:                      │
   │        Serper #1: "[name] [city] директор/owner"           │
   │          → owner_name (Russian "Имя Фамилия" near hint)    │
   │        Serper #2 (only if name found):                     │
   │          "[owner_name] [city] site:instagram.com"          │
   │          → first usable IG handle                          │
   │        if handle found → contact_method=owner_ig           │
   │             owner_ig_source = "serper-auto" (unverified!)  │
   │        else → contact_method=company_ig (name as hint)     │
   │     4. else (no phone, no IG) → drop                       │
   │                                                            │
   │  Stop at target N or when candidates exhausted             │
   ▼                                                            │
sheets_writer.append_leads                                      │
   │  → row numbers persisted back to SQLite                    │
   ▼                                                            │
JSON summary on stdout ←──────────────────────────────────────┘
```

## SQLite schema

`db/leads.db`:

```sql
CREATE TABLE leads (
  twogis_id          TEXT PRIMARY KEY,
  name               TEXT,
  city               TEXT,        -- slug: almaty | bishkek
  niche              TEXT,        -- slug: hair_beauty | nail_cosmetic | fitness | travel | custom:...
  phone              TEXT,        -- E.164 mobile if found
  phone_type         TEXT,        -- mobile | landline | unknown
  owner_name         TEXT,
  owner_phone        TEXT,
  owner_instagram    TEXT,
  owner_ig_source    TEXT,        -- '' (manual / not set) | 'serper-auto'
  size_estimate      TEXT,        -- 'micro' | 'sweet_spot' | 'large' | 'large_chain' | 'unknown'
  review_count       INTEGER,
  rating_count       INTEGER,
  branch_count       INTEGER,
  company_instagram  TEXT,
  website            TEXT,
  has_website        INTEGER,     -- always 0 here (we drop those before insert)
  contact_method     TEXT,        -- phone | owner_ig | company_ig
  data_source        TEXT,        -- apify | direct_2gis
  twogis_url         TEXT,
  address            TEXT,
  discovered_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  sheet_row          INTEGER      -- 1-indexed row in Google Sheets
);
CREATE INDEX idx_city_niche ON leads(city, niche);
CREATE INDEX idx_discovered_at ON leads(discovered_at);
```

`run.py clean --city X --niche Y` deletes the entire slice. Use only with explicit user permission.

## Google Sheets output columns

Headers are written automatically on first append if sheet is empty:

| # | Column | Source |
|---|--------|--------|
| 1 | Discovered At | UTC timestamp |
| 2 | City | Russian name (Алматы / Бишкек) |
| 3 | Niche | Russian name (Маникюр / косметология) |
| 4 | Business Name | `shortName` from 2GIS |
| 5 | Address | full street address |
| 6 | Contact Method | `phone` / `owner_ig` / `company_ig` |
| 7 | Phone | E.164 (only if classified mobile, tier=phone) |
| 8 | Phone Type | `mobile` (we never write landline) |
| 9 | Owner Name | from Serper search — owner-name hint, often empty |
| 10 | Owner Instagram | `@handle` if Serper guessed one (tier=owner_ig). **Unverified** — see column 11. Manager may overwrite with the correct account |
| 11 | Owner IG Source | `serper-auto` when the handle in col 10 was machine-guessed (manager must verify). Empty when col 10 is empty or filled in manually |
| 12 | Company Instagram | `@handle` from 2GIS — the manager's entry point for tier=company_ig leads, and a sanity-check anchor for tier=owner_ig leads |
| 13 | 2GIS URL | canonical 2gis.kz/kg link |
| 14 | Data Source | `apify` or `direct_2gis` |
| 15 | Size Estimate | `micro` / `sweet_spot` / `large` / `large_chain` / `unknown` (see Company-size filter section) |
| 16 | Owner Confidence | `high` / `medium` / `unknown` / `low` — populated by `enrich-confidence` command, see Owner-confidence section |
| 17 | Owner Conf Score | Signed integer — sum of weighted signals (for debugging) |
| 18 | Owner Conf Reasons | Short text explanation of which signals applied |

## Company-size filter (4-10 employees by default)

Added 2026-05-12 after a real-world 80-lead Almaty hair-beauty run showed
30% of leads were micro or large-chain — not our target. Now the pipeline
classifies each candidate by proxy signals from 2GIS BEFORE expensive
enrichment (Serper / IG bio fetches) and skips out-of-bucket candidates.

### Proxy signals from 2GIS

| Signal | Provided by m_mamaev actor | Used for |
|---|---|---|
| `reviewsCount` | Number of customer reviews on 2GIS | Proxy for traffic, weight on size |
| `ratingCount` | Number of ratings (≥ reviewsCount usually) | Secondary signal |
| `brand.branchCount` | Number of branches under same brand | Chain detection |

### Size classification (`niches.estimate_size`)

```
branch_count > max_branches    → "large_chain"
reviewsCount > max_reviews     → "large"
ratingCount > max_rating_count → "large"
reviewsCount < min_reviews
   AND ratingCount < 2x min   → "micro"
otherwise (within all bounds)  → "sweet_spot"
fallback                       → "unknown"
```

### Default thresholds (in `niches.DEFAULT_SIZE_THRESHOLDS`)

```python
{
    "min_reviews": 10,        # below = micro (1-3 person operation)
    "max_reviews": 300,       # above = large established business
    "max_rating_count": 600,
    "max_branches": 3,        # 4+ = chain
}
```

Calibrated against the 80-lead Almaty hair-beauty run on 2026-05-12 —
70% landed in sweet_spot with these limits.

### Per-niche overrides

`niches.NICHE_SIZE_OVERRIDES` is a hook for niche-specific tuning if
default thresholds turn out wrong for, say, travel agencies (which
typically have fewer reviews). Currently empty.

### CLI overrides

- `--min-reviews N` / `--max-reviews N` / `--max-branches N` — override thresholds
- `--include-micro` — accept micro businesses
- `--include-large` — accept large + large_chain
- `--include-unknown` — accept unclassified

Default `allowed_sizes = {"sweet_spot"}`.

### Filter runs FIRST in the pipeline

Inside `_enrich_one`, size check happens BEFORE website-check and BEFORE
the IG batch fetch results are consumed. This means:

- Skipped-by-size candidates don't consume the Serper budget
- IG profile-fetch batch (which runs before enrichment) still hits all
  candidates regardless of size — could optimise later by skipping
  pre-fetch for businesses that will fail size

Future improvement: filter by size BEFORE the IG pre-fetch to save more.

## Owner-confidence scoring (Tier 1)

Module `scripts/owner_confidence.py`. CLI entry: `run.py enrich-confidence`.

Computes a per-lead score for **likelihood that the phone is the owner's
personal number** (vs admin / receptionist / agent). Composite signal —
no single source answers this for KZ/KG, so we add multiple weak signals.

### Signals currently implemented (Tier 1, free or near-free)

| Signal | Source | Score weights |
|---|---|---|
| `cross_card_freq` | SQLite (`phones_with_frequency`) | unique phone +2, on 2 cards 0, on 3+ −3 |
| `serper_role` | One Serper.dev query per phone, scans top-10 snippets for keywords | owner-keyword (директор/основатель/founder) +2; admin-keyword (ресепшн/администратор) −3; agent-context (OLX/Avito/agent) −1 |

### Bucketing

```
score >= 3   →  "high"     (probably personal owner)
score 1..2   →  "medium"   (some positive signal)
score -1..0  →  "unknown"  (no strong signal either way)
score <= -2  →  "low"      (likely admin / agent / employee)
```

### Real distribution example (78 travel leads, Almaty + Bishkek, 2026-05-13)

```
high:    1   (1%)   — Serper found 'директор' near number
medium:  68  (93%)  — phone unique to 1 card, no role signal
unknown: 4   (5%)   — Serper found admin keyword like 'информация'
low:     0
```

73 phone-tier leads scored. 5 leads without phone (company_ig / owner_ig tiers)
skipped — they have no phone to verify.

### Deferred tiers (NOT yet implemented)

| Tier | Signal | Cost / 100 | Status |
|---|---|---|---|
| 2a | Egov.kz director match (KZ only) | $0 (requires dev cabinet registration) | Deferred |
| 2b | Maytapi WhatsApp isBusiness | $0.25/100 | Deferred |
| 3 | Kompra.kz paid director DB | $3-8/100 | Out of scope |
| 4 | GetContact unofficial (rooted Android, ToS-gray) | $0 infra + ban risk | Won't do |

Future iterations should add Tier 2a first — Egov.kz is free and provides
the strongest signal we know of (Egov director name → fuzzy match against
2GIS company → IG bio name → match score).

## Pricing notes (Apify free tier)

- Apify gives every account **$5/month free credit**, no card required, resets monthly.
- Active actor: `m_mamaev/2gis-places-scraper` (PAY_PER_EVENT: ~$2/1K places + ~$0.80/1K contacts).
- Plus: `apify/instagram-profile-scraper` ~$2.30/1K profiles for bio reads.
- **Real measured cost from smoke test**: ~$0.07 per 3 leads ≈ **$0.023 per lead**.
- So ~200 leads per run ≈ **$4.50**, just inside the $5 free credit. One run/month free.
- Above that, Apify Starter is $49/month with 50× the credit.

**Why not `zen-studio/2gis-places-scraper-api`?** That actor has its OWN internal free-tier
gate (separate from Apify's platform credit) that triggers after the first successful run
and returns a `_warning: Free tier limit reached` stub. `m_mamaev` has no such gate.

## KZ/KG phone prefix tables

Defined in `phone_classify.py`. Sources: ITU TSB Operational Bulletin + State
regulator allocation tables (Kazakhstan Ministry of Digital Development +
Kyrgyz State Communications Agency). Last verified: 2026-05-12.

### KZ mobile — `+7 7XX…` where XXX is one of:

```
700-708   Beeline / Kcell / Tele2 / Activ shared pool
747       Tele2 (ex-Altel)
771-778   Activ / Kcell / Beeline shared pool
```

**Explicitly NOT mobile despite the 7xx prefix** — these are VoIP / satellite,
and we classify them as `landline` so we never WhatsApp them:

```
750, 751         dial-up / VoIP access codes
760-764          Kulan satellite + commercial IP networks (763 = Arna)
```

City codes (always landline): `727` Almaty, `7172` Astana, `7212` Karaganda.

### KG mobile — `+996 XXX…` where XXX is one of:

```
220-229          Sky Mobile (Beeline KG)
500-509 (subset) Nur Telecom (O!)
550-559          Alfa Telecom (MegaCom)
700-709          Nur Telecom (O!)
770-779          Sky Mobile (Beeline KG) / MegaCom
990, 996-999     Alfa Telecom (MegaCom) / Nur Telecom
```

City codes (always landline): `312` Bishkek, `3222` Osh, `3138` Kant.

### What `classify()` does on a phone string

1. `normalize()`: strip formatting, fold `8 7XX…` → `+7 7XX…`, fold local KG `0 5XX…` → `+996 5XX…`
2. Country from E.164: `+7` + 10 digits → KZ; `+996` + 9 digits → KG
3. Look up the subscriber prefix (3 chars after country code) in the table above
4. Match → `mobile`; no match → `landline`; can't parse → `unknown`
5. `pick_best_mobile()` returns the first `mobile` entry; only `mobile` enters the `phone` tier

### Known limits (not currently solved in code)

- **VoIP detection beyond the static blocklist**: cloud-PBX providers (Zadarma,
  OnlinePBX, MaxiPhone) sell Almaty/Bishkek landline-shaped numbers that route
  to call-centers. Twilio Lookup `line_type_intelligence` flags these for
  ~$0.008/number. Not currently wired.
- **SIM activity (HLR)**: we don't verify a number is actually live on a
  network today. hlr-lookups.com offers SS7 HLR for €0.005-0.010 with KZ/KG
  coverage. Not currently wired.
- **Owner-vs-employee distinction**: not solvable from the phone number alone
  at any price (no commercial API returns it). Handled upstream via
  name-matching the 2GIS / IG-bio name against Egov/Kompra registered-owner
  records — that's enrichment-layer work, not phone classification.

## Apify actor input cheat-sheet (m_mamaev/2gis-places-scraper)

```json
{
  "query": ["парикмахерская", "салон красоты"],
  "locationQuery": "Алматы",
  "domain": "2gis.kz",
  "language": "ru",
  "maxItems": 100,
  "includeContacts": true,
  "maxReviewsPerPlace": 0,
  "maxMediaPerPlace": 0
}
```

Key params:
- `domain`: `2gis.kz` for Алматы, `2gis.kg` for Бишкек.
- `maxItems`: per-query cap, not total. With 4 queries × 50 = up to 200 places fetched.
- `includeContacts`: **must be true** — without it you get no phones/emails/socials.
- `maxReviewsPerPlace=0` and `maxMediaPerPlace=0`: skip reviews and photos (paid extras we don't need).

Important output fields (different from zen-studio!):
- `id` → `twogis_id`
- `shortName` → preferred name
- `phoneValue` → list of E.164 strings (use this, not `phoneText`)
- `email` → list of email strings (note: singular field name, but list-valued)
- `website` → string or null
- `socials.other[]` → Instagram URL often hides here (m_mamaev doesn't always categorize correctly)

## Troubleshooting

### "Free tier limit reached" warning in Apify output

The previous actor (`zen-studio/2gis-places-scraper-api`) had this gate. We've switched to
`m_mamaev/2gis-places-scraper`. If you see this from m_mamaev too, switch back to the previous
actor or upgrade Apify to Starter.

### Sheet permission denied

```
ERROR: Cannot access spreadsheet. Share it with:
aisheets@aisheets-486216.iam.gserviceaccount.com
```

Open the sheet in browser → Поделиться → add the email as Editor.

### Apify balance exhausted

```bash
rtk proxy curl -sH "Authorization: Bearer $APIFY_API_KEY" \
  https://api.apify.com/v2/users/me/limits
```

Check `current.monthlyUsageUsd` vs `limits.maxMonthlyUsageUsd`. Either wait until next month
or upgrade plan.

### Few or zero leads collected

Most likely cause: the niche query string doesn't match what 2GIS uses. Run with `--niche-query`
to override the default list:

```bash
python3.11 scripts/run.py search --city almaty --niche custom \
  --niche-query "массажный салон" --count 50 --sheet ...
```

### "Free tier" pattern at item level

If the actor returns items containing `_warning` keys, our normalizer dies silently — we
should improve detection. Check stderr for `item N normalize failed` lines.

### Long runs

200 leads ≈ ~3 hours of wall-clock time because we do an IG profile lookup per candidate +
Serper for owners. To speed up: drop the Serper step (set `SERPER_API_KEY=""`) — at the cost
of fewer leads in the `phone`/`owner_ig` tiers.

## Inputs the agent should NEVER assume

- Default Google Sheet ID: the user must supply per-run; we have a default
  (`194zVZg90O9B586E9XvdSlvKj2ojAyPn8pReJu53RgBQ`) but use only on explicit "по умолчанию".
- Niche slug: if user phrases differently, accept free-form via `--niche-query`.
- Cost: never claim "free" without checking balance first — Apify free credit can be exhausted
  by prior runs in the same month.

## Switching to direct 2GIS API (free path, optional)

If you obtain a 2GIS Platform Manager API key with the `contact_groups` permission:

1. Add to `.env.local`:
   ```
   TWOGIS_API_KEY=your_key_here
   ```
2. The skill auto-detects this on next `source-status` call and reports
   `data_source: "direct_2gis"` with `cost: 0`.
3. Phones, emails, and socials come from `contact_groups`. Instagram bio lookups
   still use Apify (free tier covers ~2000 IG profile fetches/month).

To request the key: go to [dev.2gis.com/api](https://dev.2gis.com/api), get a demo key,
then email dev@2gis.ru asking for `contact_groups` permission to be enabled on it.
Mention you are doing lead-gen for SMB web services in KZ/KG.

## Related skills

- `demo-sender` — sends outreach emails to leads with email addresses (this skill doesn't collect those).
- `ig-outreach` — DMs owner Instagram accounts collected by this skill.
- `_shared/sheets.py` — Google Sheets utility we reuse for access.

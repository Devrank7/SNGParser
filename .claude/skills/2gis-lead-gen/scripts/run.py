#!/usr/bin/env python3
"""2GIS Lead Gen CLI — orchestrates the full pipeline.

Subcommands:
  source-status     — report active source, balance, cost estimate, dedup count
  validate-sheet    — check Google Sheets access & headers
  search            — main pipeline: scrape → filter → enrich → append
  status            — show SQLite stats (counts by city/niche)
  clean             — wipe a city × niche slice from SQLite (irreversible)
  telegram-report   — send a summary JSON file to Telegram chats

Designed to be invoked by the skill's SKILL.md instructions. Outputs structured
JSON on stdout for the agent to read, with human-readable progress on stderr.
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

# Make _shared importable (the package lives two levels up).
SKILLS_DIR = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SKILLS_DIR))

from _shared.config import load_env  # type: ignore

from niches import (
    NICHES, CITIES, resolve_niche, resolve_city,
    thresholds_for_niche, estimate_size,
)
from data_sources import get_data_source, ApifyDataSource, Direct2GisDataSource
from website_check import check_business as check_website
from phone_classify import classify, pick_best_mobile
from find_owner import find_owner
from dedup_db import connect as db_connect, is_known, insert_lead, stats as db_stats, clean as db_clean


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(f"[run] {msg}", file=sys.stderr, flush=True)


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _now_iso():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def _city_dedup_count(conn, city_slug: str, niche_slug: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM leads WHERE city = ? AND niche = ?",
        (city_slug, niche_slug),
    ).fetchone()
    return row["n"] if row else 0


def _telegram_progress(message: str, enabled: bool = True):
    """Best-effort Telegram progress note. Never crashes the pipeline.

    Uses the lightweight `send_telegram_text` helper (added 2026-05-12) so we
    can pass a free-form string. `send_telegram_report` is a structured-dict
    formatter intended for run-end summaries, not for progress pings.
    """
    if not enabled:
        return
    try:
        from _shared.telegram import send_telegram_text  # type: ignore
        env = load_env()
        send_telegram_text(env, message)
    except Exception as e:
        _log(f"telegram send skipped: {e}")


# ─────────────────────────────────────────────────────────────────────
# source-status
# ─────────────────────────────────────────────────────────────────────

def cmd_source_status(args):
    env = load_env()
    src = get_data_source(env)
    city_slug, city_ru, country = resolve_city(args.city)
    niche_slug, _, niche_ru = resolve_niche(args.niche)

    # We over-fetch ~4x target because (a) ~50% will have a website,
    # (b) some will have no reachable contact at all.
    target = args.count
    estimated_records = target * 4
    est_cost_places = 0.0
    est_cost_ig_batch = 0.0
    if hasattr(src, "cost_per_1k_places"):
        est_cost_places = estimated_records / 1000.0 * src.cost_per_1k_places
        # Real measurement (80-lead Almaty run, 2026-05-12): 86% of candidates
        # had a company IG handle that triggered an `instagram-profile-scraper`
        # fetch during the pre-fetch batch. Each lookup ~ $2.30 / 1K.
        # Without this, the pre-flight check underestimates real spend ~50%.
        ig_lookup_rate = 0.86
        ig_cost_per_1k = 2.30
        est_cost_ig_batch = estimated_records * ig_lookup_rate / 1000.0 * ig_cost_per_1k
    est_cost = round(est_cost_places + est_cost_ig_batch, 2)
    balance = src.get_balance_usd() if hasattr(src, "get_balance_usd") else None

    conn = db_connect()
    already_in_db = _city_dedup_count(conn, city_slug, niche_slug)
    # Cross-niche dedup: businesses already known under OTHER niches in this
    # city will be silently skipped by the dedup step too — many salons in
    # 2GIS appear under multiple rubrics ("маникюр" + "парикмахерская" + etc).
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM leads WHERE city = ? AND niche != ?",
        (city_slug, niche_slug),
    ).fetchone()
    cross_niche_in_db = row["n"] if row else 0

    # Source the actor slug from data_sources.py rather than hardcoding it here,
    # so if we ever swap actors the source-status output stays in sync.
    if src.name == "apify":
        from data_sources import APIFY_2GIS_ACTOR
        endpoint = APIFY_2GIS_ACTOR.replace("~", "/")
    else:
        endpoint = "catalog.api.2gis.com/3.0/items"

    _emit({
        "data_source": src.name,
        "actor_or_endpoint": endpoint,
        "city": city_slug,
        "city_ru": city_ru,
        "niche": niche_slug,
        "niche_ru": niche_ru,
        "target_leads": target,
        "estimated_records_needed": estimated_records,
        "estimated_cost_usd": est_cost,
        "estimated_cost_breakdown_usd": {
            "twogis_places": round(est_cost_places, 2),
            "instagram_batch": round(est_cost_ig_batch, 2),
        },
        "apify_balance_remaining_usd": balance,
        "leads_already_in_db_for_slice": already_in_db,
        "leads_already_in_db_other_niches_same_city": cross_niche_in_db,
        "note": (
            "cross-niche overlap is silently skipped by dedup. Some businesses "
            "appear in 2GIS under multiple rubrics (e.g. салон красоты + "
            "маникюр + брови). The actual reduction depends on how often "
            "the upstream actor returns these duplicates for our query."
        ) if cross_niche_in_db > 0 else None,
    })


# ─────────────────────────────────────────────────────────────────────
# validate-sheet
# ─────────────────────────────────────────────────────────────────────

def cmd_validate_sheet(args):
    from sheets_writer import parse_sheet_id, validate_sheet
    sheet_id = parse_sheet_id(args.sheet)
    if not sheet_id:
        _emit({"ok": False, "error": "No sheet id/url provided."})
        return
    result = validate_sheet(sheet_id)
    result["sheet_id"] = sheet_id
    _emit(result)


# ─────────────────────────────────────────────────────────────────────
# status
# ─────────────────────────────────────────────────────────────────────

def cmd_status(args):
    conn = db_connect()
    s = db_stats(conn, city=args.city, niche=args.niche)
    _emit(s)


# ─────────────────────────────────────────────────────────────────────
# clean
# ─────────────────────────────────────────────────────────────────────

def cmd_clean(args):
    conn = db_connect()
    city_slug, _, _ = resolve_city(args.city)
    niche_slug, _, _ = resolve_niche(args.niche)
    n = db_clean(conn, city_slug, niche_slug)
    _emit({"deleted": n, "city": city_slug, "niche": niche_slug})


# ─────────────────────────────────────────────────────────────────────
# telegram-report
# ─────────────────────────────────────────────────────────────────────

def cmd_resume(args):
    """Append orphaned leads (sheet_row IS NULL) to the user-supplied Sheets.

    Use case: a previous `search` run hit an Apify balance limit or crashed
    after writing leads to SQLite but before (or during) the Sheets append.
    Those rows live in `leads.db` with sheet_row=NULL. This subcommand finds
    them for the city × niche slice and writes them out.
    """
    conn = db_connect()
    city_slug, city_ru, _ = resolve_city(args.city) if args.city else (None, None, None)
    niche_slug, _, niche_ru = resolve_niche(args.niche) if args.niche else (None, None, None)

    sql = "SELECT * FROM leads WHERE sheet_row IS NULL"
    params = []
    if city_slug:
        sql += " AND city = ?"; params.append(city_slug)
    if niche_slug:
        sql += " AND niche = ?"; params.append(niche_slug)
    sql += " ORDER BY discovered_at"
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        _emit({"status": "nothing_to_resume", "city": city_slug, "niche": niche_slug})
        return

    leads = []
    for r in rows:
        leads.append({
            "twogis_id": r["twogis_id"], "name": r["name"],
            "city": r["city"], "city_ru": (CITIES.get(r["city"], {}).get("name_ru", r["city"])),
            "niche": r["niche"], "niche_ru": (NICHES.get(r["niche"], {}).get("name_ru", r["niche"])),
            "address": r["address"], "twogis_url": r["twogis_url"],
            "contact_method": r["contact_method"],
            "phone": r["phone"], "phone_type": r["phone_type"],
            "owner_name": r["owner_name"], "owner_phone": r["owner_phone"],
            "owner_instagram": r["owner_instagram"],
            "owner_ig_source": r["owner_ig_source"] if "owner_ig_source" in r.keys() else "",
            "company_instagram": r["company_instagram"],
            "data_source": r["data_source"], "discovered_at": r["discovered_at"],
        })

    from sheets_writer import parse_sheet_id, append_leads
    sheet_id = parse_sheet_id(args.sheet)
    info = append_leads(sheet_id, leads)
    if info.get("first_row"):
        for offset, lead in enumerate(leads):
            conn.execute("UPDATE leads SET sheet_row = ? WHERE twogis_id = ?",
                         (info["first_row"] + offset, lead["twogis_id"]))
        conn.commit()

    _emit({
        "status": "resumed",
        "appended": info["appended"],
        "first_row": info.get("first_row"),
        "sheet_url": info.get("sheet_url", ""),
        "city": city_slug, "niche": niche_slug,
    })


def cmd_telegram_report(args):
    from _shared.telegram import send_telegram_text  # type: ignore
    with open(args.result) as f:
        summary = json.load(f)

    breakdown = summary.get("breakdown", {})
    size_bd = summary.get("size_breakdown", {})
    lines = [
        "📊 <b>2GIS Lead Gen</b>",
        f"Город: <b>{summary.get('city_ru', '—')}</b>",
        f"Ниша: <b>{summary.get('niche_ru', '—')}</b>",
        f"Статус: <b>{summary.get('status', '—')}</b>",
        f"Собрано: <b>{summary.get('leads_collected', 0)} / {summary.get('leads_target', 0)}</b>",
        f"  • с телефоном: {breakdown.get('phone', 0)}",
        f"  • с IG владельца (auto, нужна проверка): {breakdown.get('owner_ig', 0)}",
        f"  • с IG компании: {breakdown.get('company_ig', 0)}",
    ]
    if size_bd:
        lines.append(
            f"Размер: sweet_spot={size_bd.get('sweet_spot', 0)}, "
            f"micro={size_bd.get('micro', 0)}, large={size_bd.get('large', 0)}"
        )
    lines += [
        f"Apify spend: <b>${summary.get('apify_spend_usd', 0):.2f}</b>",
        f"Время: {summary.get('elapsed_seconds', 0)}s",
        f"Sheets: {summary.get('sheet_url', '')}",
    ]
    env = load_env()
    try:
        send_telegram_text(env, "\n".join(lines))
        _emit({"ok": True})
    except Exception as e:
        _emit({"ok": False, "error": str(e)})


# ─────────────────────────────────────────────────────────────────────
# search — the main pipeline
# ─────────────────────────────────────────────────────────────────────

def _enrich_one(biz, city_slug, city_ru, niche_slug, niche_ru, source_name,
                src, serper_key, ig_cache, size_thresholds, allowed_sizes):
    """Decide what to do with a single candidate. Pure function — returns
    {"lead": dict | None, "outcome": str, "size_estimate": str}.

    Outcomes:
      "phone"        — mobile in 2GIS card → outreach via call/WhatsApp
      "owner_ig"     — Serper found owner name + likely personal IG handle
      "company_ig"   — no mobile, no personal IG, but company IG available
      "website"      — has website → not our target
      "no_contact"   — neither phone nor IG → can't act on
      "size_filtered" — outside the company-size sweet spot (4-10 employees)

    Size filter runs FIRST so we don't waste expensive enrichment (Serper,
    IG bio fetches) on businesses that don't fit our target anyway.
    """
    # 0. Size filter (4-10 employees by default). Runs FIRST to save spend.
    size = estimate_size(biz, thresholds=size_thresholds)
    if size not in allowed_sizes:
        return {"lead": None, "outcome": "size_filtered", "size_estimate": size}

    # 1. Website check using pre-fetched IG profile when possible.
    ig_profile = ig_cache.get((biz.get("instagram") or "").lower()) if biz.get("instagram") else None
    web = check_website(biz, ig_profile=ig_profile)
    if web["has_website"]:
        return {"lead": None, "outcome": "website", "size_estimate": size}

    # 2. Tier "phone" — mobile from 2GIS card.
    phone_pick = pick_best_mobile(biz.get("phones", []))
    if phone_pick:
        return {
            "lead": _build_lead(biz, city_slug, city_ru, niche_slug, niche_ru, source_name,
                                phone=phone_pick["normalized"],
                                phone_type=phone_pick["type"],
                                contact_method="phone",
                                size_estimate=size),
            "outcome": "phone",
            "size_estimate": size,
        }

    # 3. Owner discovery via Serper — only run if there's at least a company IG
    #    or no contact at all (skipping Serper saves money when we already have
    #    nothing actionable to hand off anyway).
    if biz.get("instagram"):
        owner = find_owner(biz, city_ru, src, serper_key)
        # 3a. Tier "owner_ig" — got a personal handle from Serper.
        if owner.get("owner_instagram"):
            return {
                "lead": _build_lead(biz, city_slug, city_ru, niche_slug, niche_ru, source_name,
                                    owner_name=owner.get("owner_name", ""),
                                    owner_instagram=owner["owner_instagram"],
                                    owner_ig_source=owner.get("owner_ig_source", "serper-auto"),
                                    company_instagram=biz["instagram"],
                                    contact_method="owner_ig",
                                    size_estimate=size),
                "outcome": "owner_ig",
                "size_estimate": size,
            }
        # 3b. Tier "company_ig" — Serper may have given us just a name.
        return {
            "lead": _build_lead(biz, city_slug, city_ru, niche_slug, niche_ru, source_name,
                                owner_name=owner.get("owner_name", ""),
                                company_instagram=biz["instagram"],
                                contact_method="company_ig",
                                size_estimate=size),
            "outcome": "company_ig",
            "size_estimate": size,
        }

    # 4. No phone, no IG → manager can't act → drop.
    return {"lead": None, "outcome": "no_contact", "size_estimate": size}


def cmd_search(args):
    started = time.time()
    env = load_env()
    serper_key = env.get("SERPER_API_KEY", "").strip()
    telegram_enabled = not args.no_telegram

    src = get_data_source(env)
    city_slug, city_ru, country = resolve_city(args.city)
    niche_slug, niche_queries, niche_ru = resolve_niche(
        args.niche_query if args.niche_query else args.niche
    )
    target = args.count
    over_fetch = target * 4

    _log(f"source={src.name} city={city_ru} niche={niche_ru} target={target} "
         f"fetching={over_fetch} workers={args.workers}")

    # A4 — Pre-flight balance check. Stop early unless --force.
    # Estimate must include BOTH 2GIS places and Instagram batch lookups,
    # otherwise we systematically underestimate by ~50% (real-world: 80-lead
    # Almaty run had pre-flight estimate $0.90 but actual spend $1.38).
    if hasattr(src, "get_balance_usd") and hasattr(src, "cost_per_1k_places"):
        bal = src.get_balance_usd()
        est_places = over_fetch / 1000.0 * src.cost_per_1k_places
        est_ig = over_fetch * 0.86 / 1000.0 * 2.30  # ~86% candidates have IG (measured)
        est_cost = est_places + est_ig
        if bal is not None and bal < est_cost * 0.8 and not args.force:
            _emit({
                "status": "balance_warning",
                "estimated_cost_usd": round(est_cost, 2),
                "estimated_cost_breakdown_usd": {
                    "twogis_places": round(est_places, 2),
                    "instagram_batch": round(est_ig, 2),
                },
                "balance_remaining_usd": round(bal, 2),
                "advice": (
                    f"Apify free credit (${bal:.2f}) may run out before reaching {target} leads. "
                    f"Reduce --count to ~{int(target * bal / est_cost / 1.1)} "
                    f"or pass --force to proceed anyway."
                ),
            })
            sys.exit(4)

    # Step 1: pull a batch of candidates from the source.
    try:
        candidates = src.search(city_slug, niche_queries, over_fetch)
    except Exception as e:
        _emit({"status": "error", "stage": "source_search", "error": str(e)})
        sys.exit(2)

    _log(f"candidates fetched: {len(candidates)}")

    if args.max_spend is not None and hasattr(src, "cost_per_1k_places"):
        spend = len(candidates) / 1000.0 * src.cost_per_1k_places
        if spend > args.max_spend:
            _emit({"status": "aborted_spend",
                   "estimated_spend_usd": round(spend, 2),
                   "max_spend_usd": args.max_spend,
                   "candidates_fetched": len(candidates)})
            sys.exit(3)

    # Step 2: dedup against SQLite (allow shared conn across threads).
    conn = db_connect()
    conn.execute("PRAGMA journal_mode=WAL")  # play nicer with concurrent inserts
    before = len(candidates)
    candidates = [c for c in candidates if not is_known(conn, c["twogis_id"])]
    _log(f"after dedup: {len(candidates)} (skipped {before - len(candidates)} known)")

    # A2 — pre-fetch ALL company IG profiles in one Apify batch.
    handles = sorted({c["instagram"].lower() for c in candidates if c.get("instagram")})
    ig_cache = {}
    if handles and hasattr(src, "fetch_instagram_profiles"):
        _log(f"pre-fetching {len(handles)} company IG profiles in one batch...")
        try:
            ig_cache = src.fetch_instagram_profiles(handles)
            _log(f"IG batch done: {len(ig_cache)}/{len(handles)} profiles retrieved")
        except Exception as e:
            _log(f"IG batch failed (continuing without cache): {e}")

    _telegram_progress(
        f"🚀 <b>2GIS Lead Gen начат</b>\n"
        f"Город: {city_ru} | Ниша: {niche_ru} | Цель: {target}\n"
        f"Кандидатов: {len(candidates)} | IG batch: {len(ig_cache)}/{len(handles)}",
        telegram_enabled,
    )

    # Step 3: parallel enrichment.
    # Build size thresholds (niche default + per-niche overrides + CLI overrides).
    size_thresholds = thresholds_for_niche(niche_slug)
    if args.min_reviews is not None:
        size_thresholds["min_reviews"] = args.min_reviews
    if args.max_reviews is not None:
        size_thresholds["max_reviews"] = args.max_reviews
    if args.max_branches is not None:
        size_thresholds["max_branches"] = args.max_branches

    # Which size buckets are eligible. Default = sweet_spot only.
    allowed_sizes = {"sweet_spot"}
    if args.include_micro:
        allowed_sizes.add("micro")
    if args.include_large:
        allowed_sizes.update({"large", "large_chain"})
    if args.include_unknown:
        allowed_sizes.add("unknown")

    _log(f"size filter: allowed={sorted(allowed_sizes)} thresholds={size_thresholds}")

    collected = []
    counters = {"phone": 0, "owner_ig": 0, "company_ig": 0,
                "skipped_website": 0, "skipped_no_contact": 0,
                "skipped_size": 0, "processed": 0}
    size_breakdown = {"micro": 0, "sweet_spot": 0, "large": 0,
                      "large_chain": 0, "unknown": 0}
    state_lock = threading.Lock()
    db_lock = threading.Lock()
    stop_event = threading.Event()
    last_progress_at = [0]  # mutable so closures can update

    def _worker(biz):
        if stop_event.is_set():
            return None
        try:
            return _enrich_one(biz, city_slug, city_ru, niche_slug, niche_ru,
                               src.name, src, serper_key, ig_cache,
                               size_thresholds, allowed_sizes)
        except Exception as e:
            _log(f"worker failed for {biz.get('name','?')}: {e}")
            return {"lead": None, "outcome": "no_contact", "size_estimate": "unknown"}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, biz): biz for biz in candidates}
        for fut in as_completed(futures):
            res = fut.result()
            if res is None:
                continue
            outcome = res["outcome"]
            lead = res["lead"]
            size_est = res.get("size_estimate", "unknown")
            with state_lock:
                counters["processed"] += 1
                size_breakdown[size_est] = size_breakdown.get(size_est, 0) + 1
                if outcome == "website":
                    counters["skipped_website"] += 1
                elif outcome == "no_contact":
                    counters["skipped_no_contact"] += 1
                elif outcome == "size_filtered":
                    counters["skipped_size"] += 1
                elif lead is not None and len(collected) < target:
                    counters[outcome] += 1
                    collected.append(lead)
                    if not args.dry_run:
                        with db_lock:
                            insert_lead(conn, lead)
                    if len(collected) >= target:
                        stop_event.set()
                # Progress checkpoint every 25 processed or every 50 collected.
                proc = counters["processed"]
                if proc - last_progress_at[0] >= 25:
                    last_progress_at[0] = proc
                    _log(f"progress: processed={proc} collected={len(collected)}/{target} "
                         f"breakdown phone={counters['phone']} owner_ig={counters['owner_ig']} "
                         f"company_ig={counters['company_ig']} "
                         f"skipped_web={counters['skipped_website']} "
                         f"skipped_size={counters['skipped_size']} "
                         f"skipped_nc={counters['skipped_no_contact']}")
                if len(collected) > 0 and len(collected) % 50 == 0 and lead is not None:
                    _telegram_progress(
                        f"📈 {city_ru}/{niche_ru}: {len(collected)}/{target} лидов "
                        f"(📞 {counters['phone']} / 📷 {counters['owner_ig']} / 🏢 {counters['company_ig']})",
                        telegram_enabled,
                    )

    elapsed = int(time.time() - started)
    _log(f"enrichment done in {elapsed}s — collected={len(collected)}/{target}")

    # Step 4: write to Google Sheets (unless dry-run).
    sheet_info = {"appended": 0, "first_row": None, "tab": None, "sheet_url": ""}
    if collected and not args.dry_run and args.sheet:
        from sheets_writer import parse_sheet_id, append_leads
        sheet_id = parse_sheet_id(args.sheet)
        try:
            sheet_info = append_leads(sheet_id, collected)
            _log(f"appended {sheet_info['appended']} rows to sheet {sheet_id}")
            # Persist sheet_row back to SQLite for the rows we just wrote.
            if sheet_info.get("first_row"):
                for offset, lead in enumerate(collected):
                    conn.execute(
                        "UPDATE leads SET sheet_row = ? WHERE twogis_id = ?",
                        (sheet_info["first_row"] + offset, lead["twogis_id"]),
                    )
                conn.commit()
        except Exception as e:
            _log(f"Sheets append failed: {e}")
            sheet_info["error"] = str(e)

    # Apify spend = candidates fetched + IG profile lookups done.
    apify_spend = 0.0
    if isinstance(src, ApifyDataSource):
        apify_spend = round(len(candidates) / 1000.0 * src.cost_per_1k_places, 4)
        # IG lookups: rough — one per business with an IG handle that we hit.
        ig_lookups = sum(1 for b in candidates if b.get("instagram"))
        apify_spend += round(ig_lookups / 1000.0 * 2.30, 4)

    status = "success" if len(collected) >= target else "partial"

    summary = {
        "status": status,
        "city": city_slug,
        "city_ru": city_ru,
        "niche": niche_slug,
        "niche_ru": niche_ru,
        "leads_target": target,
        "leads_collected": len(collected),
        "breakdown": {
            "phone": counters["phone"],
            "owner_ig": counters["owner_ig"],
            "company_ig": counters["company_ig"],
        },
        "candidates_processed": counters["processed"],
        "candidates_with_website_skipped": counters["skipped_website"],
        "candidates_no_contact_skipped": counters["skipped_no_contact"],
        "candidates_size_filtered": counters["skipped_size"],
        "size_breakdown": size_breakdown,
        "size_thresholds_used": size_thresholds,
        "allowed_sizes": sorted(allowed_sizes),
        "apify_spend_usd": apify_spend,
        "elapsed_seconds": elapsed,
        "sheet_rows_appended": sheet_info["appended"],
        "sheet_url": sheet_info.get("sheet_url", ""),
        "data_source": src.name,
        "dry_run": bool(args.dry_run),
    }

    # Optionally write summary JSON to disk so telegram-report can read it.
    if args.summary_out:
        Path(args.summary_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    _emit(summary)


def _build_lead(biz, city_slug, city_ru, niche_slug, niche_ru, source_name, **extra) -> dict:
    return {
        "twogis_id": biz.get("twogis_id", ""),
        "name": biz.get("name", ""),
        "city": city_slug,
        "city_ru": city_ru,
        "niche": niche_slug,
        "niche_ru": niche_ru,
        "address": biz.get("address", ""),
        "twogis_url": biz.get("twogis_url", ""),
        "website": biz.get("website", ""),
        "has_website": False,
        "company_instagram": extra.get("company_instagram", biz.get("instagram", "")),
        "phone": extra.get("phone", ""),
        "phone_type": extra.get("phone_type", ""),
        "owner_name": extra.get("owner_name", ""),
        "owner_phone": extra.get("phone", "") if extra.get("contact_method") == "phone" and extra.get("owner_name") else "",
        "owner_instagram": extra.get("owner_instagram", ""),
        "owner_ig_source": extra.get("owner_ig_source", ""),
        "contact_method": extra["contact_method"],
        "data_source": source_name,
        "discovered_at": _now_iso(),
        "size_estimate": extra.get("size_estimate", "unknown"),
        "review_count": biz.get("review_count", 0),
        "rating_count": biz.get("rating_count", 0),
        "branch_count": biz.get("branch_count", 1),
    }


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(prog="2gis-lead-gen", description="2GIS lead gen pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sps = sub.add_parser("source-status")
    sps.add_argument("--city", required=True)
    sps.add_argument("--niche", required=True)
    sps.add_argument("--count", type=int, default=200)
    sps.set_defaults(func=cmd_source_status)

    sv = sub.add_parser("validate-sheet")
    sv.add_argument("--sheet", required=True)
    sv.set_defaults(func=cmd_validate_sheet)

    ss = sub.add_parser("status")
    ss.add_argument("--city", default=None)
    ss.add_argument("--niche", default=None)
    ss.set_defaults(func=cmd_status)

    sc = sub.add_parser("clean")
    sc.add_argument("--city", required=True)
    sc.add_argument("--niche", required=True)
    sc.set_defaults(func=cmd_clean)

    se = sub.add_parser("search")
    se.add_argument("--city", required=True)
    se.add_argument("--niche", required=True)
    se.add_argument("--count", type=int, default=200)
    se.add_argument("--sheet", default=None)
    se.add_argument("--niche-query", default=None,
                    help="Free-form niche query (overrides --niche slug lookup)")
    se.add_argument("--max-spend", type=float, default=None,
                    help="Hard cap on Apify spend in USD. Run aborts if exceeded.")
    se.add_argument("--workers", type=int, default=10,
                    help="Parallel enrichment workers (default 10). Cap at ~20 to stay within Serper rate limit.")
    se.add_argument("--force", action="store_true",
                    help="Skip pre-flight balance warning (proceed even if free credit may be exhausted).")
    se.add_argument("--no-telegram", action="store_true",
                    help="Disable Telegram progress notifications.")
    # Size-filter knobs (default: 4-10 employees = sweet_spot only).
    se.add_argument("--min-reviews", type=int, default=None,
                    help="Min 2GIS reviewsCount to be considered (default by niche: 10). "
                         "Lower = include smaller / newer businesses.")
    se.add_argument("--max-reviews", type=int, default=None,
                    help="Max 2GIS reviewsCount before being classified as 'large' "
                         "(default by niche: 300). Higher = include more established businesses.")
    se.add_argument("--max-branches", type=int, default=None,
                    help="Max branch count before classification as 'large_chain' "
                         "(default 3). Higher = include networks.")
    se.add_argument("--include-micro", action="store_true",
                    help="Include businesses classified as 'micro' (1-3 people). Off by default.")
    se.add_argument("--include-large", action="store_true",
                    help="Include businesses classified as 'large' or 'large_chain'. Off by default.")
    se.add_argument("--include-unknown", action="store_true",
                    help="Include businesses whose size could not be classified. Off by default.")
    se.add_argument("--dry-run", action="store_true")
    se.add_argument("--summary-out", default=None)
    se.set_defaults(func=cmd_search)

    sr = sub.add_parser("resume",
                        help="Append leads sitting in SQLite without a sheet_row to a Google Sheets.")
    sr.add_argument("--sheet", required=True, help="Sheet URL or ID to append the orphaned leads to.")
    sr.add_argument("--city", default=None, help="Optional city filter (slug).")
    sr.add_argument("--niche", default=None, help="Optional niche filter (slug).")
    sr.set_defaults(func=cmd_resume)

    st = sub.add_parser("telegram-report")
    st.add_argument("--result", required=True, help="Path to summary JSON")
    st.set_defaults(func=cmd_telegram_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

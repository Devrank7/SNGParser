#!/usr/bin/env python3.11
"""Gen Giper Msg CLI — hyper-personalized message generator.

Subcommands:
  validate  — confirm sheet access and add target columns
  generate  — read unmessaged leads, call Claude, write draft messages back
  status    — show counts of leads / messages by status
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from sheets_io import (  # type: ignore
    parse_sheet_id, validate_and_prepare,
    read_leads_without_message, write_message, load_sheet_context,
)
from personalize import build_user_prompt, channel_for  # type: ignore
from llm_generate import generate_for_lead  # type: ignore


def _log(msg: str):
    print(f"[run] {msg}", file=sys.stderr, flush=True)


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ─── validate ─────────────────────────────────────────────────────────────────

def cmd_validate(args):
    sid = parse_sheet_id(args.sheet)
    result = validate_and_prepare(sid)
    result["sheet_id"] = sid
    if result.get("ok"):
        # Trim large nested dicts from the report.
        result.pop("source_columns_mapped", None)
        result.pop("target_columns", None)
    _emit(result)


# ─── status ───────────────────────────────────────────────────────────────────

def cmd_status(args):
    sid = parse_sheet_id(args.sheet)
    info = validate_and_prepare(sid)
    if not info.get("ok"):
        _emit(info)
        return
    _emit({
        "sheet_id": sid,
        "tab": info["tab"],
        "total_leads": info["total_leads"],
        "leads_with_message": info["leads_with_message_already"],
        "leads_pending": info["leads_to_process"],
    })


# ─── generate ─────────────────────────────────────────────────────────────────

def cmd_generate(args):
    started = time.time()
    sid = parse_sheet_id(args.sheet)

    # Always re-validate first (idempotent, ensures target columns exist).
    val = validate_and_prepare(sid)
    if not val.get("ok"):
        _emit({"status": "error", "stage": "validate", **val})
        sys.exit(2)

    leads = read_leads_without_message(sid, limit=args.count, tier_filter=args.tier)
    if not leads:
        _emit({"status": "nothing_to_do", "message": "No leads without a message to process."})
        return

    _log(f"processing {len(leads)} leads, model={args.model}, workers={args.workers}, tier={args.tier}")

    # Load sheet metadata ONCE upfront — every `write_message` call would
    # otherwise re-read the header row, which exhausts Google's 60 read/min/user
    # quota on the first 60 leads of a parallel run. With this cache each write
    # is a single batchUpdate call (~80 API calls total instead of ~160).
    sheet_context = load_sheet_context(sid)

    breakdown = {"WhatsApp": 0, "Instagram DM": 0}
    failed = []
    sheet_lock = threading.Lock()
    counters = {"done": 0, "ok": 0}

    def _work(lead):
        user_prompt = build_user_prompt(lead)
        result = generate_for_lead(lead, user_prompt, model=args.model,
                                   max_attempts=args.max_attempts)
        return lead, result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_work, lead) for lead in leads]
        for fut in as_completed(futures):
            lead, result = fut.result()
            counters["done"] += 1
            if result["ok"]:
                counters["ok"] += 1
                channel = result["channel"]
                breakdown[channel] = breakdown.get(channel, 0) + 1
                with sheet_lock:
                    try:
                        write_message(sid, lead["row_number"], result["message"],
                                      channel=channel, status="draft",
                                      context=sheet_context)
                    except Exception as e:
                        _log(f"sheet write failed row {lead['row_number']}: {e}")
                        failed.append({
                            "row": lead["row_number"],
                            "business": lead.get("business_name"),
                            "reason": f"sheet write: {e}",
                        })
                        continue
                if counters["done"] % 5 == 0 or counters["done"] == len(leads):
                    _log(f"progress: {counters['done']}/{len(leads)} done, "
                         f"{counters['ok']} ok, {len(failed)} failed")
            else:
                failed.append({
                    "row": lead["row_number"],
                    "business": lead.get("business_name"),
                    "reason": result["validation_error"],
                })
                with sheet_lock:
                    try:
                        write_message(sid, lead["row_number"], "", channel=result["channel"],
                                      status="validation_failed",
                                      context=sheet_context)
                    except Exception:
                        pass

    elapsed = int(time.time() - started)
    _emit({
        "status": "success" if counters["ok"] == len(leads) else "partial",
        "model": args.model,
        "leads_processed": len(leads),
        "messages_generated": counters["ok"],
        "messages_failed_validation": len(failed),
        "breakdown_by_channel": breakdown,
        "failed_examples": failed[:5],
        "elapsed_seconds": elapsed,
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sid}/",
    })


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(prog="gen-giper-msg",
                                description="Hyper-personalized outreach generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="Verify sheet access and append target columns")
    v.add_argument("--sheet", required=True)
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("status", help="Show counts of leads / messages")
    s.add_argument("--sheet", required=True)
    s.set_defaults(func=cmd_status)

    g = sub.add_parser("generate", help="Generate draft messages for unmessaged leads")
    g.add_argument("--sheet", required=True)
    g.add_argument("--count", type=int, default=None,
                   help="Maximum number of leads to process (default: all unmessaged)")
    g.add_argument("--model", default="claude-sonnet-4-6",
                   help="Default claude-sonnet-4-6. Use claude-haiku-4-5 for cheaper batches.")
    g.add_argument("--workers", type=int, default=5,
                   help="Parallel claude CLI workers. Default 5. Keep low to avoid rate limits.")
    g.add_argument("--tier", default="all", choices=["all", "phone", "ig"],
                   help="Generate only for one channel batch.")
    g.add_argument("--max-attempts", type=int, default=3,
                   help="Max attempts per lead before marking validation_failed.")
    g.set_defaults(func=cmd_generate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

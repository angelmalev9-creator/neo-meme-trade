#!/usr/bin/env python3
"""Offline, read-only, decision-time A/B screening of observed PAPER entries.

Never changes strategy, state, trade history, settings, balances or service.
This is *not* a backtest of unobserved alternative fills or market trajectories.
"""
import argparse
from collections import deque
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Sofia")
COST_CAP_PCT = 2.0
LOSS_COUNT = 2
LOSS_LOOKBACK_MS = 6 * 60 * 60 * 1000
COOLDOWN_MS = 3 * 60 * 60 * 1000
LABEL = "OCT4_SHADOW_FILTERS_V1"


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def pool_key(row):
    # Never mix two different tokens that share the same ticker.
    mint = str(row.get("address") or "")
    pair = str(row.get("pairAddress") or "")
    return (mint, pair) if mint and pair else None


def cost_rejection(row, cap=COST_CAP_PCT):
    mark = number(row.get("entry_roundtrip_pnl_pct"))
    if mark is None:
        return "UNVERIFIABLE_PRE_ENTRY_COST"
    return "PRE_ENTRY_ROUNDTRIP_ABOVE_CAP" if mark < -cap else None


def recent_loss_brake(row, previously_closed, lookback_ms=LOSS_LOOKBACK_MS, cooldown_ms=COOLDOWN_MS):
    started = int(row.get("opened_at") or 0)
    key = pool_key(row)
    if not key or not started:
        return "UNVERIFIABLE_TOKEN_ID"
    recent = [v for v in previously_closed
              if pool_key(v) == key and v.get("closed_at")
              and 0 < started-int(v["closed_at"]) <= lookback_ms
              and not v.get("technical_voided")]
    recent.sort(key=lambda v: int(v["closed_at"]), reverse=True)
    # Only use past *closed* PAPER outcomes known at this entry time.
    if (len(recent) >= LOSS_COUNT
            and 0 < started-int(recent[0]["closed_at"]) <= cooldown_ms
            and all(number(v.get("pnl_usd")) is not None
                    and number(v.get("pnl_usd")) < 0 for v in recent[:LOSS_COUNT])):
        return "TWO_RECENT_LOSSES_SAME_POOL"
    return None


def score_subset(rows, accepted_ids):
    selected = [x for x in rows if str(x.get("id")) in accepted_ids]
    pnl = sum(number(x.get("pnl_usd")) or 0 for x in selected)
    return {"observed_entries": len(selected),
            "observed_wins": sum((number(x.get("pnl_usd")) or 0)>0 for x in selected),
            "observed_losses": sum((number(x.get("pnl_usd")) or 0)<0 for x in selected),
            "observed_recorded_pnl_usd": round(pnl, 4)}


def analyze(state, day="2026-11-05", session_id=None, since_ms=None):
    rows = list(state.get("history") or [])
    session_id = session_id or state.get("demo_session_id")
    timeline = sorted([r for r in rows if isinstance(r, dict) and r.get("session_id")==session_id
                       and r.get("opened_at") and r.get("closed_at")],
                      key=lambda r: (int(r["opened_at"]), str(r.get("id"))))
    base = [r for r in timeline if not r.get("technical_voided")]
    cutoff = datetime.fromisoformat(day).replace(tzinfo=TZ)
    end = cutoff + timedelta(days=1)
    start_ms, end_ms = int(cutoff.timestamp()*1000), int(end.timestamp()*1000)
    if since_ms is not None:
        start_ms = max(start_ms,int(since_ms))
    observed = [r for r in base if start_ms<=int(r["opened_at"])<end_ms]
    # Simulate as-of decisions using *only* closes strictly before each entry;
    # do not read its own result, future trades, or voided technical exits.
    history = sorted(base, key=lambda r: (int(r["closed_at"]), str(r.get("id"))))
    cursor = 0
    closed = deque()
    flags = {}
    for row in sorted(observed, key=lambda r: (int(r["opened_at"]), str(r.get("id")))):
        stamp = int(row["opened_at"])
        while cursor < len(history) and int(history[cursor]["closed_at"]) < stamp:
            closed.append(history[cursor])
            cursor += 1
        while closed and int(closed[0]["closed_at"]) < stamp-LOSS_LOOKBACK_MS:
            closed.popleft()
        flags[str(row.get("id"))] = {
            "cost": cost_rejection(row),
            "loss_brake": recent_loss_brake(row, closed),
        }
    baseline = {str(r.get("id")) for r in observed}
    if len(baseline) != len(observed) or any(not r.get("id") for r in observed):
        raise ValueError("Duplicate/missing observed trade IDs; refuse misleading comparison")
    def selected(controls):
        return {ident for ident, flag in flags.items() if all(not flag[name] for name in controls)}
    configurations = {
        "ORIGINAL_OBSERVED": baseline,
        "COST_CAP_2PCT": selected(("cost",)),
        "POOL_LOSS_BRAKE": selected(("loss_brake",)),
        "BOTH_SHADOW_FILTERS": selected(("cost","loss_brake")),
    }
    result = {
        "version": LABEL, "day": day, "timezone": "Europe/Sofia",
        "observation_start": datetime.fromtimestamp(start_ms/1000,TZ).isoformat(),
        "session_id": session_id, "input_closed_valid_trades": len(base),
        "original_strategy_modified": False, "execution": "OFFLINE_READONLY_PAPER",
        "assumptions": {
            "pre_entry_roundtrip_cost_cap_pct": COST_CAP_PCT,
            "loss_brake_min_recent_losses": LOSS_COUNT,
            "loss_brake_lookback_hours": LOSS_LOOKBACK_MS/3_600_000,
            "loss_brake_reentry_hours": COOLDOWN_MS/3_600_000,
            "technical_voids": "EXCLUDED from all comparisons and streaks",
            "time_order": "Only closed outcomes before each entry, never future PnL",
        },
        "cases": {},
        "rejection_counts": {},
        "observed_rejected_entries": [],
        "caveat": ("Screening already-observed PAPER entries, NOT a randomized experiment, "
                   "independent fill replay, or prediction of future profitability. "
                   "Excluding a historical loss does not make it a real avoided loss; "
                   "alternative entries and balances are not simulated."),
    }
    for label, ids in configurations.items():
        result["cases"][label] = {
            **score_subset(observed,ids),
            "observed_excluded_entries": len(observed)-len(ids),
        }
    for name in ("cost","loss_brake"):
        result["rejection_counts"][name] = {
            label: sum(flags[ident][name]==label for ident in flags)
            for label in sorted({f[name] for f in flags.values() if f[name]})
        }
    for row in observed:
        ident=str(row.get("id"));rejected=[v for v in flags[ident].values() if v]
        if rejected:
            result["observed_rejected_entries"].append({
                "trade_no":row.get("trade_no"),"symbol":row.get("symbol"),
                "reasons":rejected,"recorded_pnl_usd":round(number(row.get("pnl_usd")) or 0,4)
            })
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state",type=Path,required=True)
    ap.add_argument("--day",default="2026-11-05")
    ap.add_argument("--output",type=Path)
    ap.add_argument("--since",help="Optional observation cutoff, ISO date/time in Sofia unless timezone specified")
    args=ap.parse_args()
    raw=args.state.read_bytes()
    since_ms=None
    if args.since:
        stamp=datetime.fromisoformat(args.since)
        since_ms=int(stamp.replace(tzinfo=TZ).timestamp()*1000) if stamp.tzinfo is None else int(stamp.timestamp()*1000)
    report=analyze(json.loads(raw),args.day,since_ms=since_ms)
    report["input_sha256"]=hashlib.sha256(raw).hexdigest()
    rendered=json.dumps(report,ensure_ascii=False,indent=2)+"\n"
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(rendered)
    print(rendered)


if __name__=="__main__":main()

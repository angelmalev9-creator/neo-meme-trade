#!/usr/bin/env python3
"""Read-only PAPER archive audit and incremental execution-friction sensitivity.

Does not reconstruct fills, alter trading state or predict live profitability.
Usage: python3 scripts/oct4_paper_audit.py HISTORY.json --day 2026-10-04
"""
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


def audit(source, day="2026-10-04", timezone="Europe/Sofia"):
    obj = json.loads(Path(source).read_text())
    rows = obj if isinstance(obj, list) else (obj.get("history") or obj.get("trades") or [])
    local = ZoneInfo(timezone)
    day_begin = dt.datetime.fromisoformat(day).replace(tzinfo=local)
    start = day_begin.timestamp() * 1000
    end = (day_begin + dt.timedelta(days=1)).timestamp() * 1000
    deals = [r for r in rows if isinstance(r, dict) and start <= float(r.get("closed_at") or 0) < end]
    deals.sort(key=lambda r: float(r.get("closed_at") or 0))
    def num(v):
        try:
            f = float(v)
            return f if math.isfinite(f) else 0
        except (ValueError, TypeError):
            return 0
    def agg(arr):
        gross = sum(num(r.get("pnl_usd")) for r in arr)
        wins = sum(num(r.get("pnl_usd")) > 0 for r in arr)
        losses = sum(num(r.get("pnl_usd")) < 0 for r in arr)
        return {"trades": len(arr), "net_pnl_usd": round(gross, 2), "wins": wins,
                "losses": losses, "flats": len(arr) - wins - losses}
    grouped = lambda key: {str(k): agg(v) for k, v in sorted(
        ((k, [r for r in deals if (r.get(key) or "UNRECORDED") == k]) for k in
         set((r.get(key) or "UNRECORDED") for r in deals)),
        key=lambda kv: -len(kv[1]))}
    legacy = [r for r in deals if not r.get("execution_mode")]
    quote = [r for r in deals if r.get("execution_mode") == "JUPITER_QUOTE_V2"]
    # Each stress figure is an EXTRA cost beyond reported archived PnL.
    # Never claim to reconstruct buy/sell quotes from trade summaries.
    sensitivities = {}
    for pct in (0.25, 0.5, 1, 2, 2.5, 3, 5):
        extra = sum(num(r.get("notional_usd")) * pct / 100 for r in legacy)
        sensitivities[f"{pct:g}%"] = {
            "extra_roundtrip_cost_legacy_usd": round(extra, 2),
            "combined_paper_pnl_after_extra_cost_usd": round(agg(deals)["net_pnl_usd"]-extra, 2),
        }
    total_notional = sum(num(r.get("notional_usd")) for r in legacy)
    duplicate_ids = len(deals) - len(set(str(r.get("id")) for r in deals))
    spans = {}
    for key in set(str(r.get("session_id") or "UNRECORDED") for r in deals):
        selected = [r for r in deals if str(r.get("session_id") or "UNRECORDED") == key]
        spans[key] = {"first_close": dt.datetime.fromtimestamp(selected[0]["closed_at"]/1000, local).isoformat(),
                      "last_close": dt.datetime.fromtimestamp(selected[-1]["closed_at"]/1000, local).isoformat(),
                      **agg(selected)}
    return {
        "day": day, "timezone": timezone, "recorded": agg(deals),
        "unique_ids": len(deals)-duplicate_ids, "duplicate_ids": duplicate_ids,
        "execution_modes": grouped("execution_mode"),
        "exit_reasons": grouped("exit_reason"),
        "strategy_ids": grouped("strategy_id"),
        "sessions": spans,
        "jupiter_recorded_exit_quotes": sum(bool(r.get("jupiter_exit_quote_at")) for r in deals),
        "entries_with_recorded_flow_quality": sum(bool((r.get("entry_flow") or {}).get("quality")) for r in deals),
        "legacy_notional_usd": round(total_notional, 2),
        "additional_roundtrip_cost_to_erase_reported_net_pct_on_legacy": round(100*agg(deals)["net_pnl_usd"]/total_notional, 3) if total_notional else None,
        "illustrative_incremental_roundtrip_friction": sensitivities,
        "caution": "Mixed PAPER sessions and execution models; scenario costs are incremental assumptions, not verified historical fills.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("--day", default="2026-10-04")
    args = parser.parse_args()
    print(json.dumps(audit(args.history, args.day), indent=2, ensure_ascii=False))

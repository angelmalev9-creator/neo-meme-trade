"""One-time, auditable correction for confirmed NOV 5 stale-cache PAPER exits.

Never touches real wallets, exchanges or legacy all-time history.
Dry-run by default. Apply ONLY when the PAPER service is stopped and backed up.
"""
import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

CORRECTION_ID = "TECH-VOID-STALE-2026-11-05-OCT4-FRESHNESS"
CUTOFF_MS = 1793871233000  # Prior to 2026-11-05 PAPER fix rollout
EXPECTED_COUNT = 26
EXPECTED_CREDIT = 128.68161534
TARGET_DAY = "2026-11-05"
TARGET_REASON = "STALE_MARKET_EXIT"
TARGET_SESSION = "BUY-PRESSURE-3000-20261009-210444"


def select_eligible(history):
    local = ZoneInfo("Europe/Sofia")
    start = int(dt.datetime(2026, 11, 5, tzinfo=local).timestamp() * 1000)
    end = int(dt.datetime(2026, 11, 6, tzinfo=local).timestamp() * 1000)
    rows = [t for t in history
            if isinstance(t, dict) and start <= int(t.get("closed_at") or 0) < end
            and int(t.get("closed_at") or 0) < CUTOFF_MS
            and t.get("session_id") == TARGET_SESSION
            and t.get("exit_reason") == TARGET_REASON]
    return sorted(rows, key=lambda x: str(x["id"]))


def create_correction(history, existing):
    if any(c.get("id") == CORRECTION_ID for c in existing):
        return None
    rows = select_eligible(history)
    if len(rows) != EXPECTED_COUNT:
        raise ValueError(f"Expected {EXPECTED_COUNT} bugged exits, found {len(rows)}; abort")
    ids = [t.get("id") for t in rows]
    if len(set(ids)) != EXPECTED_COUNT or any(not i for i in ids):
        raise ValueError("Duplicate/missing trade IDs")
    if any(t.get("technical_voided") for t in rows):
        raise ValueError("Some trades are already voided")
    if any(not isinstance(t.get("pnl_usd"), (int, float)) or not math.isfinite(t["pnl_usd"])
           or t["pnl_usd"] >= 0 for t in rows):
        raise ValueError("Nonnegative or invalid bugged exit PnL")
    amount = round(-sum(float(t["pnl_usd"]) for t in rows), 8)
    if not math.isclose(amount, EXPECTED_CREDIT, abs_tol=0.00000001):
        raise ValueError(f"Unexpected reversal {amount}; abort")
    return {"id": CORRECTION_ID, "paper_only": True, "reason": "BUG_STALE_MARKET_PIN",
            "day": TARGET_DAY, "trade_count": len(ids), "voided_trade_ids": ids,
            "original_pnl_usd": round(-amount, 8), "amount_usd": amount,
            "description": "Technical void of stale-cache PAPER exits; original audit and fills retained."}


def apply_flags(history, correction, stamp):
    ids = set(correction["voided_trade_ids"])
    return [dict(row, technical_voided=True,
                 technical_void_reason=correction["reason"],
                 technical_void_at=stamp, technical_void_correction_id=correction["id"])
            if row.get("id") in ids else row for row in history]


def run(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args=parser.parse_args(argv)
    state=json.loads(args.state.read_text(encoding="utf-8"))
    if not isinstance(state,dict) or state.get("positions"):
        raise SystemExit("Refusing correction while positions exist")
    existing=state.get("paper_corrections") or []
    correction=create_correction(state.get("history") or [], existing)
    if correction is None:
        print(json.dumps({"status":"already_applied","correction_id":CORRECTION_ID}))
        return
    correction.update(balance_before=state["demo_balance_usd"],
                      balance_after=round(state["demo_balance_usd"]+correction["amount_usd"],8))
    print(json.dumps({"status":"dry_run" if not args.apply else "applying",
                      "correction":correction},ensure_ascii=False))
    if not args.apply:return
    active=subprocess.run(["systemctl","is-active","neo-user-angel-paper.service"],
                          capture_output=True,text=True,check=False)
    if active.stdout.strip() != "inactive":
        raise SystemExit("PAPER engine must be stopped before applying")
    os.environ.update(NEO_MARKET_STATE_PATH=str(args.state),
                      NEO_MARKET_AUDIT_PATH=str(args.audit),
                      NEO_EXECUTION_MODE="PAPER",NEO_ENGINE_MODE="PAPER",
                      NEO_STRATEGY_PROFILE="ORDER_FLOW_ADAPTIVE_OCT4")
    backend=str(Path(__file__).resolve().parent.parent/"backend")
    if backend not in sys.path: sys.path.insert(0,backend)
    import market_monitor as m
    m.STATE.load()
    if m.STATE.positions:raise SystemExit("Positions appeared; abort")
    if create_correction(m.STATE.history,m.STATE.paper_corrections) is None:
        raise SystemExit("Correction already applied, no changes")
    now=m.now_ms()
    correction["created_at"] = now
    corrected=apply_flags(m.STATE.history,correction,now)
    m.STATE.commit("PAPER_TECHNICAL_REVERSAL",
                   {"id": CORRECTION_ID,"amount_usd":correction["amount_usd"],
                    "reason":correction["reason"],"voided_trade_ids":correction["voided_trade_ids"],
                    "original_pnl_usd":correction["original_pnl_usd"],
                    "balance_before":correction["balance_before"],
                    "balance_after":correction["balance_after"]},
                   history=corrected,
                   paper_corrections=[*m.STATE.paper_corrections,correction],
                   demo_balance_usd=correction["balance_after"])
    if m.STATE.pending_audit:
        raise RuntimeError("Audit outbox was not committed; leave PAPER service stopped")
    print(json.dumps({"status":"applied","balance":m.STATE.demo_balance_usd,
                      "voided":len(correction["voided_trade_ids"]),
                      "reversal_usd":correction["amount_usd"],"audit_status":m.STATE.audit_status}))

if __name__=="__main__":run()

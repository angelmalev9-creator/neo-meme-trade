#!/usr/bin/env python3
"""NEO Selective Early — independent PAPER *mark-based* shadow strategy.

This is a forward-only, deterministic educational experiment consuming the
existing GeckoTerminal/DexScreener observation archive. It has NO RPC signing,
wallets, swaps, executable quotes, or interactions with the production engine.
Indicative chart prices and cost haircuts do not prove executable PnL.

Two predefined shadow variants share the same candidate and open-time evidence
but differ in net, modeled exit targets (+5% and +10%). Both keep every loss,
unresolved position and unobserved exit explicitly.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import time

VERSION = "NEO_SELECTIVE_EARLY_SHADOW_PAPER_V1"
VARIANTS = {"NET_TARGET_5": 5.0, "NET_TARGET_10": 10.0}
START_BALANCE = 1000.0
NOTIONAL = 200.0
MIN_LIQUIDITY = 20000.0
MAX_CREATION_AGE_MS = 150000
MIN_EVIDENCE_GAP_MS = 18000
MAX_EVIDENCE_GAP_MS = 90000
MAX_HOLD_MS = 4 * 60000
MAX_LIQUIDITY_SHARE = 0.01
BASE_COST_PCT_PER_LEG = 0.55
NETWORK_FEE_USD_PER_LEG = 0.15
MAX_ENTRY_BASE_MOVE_PCT = 12
STOP_NET_PCT = -5.0
TWO_LOSS_PAUSE_MS = 60 * 60000
SOURCE = "/var/lib/neo-early-paper/observations.sqlite3"
OUTPUT = "/var/lib/neo-shadow-paper"


def finite(n):
    try:
        x = float(n)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def estimate_leg_cost_pct(size, liquidity):
    """Transparent, conservative illustrative friction; NOT a swap quote."""
    if size <= 0 or finite(liquidity) is None or liquidity <= 0:
        return None
    return BASE_COST_PCT_PER_LEG + 100.0 * size / liquidity


def candidate(born, baseline, category, prev_ts, prev_price, prev_liq,
              now_ts, price, liquidity):
    """Only past-and-present data. Never examines any later price."""
    if category != "early_activity_unverified":
        return "independent_wallet_activity_unverified"
    if now_ts - born < 0 or now_ts - born > MAX_CREATION_AGE_MS:
        return "not_new_enough"
    gap = now_ts - prev_ts
    if gap < MIN_EVIDENCE_GAP_MS or gap > MAX_EVIDENCE_GAP_MS:
        return "unconfirmed_adjacent_marks"
    if any(finite(v) is None or v <= 0 for v in (baseline,prev_price,price,prev_liq,liquidity)):
        return "missing_mark_or_liquidity"
    if min(prev_liq,liquidity) < MIN_LIQUIDITY or NOTIONAL > min(prev_liq,liquidity)*MAX_LIQUIDITY_SHARE:
        return "insufficient_liquidity"
    move = 100*(price/prev_price - 1)
    relative_to_start = 100*(price/baseline - 1)
    if move < 1.0 or move > 5.0 or relative_to_start < 0 or relative_to_start > MAX_ENTRY_BASE_MOVE_PCT:
        return "not_confirmed_moderate_growth"
    return None


def modeled_exit_cash(entry_price, entry_liq, current_price, current_liq, size=NOTIONAL):
    """Conservative *mark-to-mark* hypothetical net value, never a fill."""
    if any(finite(z) is None or z <= 0 for z in (entry_price,entry_liq,current_price,current_liq,size)):
        return None
    if current_liq < MIN_LIQUIDITY or size > current_liq*MAX_LIQUIDITY_SHARE:
        return None
    buy_cost = estimate_leg_cost_pct(size,entry_liq)
    sell_cost = estimate_leg_cost_pct(size,current_liq)
    if buy_cost is None or sell_cost is None or sell_cost >= 100:
        return None
    units = (size-NETWORK_FEE_USD_PER_LEG)/(entry_price*(1+buy_cost/100))
    return max(0.0,units*current_price*(1-sell_cost/100)-NETWORK_FEE_USD_PER_LEG)


def connect_shadow(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=20)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=20000")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS last_marks(
      pool TEXT PRIMARY KEY, ts INTEGER NOT NULL, price REAL NOT NULL, liq REAL
    );
    CREATE TABLE IF NOT EXISTS attempted(
      variant TEXT NOT NULL, pool TEXT NOT NULL, reason TEXT,
      at_ms INTEGER NOT NULL, PRIMARY KEY(variant,pool)
    );
    CREATE TABLE IF NOT EXISTS positions(
      variant TEXT PRIMARY KEY, pool TEXT NOT NULL, symbol TEXT,
      opened_ms INTEGER NOT NULL, entry_price REAL NOT NULL,
      entry_liq REAL NOT NULL, size_usd REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS closed(
      id INTEGER PRIMARY KEY AUTOINCREMENT, variant TEXT NOT NULL,
      pool TEXT NOT NULL, symbol TEXT, opened_ms INTEGER NOT NULL,
      closed_ms INTEGER NOT NULL, entry_price REAL NOT NULL,
      exit_price REAL, net_mark_pnl_usd REAL, net_mark_pct REAL,
      reason TEXT NOT NULL, quality TEXT NOT NULL
    );
    """)
    db.commit()
    return db


def getmeta(db,key,default=None):
    r=db.execute("SELECT value FROM meta WHERE name=?",(key,)).fetchone()
    return json.loads(r["value"]) if r else default


def setmeta(db,key,value):
    db.execute("INSERT INTO meta(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
               (key,json.dumps(value)))


def init_shadow(db,src,replay_start_ms=None):
    if getmeta(db,"version") is not None:
        return
    if replay_start_ms is None:
        cursor=src.execute("SELECT coalesce(max(rowid),0) FROM marks").fetchone()[0]
        start_ms=int(time.time()*1000)
        mode="FORWARD_ONLY_FROM_START"
    else:
        cursor=0
        start_ms=int(replay_start_ms)
        mode="HISTORICAL_EVENT_TIME_REPLAY_NOT_VALIDATION"
    with db:
        setmeta(db,"version",VERSION)
        setmeta(db,"cursor",cursor)
        setmeta(db,"started_ms",start_ms)
        setmeta(db,"run_mode",mode)
        setmeta(db,"read_rows",0)
        for variant in VARIANTS:
            setmeta(db,"balance_"+variant,START_BALANCE)
            setmeta(db,"loss_streak_"+variant,0)
            setmeta(db,"pause_until_"+variant,0)


def close_position(db,variant,pos,ts,price,liq,reason):
    net=modeled_exit_cash(pos["entry_price"],pos["entry_liq"],price,liq,pos["size_usd"])
    if net is None:
        # The mark is not realizably executable. Keep the unresolved
        # outcome in the denominator, NEVER impute 0 or a winning fill.
        pnl=None
        pct=None
        quality="UNVERIFIED_EXIT_NO_LIQUIDITY_OR_MARK"
        reason="UNRESOLVED_"+reason
    else:
        pnl=net-pos["size_usd"]
        pct=100*pnl/pos["size_usd"]
        quality="MODELED_DEX_MARK_WITH_HAIRCUT_NO_EXECUTABLE_QUOTE"
        setmeta(db,"balance_"+variant,getmeta(db,"balance_"+variant,START_BALANCE)+pnl)
        streak=(getmeta(db,"loss_streak_"+variant,0)+1) if pnl<0 else 0
        setmeta(db,"loss_streak_"+variant,streak)
        if streak>=2:
            setmeta(db,"pause_until_"+variant,ts+TWO_LOSS_PAUSE_MS)
    db.execute("""INSERT INTO closed(variant,pool,symbol,opened_ms,closed_ms,
                 entry_price,exit_price,net_mark_pnl_usd,net_mark_pct,reason,quality)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (variant,pos["pool"],pos["symbol"],pos["opened_ms"],ts,
                pos["entry_price"],price,pnl,pct,reason,quality))
    db.execute("DELETE FROM positions WHERE variant=?",(variant,))


def process_mark(db,row):
    pool=row["pool"]
    now=row["ts"]
    price=finite(row["price_usd"])
    liq=finite(row["liq"])
    prev=db.execute("SELECT ts,price,liq FROM last_marks WHERE pool=?",(pool,)).fetchone()
    if price is None or price<=0 or (liq is not None and liq < 0):
        return
    for variant,target in VARIANTS.items():
        pos=db.execute("SELECT * FROM positions WHERE variant=?",(variant,)).fetchone()
        if pos and pos["pool"]==pool and now>pos["opened_ms"]:
            value=modeled_exit_cash(pos["entry_price"],pos["entry_liq"],price,liq,pos["size_usd"])
            pct=100*(value-pos["size_usd"])/pos["size_usd"] if value is not None else None
            expired=now-pos["opened_ms"]>=MAX_HOLD_MS
            # Never claim target/stop if sale cannot be modeled.
            if pct is not None and (pct>=target or pct<=STOP_NET_PCT):
                close_position(db,variant,pos,now,price,liq,
                               "NET_MARK_TARGET" if pct>=target else "NET_MARK_STOP")
            elif expired:
                close_position(db,variant,pos,now,price,liq,"MAX_HOLD")
            continue
        if pos:
            continue
        if now<getmeta(db,"pause_until_"+variant,0):
            continue
        if getmeta(db,"balance_"+variant,START_BALANCE)<NOTIONAL:
            continue
        exists=db.execute("SELECT 1 FROM attempted WHERE variant=? AND pool=?",
                          (variant,pool)).fetchone()
        if exists:
            continue
        if prev is None:
            continue
        reason=candidate(row["born_ms"],row["baseline_price"],row["category"],
                         prev["ts"],prev["price"],prev["liq"],now,price,liq)
        if reason is not None:
            # Record only definitive invalid cohorts; later price evidence
            # can turn a not-yet-confirmed candidate into one.
            if reason in ("independent_wallet_activity_unverified","not_new_enough"):
                db.execute("INSERT OR IGNORE INTO attempted VALUES(?,?,?,?)",
                           (variant,pool,reason,now))
            continue
        db.execute("INSERT OR IGNORE INTO attempted VALUES(?,?,?,?)",
                   (variant,pool,"SHADOW_MARK_OPEN",now))
        db.execute("""INSERT INTO positions(variant,pool,symbol,opened_ms,entry_price,entry_liq,size_usd)
                      VALUES(?,?,?,?,?,?,?)""",
                   (variant,pool,row["symbol"],now,price,liq,NOTIONAL))
    db.execute("""INSERT INTO last_marks(pool,ts,price,liq) VALUES(?,?,?,?)
                  ON CONFLICT(pool) DO UPDATE SET
                  ts=excluded.ts,price=excluded.price,liq=excluded.liq""",
               (pool,now,price,liq))


def replay_batch(src,db,max_rows=3000):
    cursor=getmeta(db,"cursor",0)
    rows=src.execute("""
        SELECT m.rowid AS rid,m.pool AS pool,m.received_ms AS ts,
               m.price_usd AS price_usd,m.liquidity_usd AS liq,
               p.created_ms AS born_ms,p.symbol AS symbol,
               p.baseline_price AS baseline_price,
               p.initial_category AS category
        FROM marks m JOIN pools p ON m.pool=p.pool
        WHERE m.rowid > ? ORDER BY m.rowid LIMIT ?""",(cursor,max_rows)).fetchall()
    if not rows:
        return 0
    start_ms=getmeta(db,"started_ms",0)
    with db:
        for row in rows:
            if row["ts"]>=start_ms:
                process_mark(db,row)
        setmeta(db,"cursor",rows[-1]["rid"])
        setmeta(db,"read_rows",getmeta(db,"read_rows",0)+len(rows))
    return len(rows)


def expire_unobserved_positions(db,now):
    """A missing pool or exit mark is *unresolved*, not a fabricated fill.

    Releases the shadow slot only after explicitly retaining the unpriced
    outcome; this prevents an unobserved illiquid position from silently
    disappearing or freezing the entire shadow experiment forever.
    """
    with db:
        for row in db.execute("SELECT * FROM positions").fetchall():
            if now-row["opened_ms"] >= MAX_HOLD_MS+2*60000:
                close_position(db,row["variant"],row,now,None,None,"NO_SELL_MARK")


def report(db,now):
    result={"version":VERSION,"mode":getmeta(db,"run_mode"),"generated_at_utc":
            datetime.fromtimestamp(now/1000,timezone.utc).isoformat(),
            "started_ms":getmeta(db,"started_ms"),"cursor":getmeta(db,"cursor"),
            "market_events_read":getmeta(db,"read_rows",0),
            "orders_created":0,"realized_money_usd":0,"execution":"NONE",
            "caveat":"Mark-to-mark hypothetical returns; indicative DEX price and liquidity are not executable buy/sell quotes. Unresolved positions/outcomes cannot be called wins. No real-money strategy enabled.",
            "variants":{}}
    for variant,target in VARIANTS.items():
        rows=db.execute("SELECT * FROM closed WHERE variant=?",(variant,)).fetchall()
        settled=[x for x in rows if x["net_mark_pnl_usd"] is not None]
        unresolved=[x for x in rows if x["net_mark_pnl_usd"] is None]
        w=sum(x["net_mark_pnl_usd"]>0 for x in settled)
        losses=sum(x["net_mark_pnl_usd"]<0 for x in settled)
        gains=sum(x["net_mark_pnl_usd"] for x in settled)
        pos=db.execute("SELECT pool,opened_ms,symbol FROM positions WHERE variant=?",(variant,)).fetchone()
        attempts=db.execute("SELECT count(*) FROM attempted WHERE variant=?",(variant,)).fetchone()[0]
        result["variants"][variant]={
            "mark_target_pct":target,"mark_stop_pct":STOP_NET_PCT,
            "completed":len(settled),"mark_wins":w,"mark_losses":losses,
            "unresolved":len(unresolved),"open_unverified":1 if pos else 0,
            "mark_net_model_pnl_usd":round(gains,4),
            "virtual_mark_balance_usd":round(getmeta(db,"balance_"+variant,START_BALANCE),4),
            "win_rate_among_resolvable_pct":round(100*w/len(settled),2) if settled else None,
            "pause_until":getmeta(db,"pause_until_"+variant,0),
            "unique_pools_attempted":attempts,
            "reasons":dict(Counter(row["reason"] for row in rows)),
            "current_position":dict(pos) if pos else None,
        }
    return result


def atomic_report(path,body):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    with tmp.open("w",encoding="utf-8") as f:
        json.dump(body,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,path)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",default=SOURCE)
    ap.add_argument("--db",default=OUTPUT+"/state.sqlite3")
    ap.add_argument("--report",default=OUTPUT+"/report.json")
    ap.add_argument("--replay-start-ms",type=int)
    ap.add_argument("--interval",type=int,default=20)
    ap.add_argument("--once",action="store_true")
    args=ap.parse_args()
    if args.replay_start_ms is not None and "neo-shadow-paper" in args.db:
        raise SystemExit("Historical replay requires a separate temporary DB path")
    src=sqlite3.connect("file:"+str(Path(args.source).resolve())+"?mode=ro",uri=True,timeout=20)
    src.row_factory=sqlite3.Row
    db=connect_shadow(args.db)
    init_shadow(db,src,args.replay_start_ms)
    while True:
        total=0
        while True:
            n=replay_batch(src,db)
            total+=n
            if n<3000 or total>25000: break
        now=int(time.time()*1000)
        if getmeta(db,"run_mode") == "FORWARD_ONLY_FROM_START":
            expire_unobserved_positions(db,now)
        atomic_report(args.report,report(db,now))
        if args.once: break
        time.sleep(max(10,args.interval))
    db.close()
    src.close()


if __name__=="__main__":
    main()

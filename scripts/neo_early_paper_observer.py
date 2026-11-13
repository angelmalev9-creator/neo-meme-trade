#!/usr/bin/env python3
"""NEO EARLY SOLANA PAPER OBSERVER — read-only, quote-mark research.

Runs outside NEO's trading engine. NO keys, swap creation/signing,
order routing, candidate promotion, or executable financial positions.
Observed price thresholds (+5%/+10%) are NOT TP fills or profits.

Providers:
  GeckoTerminal's Solana new-pools feed for unbiased discovery.
  DexScreener exact-pool batch endpoint for post-discovery quote marks.

Persisted via SQLite. Report JSON is written atomically. Run unprivileged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import time
from typing import Any

import requests

VERSION = "NEO_EARLY_POOL_MARK_OBSERVER_V1"
GECKO = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1"
DEX = "https://api.dexscreener.com/latest/dex/pairs/solana/"
BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
WATCH_AGE_MS = 10 * 60_000
RECORD_MS = 15 * 60_000
GRACE_MS = 90_000
HORIZONS = (1, 5, 15)
THRESHOLDS = (5, 10)
MAX_POOLS_PER_BATCH = 30
POLL_SECONDS = 20
STOP = False


def number(value: Any) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def positive(value: Any) -> float | None:
    n = number(value)
    return n if n is not None and n > 0 else None


def integer_count(value: Any) -> int | None:
    n = number(value)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def label_candidate(attrs: dict) -> str:
    change = number((attrs.get("price_change_percentage") or {}).get("m5"))
    tx = (attrs.get("transactions") or {}).get("m5") or {}
    buys = integer_count(tx.get("buys"))
    sells = integer_count(tx.get("sells"))
    if change is not None and abs(change) >= 25:
        return "sharp_move_review"
    if buys is not None and sells is not None and buys + sells >= 20:
        if buys > 5 * max(1, sells) or sells > 5 * max(1, buys):
            return "sharp_move_review"
        return "early_activity_unverified"
    return "insufficient_evidence"


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("""
      CREATE TABLE IF NOT EXISTS pools(
        pool TEXT PRIMARY KEY, mint TEXT NOT NULL, symbol TEXT NOT NULL,
        created_ms INTEGER NOT NULL, first_seen_ms INTEGER NOT NULL,
        baseline_price REAL, baseline_liquidity REAL,
        initial_category TEXT NOT NULL, initial_price_source TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'GECKO_TERMINAL_NEW_POOLS'
      )
    """)
    db.execute("""
      CREATE TABLE IF NOT EXISTS marks(
        pool TEXT NOT NULL, received_ms INTEGER NOT NULL,
        price_usd REAL NOT NULL, liquidity_usd REAL,
        PRIMARY KEY(pool, received_ms),
        FOREIGN KEY(pool) REFERENCES pools(pool)
      )
    """)
    db.execute("""
      CREATE TABLE IF NOT EXISTS horizons(
        pool TEXT NOT NULL, minutes INTEGER NOT NULL,
        state TEXT NOT NULL, received_ms INTEGER, price_usd REAL,
        change_pct REAL, PRIMARY KEY(pool, minutes),
        FOREIGN KEY(pool) REFERENCES pools(pool)
      )
    """)
    db.execute("""
      CREATE TABLE IF NOT EXISTS target_marks(
        pool TEXT NOT NULL, threshold_pct INTEGER NOT NULL,
        received_ms INTEGER NOT NULL, change_pct REAL NOT NULL,
        PRIMARY KEY(pool, threshold_pct),
        FOREIGN KEY(pool) REFERENCES pools(pool)
      )
    """)
    db.execute("""
      CREATE TABLE IF NOT EXISTS diagnostics(
        logged_ms INTEGER NOT NULL, kind TEXT NOT NULL, detail TEXT
      )
    """)
    db.commit()
    return db


def fetch_json(session: requests.Session, url: str) -> Any:
    r = session.get(url, timeout=(4, 10))
    if r.status_code == 429:
        raise RuntimeError("provider_rate_limited")
    r.raise_for_status()
    return r.json()


def discover(session: requests.Session, now: int) -> list[dict]:
    body = fetch_json(session, GECKO)
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise RuntimeError("invalid_gecko_new_pool_response")
    results = []
    for item in body["data"]:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") or {}
        rel = item.get("relationships") or {}
        pool = str(attrs.get("address") or "")
        token_id = (((rel.get("base_token") or {}).get("data") or {}).get("id") or "")
        mint = str(token_id).removeprefix("solana_")
        if not BASE58.fullmatch(pool) or not BASE58.fullmatch(mint) or pool == mint:
            continue
        created = str(attrs.get("pool_created_at") or "")
        try:
            origin = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            born_ms = int(origin.timestamp() * 1000)
        except (ValueError, TypeError):
            continue
        age = now - born_ms
        if age < -10_000 or age > WATCH_AGE_MS:
            continue
        results.append({
            "pool": pool, "mint": mint,
            "symbol": str(attrs.get("name") or "")[:80],
            "created_ms": born_ms,
            "category": label_candidate(attrs),
        })
    return results


def quote_pools(session: requests.Session, ids: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(ids), MAX_POOLS_PER_BATCH):
        batch = ids[start:start + MAX_POOLS_PER_BATCH]
        if not batch:
            continue
        body = fetch_json(session, DEX + ",".join(batch))
        if not isinstance(body, dict):
            continue
        for pair in body.get("pairs") or []:
            if not isinstance(pair, dict) or pair.get("chainId") != "solana":
                continue
            pool = pair.get("pairAddress")
            if pool not in batch:
                continue
            price = positive(pair.get("priceUsd"))
            if price is None:
                continue
            found[pool] = {
                "price": price,
                "liquidity": positive((pair.get("liquidity") or {}).get("usd")),
                "base_mint": (pair.get("baseToken") or {}).get("address"),
            }
    return found


def store_cycle(db: sqlite3.Connection, newest: list[dict], quotes: dict[str, dict], now: int):
    # One record for *every* encountered new pool, including missing prices.
    # Do not exclude unpriced pools from denominator as if they were losers/winners.
    for entry in newest:
        quote = quotes.get(entry["pool"])
        if quote and quote["base_mint"] != entry["mint"]:
            quote = None
        db.execute(
            """INSERT OR IGNORE INTO pools(
              pool,mint,symbol,created_ms,first_seen_ms,baseline_price,
              baseline_liquidity,initial_category,initial_price_source)
              VALUES(?,?,?,?,?,?,?,?,?)""",
            (entry["pool"], entry["mint"], entry["symbol"], entry["created_ms"],
             now, quote["price"] if quote else None,
             quote["liquidity"] if quote else None, entry["category"],
             "DEXSCREENER_RECEIVED_MARK" if quote else "BASELINE_MISSING")
        )
    ongoing = db.execute(
        "SELECT pool,mint,first_seen_ms,baseline_price FROM pools WHERE ? <= first_seen_ms + ?",
        (now, RECORD_MS + GRACE_MS)
    ).fetchall()
    # Individual event detection requires a fresh response in this cycle.
    # DexScreener has no per-pair source quote timestamp: received_ms is
    # retrieval time, NOT evidence of an executable swap quote.
    for row in ongoing:
        quote = quotes.get(row["pool"])
        if not quote or quote["base_mint"] != row["mint"]:
            continue
        db.execute(
            "INSERT OR IGNORE INTO marks(pool,received_ms,price_usd,liquidity_usd) VALUES(?,?,?,?)",
            (row["pool"], now, quote["price"], quote["liquidity"])
        )
        baseline = number(row["baseline_price"])
        if baseline is None or baseline <= 0 or now < row["first_seen_ms"]:
            continue
        elapsed = now - row["first_seen_ms"]
        if elapsed <= RECORD_MS:
            change = (quote["price"] / baseline - 1) * 100
            if math.isfinite(change):
                for threshold in THRESHOLDS:
                    if change >= threshold:
                        db.execute(
                            "INSERT OR IGNORE INTO target_marks(pool,threshold_pct,received_ms,change_pct) VALUES(?,?,?,?)",
                            (row["pool"], threshold, now, change)
                        )
    for row in db.execute(
        "SELECT pool,first_seen_ms,baseline_price FROM pools WHERE ? >= first_seen_ms + 60000",
        (now,)
    ).fetchall():
        for minutes in HORIZONS:
            already = db.execute(
                "SELECT 1 FROM horizons WHERE pool=? AND minutes=?",
                (row["pool"], minutes)
            ).fetchone()
            if already:
                continue
            target = row["first_seen_ms"] + minutes * 60_000
            if now < target:
                continue
            base = number(row["baseline_price"])
            if base is None or base <= 0:
                db.execute(
                    "INSERT OR IGNORE INTO horizons(pool,minutes,state) VALUES(?,?,?)",
                    (row["pool"], minutes, "baseline_missing")
                )
                continue
            observed = db.execute(
                """SELECT received_ms,price_usd FROM marks WHERE pool=?
                AND received_ms>=? AND received_ms<=?
                ORDER BY received_ms LIMIT 1""",
                (row["pool"], target, target+GRACE_MS)
            ).fetchone()
            if observed:
                change = (observed["price_usd"] / base - 1) * 100
                if math.isfinite(change):
                    db.execute(
                        """INSERT OR IGNORE INTO horizons(
                        pool,minutes,state,received_ms,price_usd,change_pct)
                        VALUES(?,?, 'observed', ?,?,?)""",
                        (row["pool"], minutes, observed["received_ms"],
                         observed["price_usd"], change)
                    )
            elif now > target + GRACE_MS:
                db.execute(
                    "INSERT OR IGNORE INTO horizons(pool,minutes,state) VALUES(?,?,'missing')",
                    (row["pool"], minutes)
                )
    db.commit()


def summary(db: sqlite3.Connection, now: int) -> dict:
    total = db.execute("SELECT COUNT(*) FROM pools").fetchone()[0]
    missing_start = db.execute(
        "SELECT COUNT(*) FROM pools WHERE baseline_price IS NULL"
    ).fetchone()[0]
    counted = db.execute(
        """SELECT initial_category,COUNT(*) AS n FROM pools GROUP BY initial_category"""
    ).fetchall()
    groups = {x["initial_category"]: x["n"] for x in counted}
    milestones = {}
    for minute in HORIZONS:
        due = db.execute(
            "SELECT COUNT(*) FROM pools WHERE first_seen_ms+? <= ?",
            (minute*60000, now)
        ).fetchone()[0]
        marks = db.execute(
            """SELECT h.change_pct FROM horizons h
               WHERE h.minutes=? AND h.state='observed'
               ORDER BY h.change_pct""",
            (minute,)
        ).fetchall()
        changes = [r["change_pct"] for r in marks]
        missing = db.execute(
            "SELECT COUNT(*) FROM horizons WHERE minutes=? AND state IN ('missing','baseline_missing')",
            (minute,)
        ).fetchone()[0]
        milestones[str(minute)] = {
            "due": due, "observed": len(changes), "missing": missing,
            "pending": max(0, due-len(changes)-missing),
            "price_up": sum(x>0 for x in changes),
            "price_down_or_flat": sum(x<=0 for x in changes),
            "median_price_change_pct": (
                round((changes[(len(changes)-1)//2]+changes[len(changes)//2])/2,4)
                if changes else None),
            "mark_pnl_if_200_usd_at_baseline_and_held_to_milestone_usd":
                round(sum(200*x/100 for x in changes),2),
            "important": "GROSS CHART MARK ONLY; cohort subset is missing prices; NOT money earned",
        }
    thresholds = {}
    for pct in THRESHOLDS:
        hits=db.execute(
            "SELECT COUNT(*) FROM target_marks WHERE threshold_pct=?",
            (pct,)
        ).fetchone()[0]
        thresholds[str(pct)] = {
            "ever_observed_mark_at_or_above":hits,
            "percentage_of_all_seen_pools":round(100*hits/total,3) if total else None,
            "not_trade_fills":True,
        }
    latest = db.execute(
        """SELECT pool,symbol,first_seen_ms,initial_category,baseline_price
        FROM pools ORDER BY first_seen_ms DESC LIMIT 8"""
    ).fetchall()
    return {
        "version":VERSION,
        "generated_at_utc":dt.datetime.fromtimestamp(now/1000,dt.timezone.utc).isoformat(),
        "mode":"CONTINUOUS_READ_ONLY_PAPER_MARK_RESEARCH",
        "orders_created":0,"actual_pnl_usd":0,
        "execution_enabled":False,"wallet_keys_accessed":False,
        "discovery_provider":"GeckoTerminal Solana new pools",
        "mark_provider":"DexScreener exact pool (indicative, not executable)",
        "denominator_notice":"Only new pools returned by provider while this observer was running; not all Solana launches.",
        "pools_first_seen":total,"pools_without_initial_quote":missing_start,
        "categories_at_first_observation":groups,
        "milestones_after_first_observation_minutes":milestones,
        "gross_observed_threshold_marks_pct":thresholds,
        "latest_seen":[dict(r) for r in latest],
        "warning":"+5%/+10% observed mark is not a fill, realized profit, or guarantee. Missing/delisted pools are not winners. No buy/sell operations.",
    }


def write_report(report_path:Path,data:dict):
    report_path.parent.mkdir(parents=True,exist_ok=True)
    tmp = report_path.with_suffix(report_path.suffix + ".tmp")
    with tmp.open("w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,report_path)


def serve(args):
    db = open_db(Path(args.database))
    session=requests.Session()
    session.headers.update({"User-Agent":"NEO-Early-PAPER-Research/1.0 (read-only market study)"})
    cycles=0
    errors=0
    def interrupt(*_):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,interrupt)
    signal.signal(signal.SIGINT,interrupt)
    while not STOP:
        now=int(time.time()*1000)
        try:
            newest=discover(session,now)
            for pool in newest:
                db.execute("""INSERT OR IGNORE INTO pools(
                  pool,mint,symbol,created_ms,first_seen_ms,baseline_price,
                  baseline_liquidity,initial_category,initial_price_source
                  ) VALUES(?,?,?,?,?,NULL,NULL,?,'BASELINE_MISSING')""",
                  (pool["pool"],pool["mint"],pool["symbol"],pool["created_ms"],now,pool["category"])
                )
            db.commit()
            ongoing = db.execute(
                "SELECT pool FROM pools WHERE ? <= first_seen_ms + ? ORDER BY first_seen_ms",
                (now, RECORD_MS + GRACE_MS)
            ).fetchall()
            keys=list(dict.fromkeys([r["pool"] for r in ongoing] + [x["pool"] for x in newest]))
            quotes=quote_pools(session,keys)
            # Store a first-event baseline only when a same-cycle Dex mark exists;
            # do not backfill an initially missing quote later.
            for entry in newest:
                mark=quotes.get(entry["pool"])
                if not mark or mark["base_mint"] != entry["mint"]:
                    continue
                db.execute("""UPDATE pools SET baseline_price=?,baseline_liquidity=?,
                  initial_price_source='DEXSCREENER_RECEIVED_MARK'
                  WHERE pool=? AND first_seen_ms=? AND baseline_price IS NULL""",
                  (mark["price"],mark["liquidity"],entry["pool"],now))
            db.commit()
            store_cycle(db,[],quotes,now)
            write_report(Path(args.report),summary(db,now))
            cycles += 1
            if cycles % 3 == 1:
                print(json.dumps({
                    "time":dt.datetime.now(dt.timezone.utc).isoformat(),
                    "cycles":cycles,"new":len(newest),"tracked":len(keys),
                    "priced":len(quotes),"pools":db.execute("SELECT COUNT(*) FROM pools").fetchone()[0],
                    "api_errors":errors,
                },ensure_ascii=False),flush=True)
        except Exception as exc:
            errors += 1
            try:
                db.execute("INSERT INTO diagnostics VALUES(?,?,?)",(int(time.time()*1000),"api_error",str(exc)[:400]))
                db.commit()
                write_report(Path(args.report),dict(summary(db,int(time.time()*1000)),last_error=str(exc)[:400],api_errors=errors))
            except Exception as report_exc:
                print("report_error:",str(report_exc),file=sys.stderr,flush=True)
            print("cycle_error:",str(exc),file=sys.stderr,flush=True)
        if args.once or STOP:
            break
        end=time.monotonic()+max(10,args.interval)
        while not STOP and time.monotonic()<end:
            time.sleep(min(.5,end-time.monotonic()))
    db.close()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--database",default="/var/lib/neo-early-paper/observations.sqlite3")
    ap.add_argument("--report",default="/var/lib/neo-early-paper/report.json")
    ap.add_argument("--interval",type=int,default=POLL_SECONDS)
    ap.add_argument("--once",action="store_true")
    args=ap.parse_args()
    serve(args)


if __name__=="__main__":
    main()

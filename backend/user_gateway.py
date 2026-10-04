#!/usr/bin/env python3
import copy
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests

HOST = os.getenv("NEO_USER_GATEWAY_HOST", "127.0.0.1")
PORT = int(os.getenv("NEO_USER_GATEWAY_PORT", "8789"))
UPSTREAM = os.getenv("NEO_MARKET_UPSTREAM", "http://127.0.0.1:8788").rstrip("/")
STATE_PATH = Path(os.getenv("NEO_USER_STATE_PATH", "/var/lib/neo-market/user_accounts.json"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qziuovwcauaklgqscqys.supabase.co").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
STARTING_BALANCE = 1000.0
MAX_HISTORY = 300
LOCK = threading.RLock()

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "NEO-Meme-User-Gateway/1.0", "Accept": "application/json"})


def now_ms():
    return int(time.time() * 1000)


def number(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def load_store():
    try:
        if not STATE_PATH.exists():
            return {"accounts": {}}
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"accounts": {}}
        data.setdefault("accounts", {})
        return data
    except Exception:
        return {"accounts": {}}


STORE = load_store()


def save_store():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(STORE, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(STATE_PATH)


def parse_created_at(value):
    if not value:
        return now_ms()
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return now_ms()


def verify_user(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token or not SUPABASE_PUBLISHABLE_KEY:
        return None
    try:
        response = SESSION.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": SUPABASE_PUBLISHABLE_KEY,
            },
            timeout=8,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        return data if data.get("id") else None
    except Exception:
        return None


def fetch_upstream(path="/state"):
    response = SESSION.get(f"{UPSTREAM}{path}", timeout=12)
    response.raise_for_status()
    return response.json()


def session_id(user_id, started_at):
    return f"USER-{user_id[:8]}-{str(started_at)[-6:]}"


def ensure_account(user, raw):
    user_id = user["id"]
    accounts = STORE.setdefault("accounts", {})
    account = accounts.get(user_id)
    if account:
        return account

    registered_at = parse_created_at(user.get("created_at"))
    engine_started = int((raw.get("stats") or {}).get("demo_started_at") or 0)
    started_at = max(registered_at, engine_started)
    account = {
        "user_id": user_id,
        "started_at": started_at,
        "starting_balance": STARTING_BALANCE,
        "balance": STARTING_BALANCE,
        "history": [],
        "seen_trade_ids": [],
        "trade_numbers": {},
        "trade_seq": 0,
        "created_at": now_ms(),
        "updated_at": now_ms(),
    }
    accounts[user_id] = account
    save_store()
    return account


def next_trade_no(account, trade_id):
    key = str(trade_id)
    existing = account["trade_numbers"].get(key)
    if existing:
        return int(existing)
    account["trade_seq"] = int(account.get("trade_seq") or 0) + 1
    account["trade_numbers"][key] = account["trade_seq"]
    return account["trade_seq"]


def sync_account(account, raw):
    started_at = int(account.get("started_at") or 0)
    seen = set(str(x) for x in account.get("seen_trade_ids", []))
    closed = []
    for trade in raw.get("history") or []:
        opened_at = int(trade.get("opened_at") or 0)
        trade_id = str(trade.get("id") or "")
        if not trade_id or opened_at < started_at or trade_id in seen:
            continue
        closed.append(trade)

    closed.sort(key=lambda t: int(t.get("closed_at") or t.get("updated_at") or t.get("opened_at") or 0))
    for trade in closed:
        trade_id = str(trade.get("id"))
        before = number(account.get("balance"), STARTING_BALANCE)
        pnl = number(trade.get("pnl_usd"))
        after = before + pnl
        item = copy.deepcopy(trade)
        item["trade_no"] = next_trade_no(account, trade_id)
        item["session_id"] = session_id(account["user_id"], started_at)
        item["balance_before"] = round(before, 8)
        item["balance_after"] = round(after, 8)
        account["balance"] = round(after, 8)
        account.setdefault("history", []).insert(0, item)
        account["history"] = account["history"][:MAX_HISTORY]
        seen.add(trade_id)

    account["seen_trade_ids"] = list(seen)[-2000:]
    account["updated_at"] = now_ms()
    return account


def scoped_state(user, raw):
    with LOCK:
        account = ensure_account(user, raw)
        account = sync_account(account, raw)
        save_store()

        started_at = int(account["started_at"])
        sid = session_id(user["id"], started_at)
        history = copy.deepcopy(account.get("history", []))

        positions = []
        for position in raw.get("positions") or []:
            if int(position.get("opened_at") or 0) < started_at:
                continue
            item = copy.deepcopy(position)
            trade_id = str(item.get("id") or "")
            item["trade_no"] = next_trade_no(account, trade_id)
            item["session_id"] = sid
            item["balance_at_entry"] = number(account.get("balance"), STARTING_BALANCE)
            positions.append(item)

        balance = number(account.get("balance"), STARTING_BALANCE)
        reserved = sum(number(p.get("notional_usd")) for p in positions)
        unrealized = sum(number(p.get("pnl_usd")) for p in positions)
        equity = balance + unrealized
        wins = sum(1 for t in history if number(t.get("pnl_usd")) > 0)

        day = time.strftime("%Y-%m-%d", time.gmtime())
        realized_today = 0.0
        for trade in history:
            stamp = int(trade.get("closed_at") or trade.get("updated_at") or 0)
            if stamp and time.strftime("%Y-%m-%d", time.gmtime(stamp / 1000)) == day:
                realized_today += number(trade.get("pnl_usd"))

        result = copy.deepcopy(raw)
        result["positions"] = positions
        result["history"] = history
        result["events"] = [
            e for e in (raw.get("events") or [])
            if int(e.get("ts") or 0) >= started_at
        ]

        stats = result.setdefault("stats", {})
        stats.update({
            "open_positions": len(positions),
            "closed_trades": len(history),
            "wins": wins,
            "win_rate": round((wins / len(history)) * 100, 1) if history else 0,
            "realized_today_usd": round(realized_today, 2),
            "demo_starting_balance_usd": STARTING_BALANCE,
            "demo_balance_usd": round(balance, 2),
            "demo_equity_usd": round(equity, 2),
            "demo_available_usd": round(max(0.0, balance - reserved), 2),
            "demo_reserved_usd": round(reserved, 2),
            "unrealized_pnl_usd": round(unrealized, 2),
            "realized_total_usd": round(balance - STARTING_BALANCE, 2),
            "return_pct": round(((equity - STARTING_BALANCE) / STARTING_BALANCE) * 100, 3),
            "demo_started_at": started_at,
            "demo_session_id": sid,
        })
        result["account_scope"] = {
            "user_id": user["id"],
            "isolated": True,
            "strategy": "ORDER_FLOW_ADAPTIVE",
        }
        save_store()
        return result


def reset_account(user, raw):
    with LOCK:
        user_id = user["id"]
        STORE.setdefault("accounts", {})[user_id] = {
            "user_id": user_id,
            "started_at": now_ms(),
            "starting_balance": STARTING_BALANCE,
            "balance": STARTING_BALANCE,
            "history": [],
            "seen_trade_ids": [],
            "trade_numbers": {},
            "trade_seq": 0,
            "created_at": now_ms(),
            "updated_at": now_ms(),
        }
        save_store()
        return scoped_state(user, raw)


class Handler(BaseHTTPRequestHandler):
    server_version = "NEOUserGateway/1.0"

    def log_message(self, fmt, *args):
        return

    def cors(self):
        origin = self.headers.get("Origin", "")
        allowed = {
            "https://angelmalev9-creator.github.io",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
        self.send_header("Access-Control-Allow-Origin", origin if origin in allowed else "https://angelmalev9-creator.github.io")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")

    def json_response(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def authenticated(self):
        user = verify_user(self.headers)
        if not user:
            self.json_response({"error": "unauthorized"}, 401)
            return None
        return user

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/user/health":
            self.json_response({"ok": True, "isolated_accounts": True})
            return
        if path != "/user/state":
            self.json_response({"error": "not_found"}, 404)
            return
        user = self.authenticated()
        if not user:
            return
        try:
            raw = fetch_upstream("/state")
            self.json_response(scoped_state(user, raw))
        except Exception as exc:
            self.json_response({"error": "gateway_error", "message": str(exc)}, 502)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/user/reset":
            self.json_response({"error": "not_found"}, 404)
            return
        user = self.authenticated()
        if not user:
            return
        try:
            raw = fetch_upstream("/state")
            self.json_response(reset_account(user, raw))
        except Exception as exc:
            self.json_response({"error": "gateway_error", "message": str(exc)}, 502)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"NEO user gateway listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
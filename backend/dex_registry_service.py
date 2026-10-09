#!/usr/bin/env python3
"""Free DEX-presence watcher for NEO Meme Coins.

Sources:
- GeckoTerminal's public Solana new-pools feed (slow, rate-limit friendly)
- the local NEO engine feed/state (no external request)
- NEO's verified PumpSwap on-chain tape (no extra RPC traffic)

It never changes strategy, prices, entries or exits. It only records whether a mint
has been seen with a concrete DEX pool and exposes a localhost read-only API.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from dex_registry import DexRegistry

HOST = os.getenv('NEO_DEX_REGISTRY_HOST', '127.0.0.1')
PORT = int(os.getenv('NEO_DEX_REGISTRY_PORT', '18807'))
REGISTRY_PATH = Path(os.getenv('NEO_DEX_REGISTRY_PATH', '/var/lib/neo-market/dex_registry.json'))
ENGINE_STATE_PATH = Path(os.getenv('NEO_DEX_ENGINE_STATE_PATH', '/var/lib/neo-market/users/42d3192d-f033-4061-85d6-2408c5e168e7/state.json'))
ENGINE_URL = os.getenv('NEO_DEX_ENGINE_URL', 'http://127.0.0.1:18804/state')
LIVE_TAPE_PATH = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
LIVE_TAPE_DB_PATH = Path(os.getenv('NEO_LIVE_TAPE_DB_PATH', '/var/lib/neo-market/live_tape.sqlite3'))
GECKO_URL = os.getenv('NEO_GECKO_NEW_POOLS_URL', 'https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1')
GECKO_SECONDS = max(30.0, float(os.getenv('NEO_DEX_GECKO_SECONDS', '60')))
ENGINE_SECONDS = max(5.0, float(os.getenv('NEO_DEX_ENGINE_SECONDS', '10')))
DB_SECONDS = max(30.0, float(os.getenv('NEO_DEX_DB_SECONDS', '60')))
LOCAL_SECONDS = max(1.0, float(os.getenv('NEO_DEX_LOCAL_SECONDS', '2')))
SOL_MINT = 'So11111111111111111111111111111111111111112'
ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NEO-DEX-Registry/1.0', 'Accept': 'application/json;version=20230203'})
REGISTRY = DexRegistry(REGISTRY_PATH)
STATUS = {'status': 'starting', 'updated_at': 0, 'last_gecko_at': 0, 'last_engine_at': 0, 'last_db_at': 0, 'last_local_at': 0,
          'gecko_error': None, 'engine_error': None, 'db_error': None, 'local_error': None, 'gecko_backoff_seconds': 0}
STATUS_LOCK = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def gecko_records(payload: dict, observed_at: int) -> list[dict]:
    """Normalize every Solana pool, regardless of DEX, into registry evidence."""
    out: list[dict] = []
    for row in payload.get('data') or []:
        if not isinstance(row, dict):
            continue
        attrs, rel = row.get('attributes') or {}, row.get('relationships') or {}
        dex = str((((rel.get('dex') or {}).get('data') or {}).get('id')) or 'unknown').lower()
        pair = str(attrs.get('address') or '').strip()
        base = str((((rel.get('base_token') or {}).get('data') or {}).get('id')) or '').removeprefix('solana_')
        quote = str((((rel.get('quote_token') or {}).get('data') or {}).get('id')) or '').removeprefix('solana_')
        if not ADDRESS.fullmatch(pair):
            continue
        for mint in (base, quote):
            if mint == SOL_MINT or not ADDRESS.fullmatch(mint):
                continue
            out.append({'mint': mint, 'pair': pair, 'dex': dex, 'source': 'gecko-new-pools',
                        'observed_at': observed_at})
    return out


def tape_db_records(path: Path) -> list[dict]:
    """Read the latest already-verified PumpSwap event for every recorded pool."""
    if not path.exists():
        return []
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1.5)
    try:
        con.execute('pragma query_only=on')
        rows = con.execute(
            'select e.pair,e.event_time,e.payload from events e '
            'join (select pair,max(event_time) as latest from events group by pair) x '
            'on e.pair=x.pair and e.event_time=x.latest'
        ).fetchall()
    finally:
        con.close()
    out, seen = [], set()
    for pair, event_time, raw in rows:
        if pair in seen:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        mint = str(payload.get('address') or '').strip()
        exact_pair = str(payload.get('pairAddress') or pair or '').strip()
        if not ADDRESS.fullmatch(mint) or not ADDRESS.fullmatch(exact_pair):
            continue
        if payload.get('confirmed_swap') is False:
            continue
        out.append({'mint': mint, 'pair': exact_pair, 'dex': 'pumpswap',
                    'source': 'live-tape-sqlite', 'observed_at': int(event_time or 0)})
        seen.add(pair)
    return out


def poll_tape_db() -> None:
    stamp = now_ms()
    try:
        for row in tape_db_records(LIVE_TAPE_DB_PATH):
            REGISTRY.observe(row['mint'], row['pair'], row['dex'], row['source'],
                             observed_at=row['observed_at'] or stamp, onchain_verified=True, persist=False)
        REGISTRY.flush_if_due(stamp, force=True)
        with STATUS_LOCK:
            STATUS.update(last_db_at=stamp, db_error=None)
    except Exception as exc:
        with STATUS_LOCK:
            STATUS['db_error'] = str(exc)[:240]


def poll_gecko() -> float:
    """Poll slowly enough to stay free; 429s trigger an automatic longer backoff."""
    stamp = now_ms()
    try:
        response = SESSION.get(GECKO_URL, timeout=(1.5, 4.0))
        response.raise_for_status()
        payload = response.json()
        for row in gecko_records(payload if isinstance(payload, dict) else {}, stamp):
            REGISTRY.observe(row['mint'], row['pair'], row['dex'], row['source'],
                             observed_at=row['observed_at'], persist=False)
        REGISTRY.flush_if_due(stamp, force=True)
        with STATUS_LOCK:
            STATUS.update(last_gecko_at=stamp, gecko_error=None, gecko_backoff_seconds=0)
        return GECKO_SECONDS
    except requests.HTTPError as exc:
        code = getattr(exc.response, 'status_code', None)
        if code == 429:
            try:
                retry = float(exc.response.headers.get('Retry-After') or 0)
            except (TypeError, ValueError):
                retry = 0
            delay = max(GECKO_SECONDS * 2, retry, 120.0)
        else:
            delay = GECKO_SECONDS
        with STATUS_LOCK:
            STATUS.update(gecko_error=str(exc)[:240], gecko_backoff_seconds=int(delay))
        return delay
    except Exception as exc:
        with STATUS_LOCK:
            STATUS.update(gecko_error=str(exc)[:240], gecko_backoff_seconds=int(GECKO_SECONDS))
        return GECKO_SECONDS


def poll_engine() -> None:
    stamp = now_ms()
    try:
        try:
            response = SESSION.get(ENGINE_URL, timeout=4.0)
            response.raise_for_status()
            state = response.json()
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = read_json(ENGINE_STATE_PATH)
        rows = list(state.get('feed') or []) + list(state.get('positions') or []) + list(state.get('history') or [])
        REGISTRY.observe_market_pairs(rows, source='engine-feed', observed_at=stamp)
        with STATUS_LOCK:
            STATUS.update(last_engine_at=stamp, engine_error=None)
    except Exception as exc:
        with STATUS_LOCK:
            STATUS['engine_error'] = str(exc)[:240]


def poll_local() -> None:
    stamp = now_ms()
    try:
        tape = read_json(LIVE_TAPE_PATH)
        if tape:
            REGISTRY.observe_tape(tape)
        with STATUS_LOCK:
            STATUS.update(last_local_at=stamp, local_error=None)
    except Exception as exc:
        with STATUS_LOCK:
            STATUS['local_error'] = str(exc)[:240]


def public_state() -> dict:
    stats = REGISTRY.stats()
    with STATUS_LOCK:
        status = dict(STATUS)
    status['status'] = 'online' if status.get('last_local_at') or status.get('last_engine_at') or status.get('last_db_at') or status.get('last_gecko_at') else 'starting'
    status['updated_at'] = max(int(stats.get('updated_at') or 0), int(status.get('last_local_at') or 0),
                               int(status.get('last_engine_at') or 0), int(status.get('last_db_at') or 0),
                               int(status.get('last_gecko_at') or 0))
    status.update(stats)
    return status


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def send_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {'/health', '/state'}:
            state = public_state()
            if parsed.path == '/health':
                state = {'ok': state['status'] == 'online', **state}
            self.send_json(state)
            return
        if parsed.path == '/has':
            mint = (parse_qs(parsed.query).get('mint') or [''])[0][:64]
            if not ADDRESS.fullmatch(mint):
                self.send_json({'error': 'invalid mint'}, 400)
                return
            self.send_json({'mint': mint, 'has_dex': REGISTRY.has_dex(mint),
                            'pairs': REGISTRY.pairs_for(mint)})
            return
        self.send_json({'error': 'not found'}, 404)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    next_gecko = 0.0
    next_engine = 0.0
    next_db = 0.0
    while True:
        started = time.monotonic()
        poll_local()
        if started >= next_engine:
            poll_engine()
            next_engine = started + ENGINE_SECONDS
        if started >= next_db:
            poll_tape_db()
            next_db = started + DB_SECONDS
        if started >= next_gecko:
            next_gecko = started + poll_gecko()
        with STATUS_LOCK:
            STATUS['status'] = 'online'
            STATUS['updated_at'] = now_ms()
        sleep_for = max(0.2, LOCAL_SECONDS - (time.monotonic() - started))
        time.sleep(sleep_for)


if __name__ == '__main__':
    main()

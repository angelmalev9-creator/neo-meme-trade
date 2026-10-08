#!/usr/bin/env python3
"""Buyers / sellers of one pool over several windows, for the dashboard only.

Two read-only sources are combined and labelled separately:

* the verified on-chain tape (``live_tape.json``) for the short windows it
  covers (counts of trades and distinct wallets), and
* GeckoTerminal's free pool endpoint for 5m … 24h (buys, sells, buyers,
  sellers), cached per pool so a dashboard polling every two seconds stays
  far under the public rate limit.

Nothing here feeds a trading decision. The engine still uses only the
verified tape and the exact-pool quotes it already had.
"""
import math
import threading
import time
from typing import Any, Callable

GECKO_POOL_URL = 'https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}'
GECKO_HEADERS = {'Accept': 'application/json;version=20230203'}
GECKO_TTL_MS = 10_000
GECKO_ERROR_TTL_MS = 30_000
GECKO_WINDOWS = ('m5', 'm15', 'm30', 'h1', 'h6', 'h24')
WINDOW_SECONDS = {'m1': 60, 'm5': 300, 'm15': 900, 'm30': 1800, 'h1': 3600, 'h4': 14400,
                  'h6': 21600, 'h24': 86400, 'd3': 259200, 'd7': 604800}
TAPE_WINDOWS = ('m1', 'm5', 'm15', 'm30', 'h1')
_CACHE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError, OverflowError):
        return default


def tape_windows(events: list[dict[str, Any]], address: str, pair: str, *, now: int,
                 tape_oldest_ms: int | None = None) -> dict[str, Any]:
    """Counts from the verified tape for each window the tape fully covers."""
    rows = [e for e in events if isinstance(e, dict) and e.get('address') == address
            and (not pair or e.get('pairAddress') == pair) and not e.get('quality_flags')
            and _num(e.get('usd_amount')) > 0 and 0 < _num(e.get('ts')) <= now]
    oldest = tape_oldest_ms if tape_oldest_ms else (min((int(_num(e.get('ts'))) for e in rows), default=now))
    covered_seconds = max(0, (now - oldest) // 1000)
    out = {}
    for key in TAPE_WINDOWS:
        seconds = WINDOW_SECONDS[key]
        cutoff = now - seconds * 1000
        inside = [e for e in rows if _num(e.get('ts')) >= cutoff]
        buys = [e for e in inside if e.get('direction') == 'BUY']
        sells = [e for e in inside if e.get('direction') == 'SELL']
        out[key] = {
            'buys': len(buys), 'sells': len(sells),
            'buyers': len({e.get('wallet') for e in buys if e.get('wallet')}),
            'sellers': len({e.get('wallet') for e in sells if e.get('wallet')}),
            'buy_usd': round(sum(_num(e.get('usd_amount')) for e in buys), 2),
            'sell_usd': round(sum(_num(e.get('usd_amount')) for e in sells), 2),
            # A window longer than the tape's retention is only partly observed.
            'complete': covered_seconds >= seconds,
        }
    return {'windows': out, 'covered_seconds': int(covered_seconds), 'events': len(rows)}


def parse_gecko_pool(payload: dict[str, Any]) -> dict[str, Any]:
    attrs = ((payload or {}).get('data') or {}).get('attributes') or {}
    transactions = attrs.get('transactions') or {}
    windows = {}
    for key in GECKO_WINDOWS:
        row = transactions.get(key) or {}
        windows[key] = {field: int(_num(row.get(field))) for field in ('buys', 'sells', 'buyers', 'sellers')}
    volume = attrs.get('volume_usd') or {}
    change = attrs.get('price_change_percentage') or {}
    created = attrs.get('pool_created_at')
    return {
        'windows': windows,
        'volume_usd': {k: round(_num(volume.get(k)), 2) for k in GECKO_WINDOWS if k in volume},
        'price_change_pct': {k: _num(change.get(k)) for k in GECKO_WINDOWS if k in change},
        'pool_created_at': created,
        'reserve_usd': _num(attrs.get('reserve_in_usd')),
        'market_cap_usd': _num(attrs.get('market_cap_usd') or attrs.get('fdv_usd')),
    }


def gecko_pool(pair: str, fetch: Callable[[str], dict[str, Any]], *, now: int | None = None) -> dict[str, Any]:
    """Cached GeckoTerminal pool statistics; errors are cached too, briefly."""
    now = now or now_ms()
    with _LOCK:
        cached = _CACHE.get(pair)
    if cached:
        ttl = GECKO_ERROR_TTL_MS if cached.get('error') else GECKO_TTL_MS
        if 0 <= now - int(cached.get('fetched_at') or 0) <= ttl:
            return cached
    try:
        row = parse_gecko_pool(fetch(GECKO_POOL_URL.format(pair=pair)))
        row.update(fetched_at=now, error=None, source='geckoterminal')
    except Exception as exc:  # network, JSON or shape problems all show as unavailable
        row = {'fetched_at': now, 'error': str(exc)[:160], 'source': 'geckoterminal', 'windows': {}}
    with _LOCK:
        _CACHE[pair] = row
        if len(_CACHE) > 256:
            for stale in sorted(_CACHE, key=lambda k: _CACHE[k].get('fetched_at', 0))[:64]:
                _CACHE.pop(stale, None)
    return row


def available_windows(age_minutes: float | None) -> list[str]:
    """Windows that make sense for a pool of this age (longer ones would be empty)."""
    order = ['m1', 'm5', 'm15', 'm30', 'h1', 'h6', 'h24']
    if age_minutes is None:
        return order
    return [k for k in order if k in ('m1', 'm5') or WINDOW_SECONDS[k] <= age_minutes * 60]


def build(address: str, pair: str, *, coin: dict[str, Any] | None, tape: dict[str, Any],
          fetch: Callable[[str], dict[str, Any]], now: int | None = None) -> dict[str, Any]:
    now = now or now_ms()
    events = tape.get('events') or []
    coverage = ((tape.get('pair_coverage') or {}).get(pair) or {}) if pair else {}
    tape_view = tape_windows(events, address, pair, now=now)
    gecko = gecko_pool(pair, fetch, now=now) if pair else {'windows': {}, 'error': 'no_pair'}
    age = _num((coin or {}).get('ageMinutes'), 0) or None
    dex_tx = (coin or {}).get('txns') or {}
    return {
        'address': address, 'pairAddress': pair, 'at': now,
        'age_minutes': age, 'available_windows': available_windows(age),
        'tape': {**tape_view, 'status': tape.get('status'), 'coverage': coverage.get('status'),
                 'updated_at': tape.get('updated_at')},
        'gecko': {k: gecko.get(k) for k in ('windows', 'volume_usd', 'price_change_pct', 'pool_created_at',
                                            'fetched_at', 'error', 'source')},
        'dexscreener': {k: {'buys': int(_num((dex_tx.get(k) or {}).get('buys'))),
                            'sells': int(_num((dex_tx.get(k) or {}).get('sells')))}
                        for k in ('m5', 'h1', 'h6', 'h24') if isinstance(dex_tx.get(k), dict)},
        'longer_windows_note': 'За прозорци над 24ч (3д, 7д) няма безплатен източник на купувачи/продавачи.',
    }

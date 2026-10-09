#!/usr/bin/env python3
"""Dedicated PAPER order-flow collector matching the 2026-10-04 data path.

The trading policy stays in order_flow_adaptive_oct4.py.  This collector only
restores the Oct-4 live-tape semantics (signer token-balance deltas from the
SolanaTracker public RPC) while adding two compatibility fields required by the
modern engine: exact-pool coverage and event availability timestamps.

It is PAPER-only market observation.  It never submits a transaction.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import requests

API_URL = os.getenv('NEO_LOCAL_API', 'http://127.0.0.1:8788/state')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://rpc.solanatracker.io/public')
RPC_FALLBACK_URLS = tuple(url.strip() for url in os.getenv(
    'SOLANA_RPC_FALLBACKS',
    'https://api.mainnet-beta.solana.com,https://solana-rpc.publicnode.com',
).split(',') if url.strip() and url.strip() != RPC_URL)
RPC_URLS = (RPC_URL, *RPC_FALLBACK_URLS)
OUT = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape-oct4.json'))
MAX_TRACKED = max(1, int(os.getenv('NEO_TAPE_MAX_PAIRS', '45')))
MAX_EVENTS = max(100, int(os.getenv('NEO_TAPE_MAX_EVENTS', '1600')))
POLL_SECONDS = max(0.5, float(os.getenv('NEO_TAPE_POLL_SECONDS', '2.0')))
FLOW_WINDOW_MS = max(30_000, int(os.getenv('NEO_TAPE_FLOW_WINDOW_MS', '60000')))
STICKY_MS = max(FLOW_WINDOW_MS, int(float(os.getenv('NEO_TAPE_STICKY_SECONDS', '210')) * 1000))
RPC_BATCH = max(1, min(60, int(os.getenv('NEO_TAPE_RPC_BATCH_SIZE', '60'))))
MIN_SCORE = float(os.getenv('NEO_TAPE_OCT4_MIN_SCORE', '85'))
MIN_LIQUIDITY = float(os.getenv('NEO_TAPE_OCT4_MIN_LIQUIDITY', '15000'))
MIN_M5 = float(os.getenv('NEO_TAPE_OCT4_MIN_M5', '-5'))
MAX_M5 = float(os.getenv('NEO_TAPE_OCT4_MAX_M5', '25'))

SESSION = requests.Session()
SESSION.headers.update({'content-type': 'application/json', 'user-agent': 'NEO-LiveTape-Oct4/1.0'})
EVENTS: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
SEEN: set[str] = set()
INITIALIZED_PAIRS: set[str] = set()
PAIR_STARTED: dict[str, int] = {}
PAIR_LAST_POLL: dict[str, int] = {}
PAIR_LATEST_EVENT: dict[str, int] = {}
WALLET_HITS: defaultdict[str, int] = defaultdict(int)
TRACKED: dict[str, dict[str, Any]] = {}
STATUS: dict[str, Any] = {
    'status': 'starting', 'tracked_pairs': 0, 'updated_at': 0,
    'source': 'solanatracker-public-oct4-compatible', 'poll_seconds': POLL_SECONDS,
    'schema_version': 'oct4-compat-1',
}


def now_ms() -> int:
    return int(time.time() * 1000)


def number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError, OverflowError):
        return default


def key_of(item: Any) -> str:
    return str(item.get('pubkey') if isinstance(item, dict) else item)


def activity_of(coin: dict[str, Any]) -> float:
    tx = (coin.get('txns') or {}).get('m5') or {}
    return number(tx.get('buys')) + number(tx.get('sells'))


def preflow_candidate(coin: dict[str, Any]) -> bool:
    changes = coin.get('priceChange') or {}
    return (
        bool(coin.get('pairAddress')) and bool(coin.get('address'))
        and number(coin.get('priceUsd')) > 0
        and number(coin.get('score')) >= MIN_SCORE
        and number(coin.get('liquidityUsd')) >= MIN_LIQUIDITY
        and MIN_M5 <= number(changes.get('m5'), -999.0) <= MAX_M5
    )


def coin_meta(coin: dict[str, Any], stamp: int, *, pinned: bool = False) -> dict[str, Any]:
    return {
        'pair': str(coin.get('pairAddress') or ''),
        'address': str(coin.get('address') or ''),
        'symbol': str(coin.get('symbol') or '?'),
        'price': number(coin.get('priceUsd')),
        'score': number(coin.get('score')),
        'liquidity': number(coin.get('liquidityUsd')),
        'activity': activity_of(coin),
        'last_eligible': stamp,
        'pinned_position': bool(pinned),
    }


def feed_snapshot() -> list[dict[str, Any]]:
    """Keep Oct-4 candidates sticky long enough to build a real 60s flow window.

    The modern market scanner rotates through a much larger universe than it did
    on Oct 4.  Without this small sticky cache a candidate disappears from the
    scanner before one full flow window can be observed.  Only pre-flow GOLD
    candidates are retained; the engine still owns the actual entry decision.
    """
    response = SESSION.get(API_URL, timeout=5)
    response.raise_for_status()
    state = response.json()
    stamp = now_ms()

    current_candidates: list[dict[str, Any]] = []
    for coin in state.get('feed', []) or []:
        if isinstance(coin, dict) and preflow_candidate(coin):
            current_candidates.append(coin)

    positions = []
    for position in state.get('positions', []) or []:
        if not isinstance(position, dict):
            continue
        snap = dict(position.get('coin_snapshot') or {})
        snap.update(address=position.get('address'), pairAddress=position.get('pairAddress'))
        if snap.get('address') and snap.get('pairAddress'):
            positions.append(snap)

    # Update only while a coin still satisfies the GOLD pre-flow gates.  A tape
    # pin therefore cannot keep an obsolete candidate alive forever.
    for coin in current_candidates:
        meta = coin_meta(coin, stamp)
        old = TRACKED.get(meta['pair']) or {}
        meta['started_at'] = int(old.get('started_at') or stamp)
        TRACKED[meta['pair']] = meta
    for coin in positions:
        meta = coin_meta(coin, stamp, pinned=True)
        old = TRACKED.get(meta['pair']) or {}
        meta['started_at'] = int(old.get('started_at') or stamp)
        TRACKED[meta['pair']] = meta

    for pair, meta in list(TRACKED.items()):
        if not meta.get('pinned_position') and stamp - int(meta.get('last_eligible') or 0) > STICKY_MS:
            TRACKED.pop(pair, None)
            INITIALIZED_PAIRS.discard(pair)
            PAIR_STARTED.pop(pair, None)
            PAIR_LAST_POLL.pop(pair, None)
            PAIR_LATEST_EVENT.pop(pair, None)

    ranked = sorted(
        TRACKED.values(),
        key=lambda row: (
            1 if row.get('pinned_position') else 0,
            number(row.get('score')),
            number(row.get('liquidity')),
            -number(row.get('activity')),
            number(row.get('last_eligible')),
        ),
        reverse=True,
    )[:MAX_TRACKED]
    keep = {row['pair'] for row in ranked}
    # Capacity eviction is deliberate; if it returns later it warms again.
    for pair in list(TRACKED):
        if pair not in keep and not TRACKED[pair].get('pinned_position'):
            TRACKED.pop(pair, None)
            INITIALIZED_PAIRS.discard(pair)
            PAIR_STARTED.pop(pair, None)
            PAIR_LAST_POLL.pop(pair, None)
            PAIR_LATEST_EVENT.pop(pair, None)
    return [dict(row) for row in ranked]


def align_answers(calls: list[tuple[str, list[Any]]], data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise RuntimeError('RPC batch response is not a list')
    indexed: dict[int, dict[str, Any]] = {}
    for answer in data:
        if isinstance(answer, dict) and type(answer.get('id')) is int:
            indexed[answer['id']] = answer
    return [indexed.get(i + 1, {'id': i + 1, 'error': {'code': 'MISSING_RPC_ID'}}) for i in range(len(calls))]


def _rpc_cap(url: str, calls: list[tuple[str, list[Any]]]) -> int:
    host = url.lower()
    methods = {method for method, _ in calls}
    if 'publicnode.com' in host:
        return 1 if methods == {'getTransaction'} else min(RPC_BATCH, 4)
    if 'api.mainnet-beta.solana.com' in host:
        return min(RPC_BATCH, 10)
    return RPC_BATCH


def _rpc_request(url: str, calls: list[tuple[str, list[Any]]]) -> list[dict[str, Any]]:
    answers: list[dict[str, Any]] = []
    cap = max(1, _rpc_cap(url, calls))
    for start in range(0, len(calls), cap):
        chunk = calls[start:start + cap]
        payload = [{'jsonrpc': '2.0', 'id': i + 1, 'method': method, 'params': params}
                   for i, (method, params) in enumerate(chunk)]
        response = SESSION.post(url, json=payload, timeout=(1.5, 8.0))
        response.raise_for_status()
        answers.extend(align_answers(chunk, response.json()))
    return answers


def rpc_batch(calls: list[tuple[str, list[Any]]]) -> list[dict[str, Any]]:
    """Use the Oct-4 RPC first, but fail over transient transport/provider errors.

    Entry semantics stay unchanged.  Fallbacks only fill calls whose provider
    response is an RPC error; a legitimate ``result: null`` is preserved.
    """
    if not calls:
        return []
    resolved: list[dict[str, Any] | None] = [None] * len(calls)
    pending = list(range(len(calls)))
    last_error = 'RPC_UNAVAILABLE'
    for url in RPC_URLS:
        if not pending:
            break
        subset = [calls[index] for index in pending]
        try:
            answers = _rpc_request(url, subset)
        except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
            last_error = type(exc).__name__
            continue
        retry: list[int] = []
        for original_index, answer in zip(pending, answers):
            if not isinstance(answer, dict) or answer.get('error'):
                retry.append(original_index)
                if isinstance(answer, dict):
                    last_error = str((answer.get('error') or {}).get('code') or 'RPC_ERROR')
                continue
            resolved[original_index] = answer
        pending = retry
    for index in pending:
        resolved[index] = {'id': index + 1, 'error': {'code': last_error}}
    return [answer or {'id': index + 1, 'error': {'code': 'RPC_UNAVAILABLE'}}
            for index, answer in enumerate(resolved)]


def amount_map(items: Any, mint: str) -> defaultdict[str, float]:
    out: defaultdict[str, float] = defaultdict(float)
    for row in items or []:
        if row.get('mint') != mint:
            continue
        owner = row.get('owner')
        if not owner:
            continue
        ui = row.get('uiTokenAmount') or {}
        amount = number(ui.get('uiAmountString'), number(ui.get('uiAmount')))
        out[str(owner)] += amount
    return out


def parse_trade(tx: dict[str, Any], meta: dict[str, Any], *, available_at: int) -> dict[str, Any] | None:
    tx_meta = (tx or {}).get('meta') or {}
    if tx_meta.get('err') is not None:
        return None
    mint = meta['address']
    pre = amount_map(tx_meta.get('preTokenBalances'), mint)
    post = amount_map(tx_meta.get('postTokenBalances'), mint)
    deltas = {owner: post.get(owner, 0.0) - pre.get(owner, 0.0) for owner in set(pre) | set(post)}
    if not deltas:
        return None
    message = ((tx or {}).get('transaction') or {}).get('message') or {}
    keys = message.get('accountKeys') or []
    signers = [key_of(key) for key in keys if isinstance(key, dict) and key.get('signer')]
    signer_deltas = [(owner, delta) for owner, delta in deltas.items() if owner in signers and abs(delta) > 0]
    if not signer_deltas:
        return None
    wallet, delta = max(signer_deltas, key=lambda item: abs(item[1]))
    direction = 'BUY' if delta > 0 else 'SELL'
    token_amount = abs(delta)
    usd = token_amount * number(meta.get('price'))
    if usd <= 0:
        return None
    WALLET_HITS[wallet] += 1
    repeat = WALLET_HITS[wallet]
    note = ('WHALE ' + direction if usd >= 2500 else
            'LARGE ' + direction if usd >= 750 else
            'REPEAT WALLET' if repeat >= 3 else direction)
    block_time = (tx or {}).get('blockTime')
    event_time = int(block_time * 1000) if block_time else available_at
    return {
        'ts': event_time, 'event_time': event_time, 'observed_at': available_at,
        'ingested_at': available_at, 'available_at': available_at,
        'direction': direction, 'token_amount': round(token_amount, 6),
        'usd_amount': round(usd, 6), 'wallet': wallet, 'note': note,
        'address': mint, 'pairAddress': meta['pair'], 'symbol': meta['symbol'],
        'price_estimate': number(meta.get('price')), 'quality_flags': [],
        'confirmed_swap': True, 'provider': 'solanatracker-public-oct4-compatible',
    }


def coverage_snapshot(feed: list[dict[str, Any]], stamp: int) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for meta in feed:
        pair = meta['pair']
        started = PAIR_STARTED.get(pair)
        last_poll = PAIR_LAST_POLL.get(pair, 0)
        complete = bool(started and stamp - started >= FLOW_WINDOW_MS and stamp - last_poll <= max(6000, int(POLL_SECONDS * 4000)))
        latest = PAIR_LATEST_EVENT.get(pair)
        coverage[pair] = {
            'address': meta['address'], 'pairAddress': pair,
            'status': 'COMPLETE' if complete else 'UNKNOWN',
            'complete_since_ms': started if complete else None,
            'last_poll_at': last_poll, 'backlog': 0, 'oldest_pending_at': None,
            'pagination_pending': False, 'unclassified': 0,
            'max_lag_ms': max(0, stamp - latest) if latest else None,
            'reason': None if complete else 'OCT4_FLOW_WARMING',
        }
    return coverage


def atomic_write(payload: dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + f'.{os.getpid()}.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, OUT)


def poll_once() -> dict[str, Any]:
    feed = feed_snapshot()
    stamp = now_ms()
    STATUS['tracked_pairs'] = len(feed)
    STATUS['updated_at'] = stamp
    if not feed:
        payload = {**STATUS, 'status': 'warming', 'events': list(EVENTS), 'pair_coverage': {}, 'coverage': 0.0,
                   'window_ms': FLOW_WINDOW_MS, 'backlog': 0, 'current_backlog': 0, 'stale_pending': 0,
                   'events_total': len(EVENTS), 'lag_ms': 0, 'error': 'No current GOLD pre-flow candidates'}
        atomic_write(payload)
        return payload

    calls = [('getSignaturesForAddress', [meta['pair'], {'limit': 4, 'commitment': 'confirmed'}]) for meta in feed]
    answers = rpc_batch(calls)
    new: list[tuple[str, Any, dict[str, Any]]] = []
    for meta, answer in zip(feed, answers):
        pair = meta['pair']
        if answer.get('error') or not isinstance(answer.get('result'), list):
            continue
        PAIR_LAST_POLL[pair] = stamp
        rows = answer.get('result') or []
        if pair not in INITIALIZED_PAIRS:
            for row in rows:
                signature = row.get('signature')
                if signature:
                    SEEN.add(signature)
            INITIALIZED_PAIRS.add(pair)
            PAIR_STARTED[pair] = stamp
            continue
        for row in reversed(rows):
            signature = row.get('signature')
            if not signature or signature in SEEN or row.get('err') is not None:
                continue
            SEEN.add(signature)
            new.append((signature, row.get('slot'), meta))

    if len(SEEN) > 20000:
        keep = {event.get('signature') for event in EVENTS if event.get('signature')}
        SEEN.clear(); SEEN.update(sig for sig in keep if sig)
        # Re-prime rather than risking a false uninterrupted window.
        INITIALIZED_PAIRS.clear(); PAIR_STARTED.clear()

    if new:
        new = new[-60:]
        tx_calls = [('getTransaction', [signature, {'encoding': 'jsonParsed', 'commitment': 'confirmed',
                                                    'maxSupportedTransactionVersion': 0}])
                    for signature, _, _ in new]
        tx_answers = rpc_batch(tx_calls)
        available = now_ms()
        for (signature, slot, meta), answer in zip(new, tx_answers):
            tx = answer.get('result') if not answer.get('error') else None
            trade = parse_trade(tx, meta, available_at=available) if tx else None
            if not trade:
                continue
            trade['signature'] = signature
            trade['slot'] = slot
            trade['event_id'] = f"{signature}:{meta['pair']}:0"
            PAIR_LATEST_EVENT[meta['pair']] = int(trade['ts'])
            EVENTS.appendleft(trade)

    stamp = now_ms()
    coverage = coverage_snapshot(feed, stamp)
    complete_count = sum(row['status'] == 'COMPLETE' for row in coverage.values())
    payload = {
        **STATUS,
        # The collector is healthy as soon as it is polling successfully. New
        # candidates can still be individually WARMING without making the whole
        # transport look offline/degraded forever. Entry checks remain per-pair.
        'status': 'online' if complete_count else 'warming',
        'updated_at': stamp, 'events': list(EVENTS), 'pair_coverage': coverage,
        'coverage': complete_count / max(1, len(feed)), 'warming_pairs': len(feed) - complete_count,
        'backlog': 0, 'current_backlog': 0, 'stale_pending': 0,
        'events_total': len(EVENTS), 'lag_ms': 0,
    }
    payload.pop('error', None)
    atomic_write(payload)
    return payload


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    while True:
        started = time.time()
        try:
            poll_once()
        except Exception as exc:
            stamp = now_ms()
            fallback_coverage = coverage_snapshot(list(TRACKED.values())[:MAX_TRACKED], stamp)
            complete_count = sum(row.get('status') == 'COMPLETE' for row in fallback_coverage.values())
            payload = {
                **STATUS, 'status': 'degraded', 'updated_at': stamp,
                'error': str(exc)[:240], 'events': list(EVENTS),
                'pair_coverage': fallback_coverage,
                'coverage': complete_count / max(1, len(fallback_coverage)),
                'warming_pairs': max(0, len(fallback_coverage) - complete_count),
                'window_ms': FLOW_WINDOW_MS,
                'backlog': 0, 'current_backlog': 0, 'stale_pending': 0,
                'events_total': len(EVENTS), 'lag_ms': 0,
            }
            atomic_write(payload)
        time.sleep(max(0.25, POLL_SECONDS - (time.time() - started)))


if __name__ == '__main__':
    main()

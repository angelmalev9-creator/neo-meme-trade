"""PAPER-only, deterministic guard against repeated losses in one exact token pool.

Does not alter Oct 4 strategy signals, quote estimates, position sizing or exits.
Only uses losses already closed in the current PAPER session; no future outcomes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

VERSION = 'PAPER_EXACT_POOL_LOSS_STREAK_V1'
LOSSES_TO_BLOCK = 2
LOOKBACK_MS = 6 * 60 * 60 * 1000
BLOCK_MS = 3 * 60 * 60 * 1000
REJECTION_CODE = 'pool_loss_streak_cooldown'


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _stamp_ms(value: Any) -> int:
    try:
        result = int(value)
        return result if result > 0 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def pool_key(record: dict[str, Any]) -> tuple[str, str] | None:
    mint = str(record.get('address') or '').strip()
    pool = str(record.get('pairAddress') or '').strip()
    return (mint, pool) if mint and pool else None


def blocked_pools(history: list[dict[str, Any]], now_ms: int,
                  session_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Exact pool streaks from *known* closes before the decision timestamp.

    Excludes technical-voided and other-session trades. Same-ticker, other-pool
    trades have no influence. Uses closing order, not opening order; a later win
    breaks the losing streak. Cooldown ends three hours after latest loss.
    """
    now = _stamp_ms(now_ms)
    if not now or not session_id:
        return {}
    matching: dict[tuple[str, str], list[dict[str, Any]]] = {}
    cutoff = now - LOOKBACK_MS
    for row in history:
        if not isinstance(row, dict):
            continue
        if row.get('session_id') != session_id or row.get('technical_voided'):
            continue
        key = pool_key(row)
        closed = _stamp_ms(row.get('closed_at'))
        if not key or not cutoff <= closed < now:
            continue
        pnl = _number(row.get('pnl_usd'))
        if pnl is None:
            continue
        matching.setdefault(key, []).append({
            'closed_at': closed, 'pnl_usd': pnl,
            'symbol': str(row.get('symbol') or ''),
            'trade_id': str(row.get('id') or ''),
        })
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in matching.items():
        rows.sort(key=lambda row: (row['closed_at'], row['trade_id']), reverse=True)
        recent = rows[:LOSSES_TO_BLOCK]
        if len(recent) != LOSSES_TO_BLOCK:
            continue
        if not all(row['pnl_usd'] < 0 for row in recent):
            continue
        blocked_until = recent[0]['closed_at'] + BLOCK_MS
        if now >= blocked_until:
            continue
        out[key] = {
            'reason': REJECTION_CODE,
            'symbol': recent[0]['symbol'],
            'losses': LOSSES_TO_BLOCK,
            'last_loss_at': recent[0]['closed_at'],
            'blocked_until': blocked_until,
            'remaining_seconds': max(0, math.ceil((blocked_until-now)/1000)),
            'version': VERSION,
        }
    return out


def description(history: list[dict[str, Any]], now_ms: int,
                session_id: str, *, enabled: bool) -> dict[str, Any]:
    blocked = blocked_pools(history, now_ms, session_id) if enabled else {}
    return {
        'enabled': enabled,
        'version': VERSION,
        'scope': 'PAPER_ONLY_SAME_MINT_AND_POOL_CURRENT_SESSION',
        'losses_to_block': LOSSES_TO_BLOCK,
        'lookback_hours': LOOKBACK_MS // 3_600_000,
        'block_hours': BLOCK_MS // 3_600_000,
        'blocked_pool_count': len(blocked),
        'blocked_pools': [
            {**result, 'mint': key[0], 'pool': key[1]}
            for key, result in sorted(blocked.items(), key=lambda x: x[1]['blocked_until'], reverse=True)
        ][:20],
    }

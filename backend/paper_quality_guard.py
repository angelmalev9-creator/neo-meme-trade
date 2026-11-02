"""Forward-only quality overlay for the restored Oct-4 PAPER strategy.

This module never submits orders and never rewrites historical results.  It
adds evidence requirements that are available at decision time only:
verified buyer-cluster breadth, anti-churn/anti-wash structure, executable
price basis discipline, and a short loss-streak cooling brake.
"""
from __future__ import annotations

import math
from typing import Any

VERSION = 'OCT4_PAPER_QUALITY_V1'

MIN_BUYER_WALLETS = 3
MIN_UNIQUE_WALLETS = 4
MIN_WALLET_BUY_SELL_RATIO = 1.50
MIN_REPEAT_BUYERS_OR_BUYERS = 1
BUYERS_WITHOUT_REPEAT_OK = 5
MAX_MARKET_BUY_SELL_RATIO = 8.0
MAX_H1_TURNOVER_TO_LIQUIDITY = 4.0
MAX_EXECUTION_BASIS_PCT = 2.50
MAX_ENTRY_IMPACT_PCT = 1.25
MAX_IMMEDIATE_ROUNDTRIP_COST_PCT = 2.75
MAX_CONSERVATIVE_ROUNDTRIP_COST_PCT = 4.00
LOSS_STREAK_COUNT = 4
LOSS_STREAK_COOLDOWN_MS = 2 * 60 * 60 * 1000


def _n(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def market_rejections(coin: dict[str, Any], flow: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """Require a real buyer cluster and reject churn/extreme imbalance.

    These checks use only contemporaneous market/tape evidence; no future PnL
    or backfilled label is consulted.
    """
    reasons: list[str] = []
    buyers = int(_n(flow.get('buyer_wallets')))
    unique = int(_n(flow.get('unique_wallets')))
    wallet_ratio = _n(flow.get('wallet_buy_sell_ratio'))
    repeat_buyers = int(_n(flow.get('repeat_buy_wallets')))

    if buyers < MIN_BUYER_WALLETS or unique < MIN_UNIQUE_WALLETS:
        reasons.append('buyer_cluster')
    if wallet_ratio < MIN_WALLET_BUY_SELL_RATIO:
        reasons.append('wallet_ratio')
    if repeat_buyers < MIN_REPEAT_BUYERS_OR_BUYERS and buyers < BUYERS_WITHOUT_REPEAT_OK:
        reasons.append('repeat_buyers')

    tx5 = (coin.get('txns') or {}).get('m5') or {}
    market_buys = _n(tx5.get('buys'))
    market_sells = _n(tx5.get('sells'))
    market_ratio = market_buys / max(market_sells, 1.0)
    if market_ratio > MAX_MARKET_BUY_SELL_RATIO:
        reasons.append('extreme_buy_imbalance')

    liquidity = _n(coin.get('liquidityUsd'))
    h1_volume = _n((coin.get('volume') or {}).get('h1'))
    turnover = h1_volume / max(liquidity, 1.0)
    if liquidity <= 0 or h1_volume <= 0 or turnover > MAX_H1_TURNOVER_TO_LIQUIDITY:
        reasons.append('turnover_churn')

    # Conviction remains owned by the restored Oct-4 model; this overlay only
    # rejects missing/non-finite context rather than inventing a new threshold.
    if not math.isfinite(_n(context.get('conviction'), math.nan)):
        reasons.append('conviction')
    return reasons


def execution_rejections(*, market_price: float, fill_price: float, impact_pct: float,
                         immediate_roundtrip_pct: float, conservative_roundtrip_pct: float) -> tuple[list[str], dict[str, float]]:
    reasons: list[str] = []
    market = _n(market_price)
    fill = _n(fill_price)
    basis = abs(fill / market - 1.0) * 100.0 if market > 0 and fill > 0 else math.inf
    if not math.isfinite(basis) or basis > MAX_EXECUTION_BASIS_PCT:
        reasons.append('execution_basis')
    if _n(impact_pct, math.inf) > MAX_ENTRY_IMPACT_PCT:
        reasons.append('impact')
    if _n(immediate_roundtrip_pct, -math.inf) < -MAX_IMMEDIATE_ROUNDTRIP_COST_PCT:
        reasons.append('roundtrip_cost')
    if _n(conservative_roundtrip_pct, -math.inf) < -MAX_CONSERVATIVE_ROUNDTRIP_COST_PCT:
        reasons.append('worst_case_cost')
    return reasons, {
        'execution_basis_pct': round(basis, 4) if math.isfinite(basis) else math.inf,
        'entry_impact_pct': round(_n(impact_pct), 4),
        'expected_roundtrip_pct': round(_n(immediate_roundtrip_pct), 4),
        'conservative_roundtrip_pct': round(_n(conservative_roundtrip_pct), 4),
    }


def regime_rejections(history: list[dict[str, Any]], now_ms: int, *, strategy_id: str) -> list[str]:
    """Cool the same strategy after four fresh losses; do not mix profiles."""
    rows = [row for row in history if row.get('strategy_id') == strategy_id and row.get('closed_at')]
    rows.sort(key=lambda row: int(row.get('closed_at') or 0), reverse=True)
    recent = rows[:LOSS_STREAK_COUNT]
    if len(recent) < LOSS_STREAK_COUNT or any(_n(row.get('pnl_pct')) >= 0 for row in recent):
        return []
    last_closed = int(recent[0].get('closed_at') or 0)
    if 0 <= now_ms - last_closed < LOSS_STREAK_COOLDOWN_MS:
        return ['regime_brake']
    return []

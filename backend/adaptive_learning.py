"""ORDER_FLOW_ADAPTIVE_LEARNING — high-frequency PAPER profile that learns from its own results.

Pure module: no network, no clock, no account state, no swap submission.

What it adds on top of ORDER_FLOW_ADAPTIVE (whose conviction model and exit
order it reuses unchanged):

1. Two entry tiers. CORE is the 2026-10-04 GOLD rule. EXPLORE is a looser
   rule that always trades at a small size, so the engine keeps sampling
   contexts the strict rule would never see.
2. Liquidity-scaled size, so a thin pool gets a small position instead of a
   cost rejection.
3. Online learning from closed trades. Every closed trade is filed under a
   few context buckets (tier, hold mode, liquidity, market cap, pool age,
   5-minute move, flow ratio, score, DEX). For a new candidate the buckets'
   recent net results decide its size multiplier, and a bucket that keeps
   losing is avoided until its evidence has aged out.

What it does not do: it cannot know the future, and a bucket that looked
good on a few dozen paper trades can simply have been lucky. Results decay
with a 24-hour half-life so the engine keeps re-testing what it believes.
Thresholds and exits are fixed; only size and avoid/allow are learned.
"""
import math
from types import MappingProxyType

import order_flow_adaptive_oct4 as oct4

PROFILE = 'ORDER_FLOW_ADAPTIVE_LEARNING'
STRATEGY_ID = 'ORDER_FLOW_ADAPTIVE_LEARNING'
STRATEGY_VERSION = 'learner-2026-10-09-fixed-4-10'
ENTRY_POLICY_VERSION = 'ORDER_FLOW_TIERED_LEARNER_V1'
LEARNING_MODE = 'ONLINE_CONTEXT_EXPECTANCY_V1'
EXIT_POLICY = 'fixed'
EXIT_POLICY_VERSION = 'HONEST_NET_EXIT_V1'

CONFIG = MappingProxyType({
    'scan_seconds': 5,
    'position_scan_seconds': 1.0,
    'max_positions': 8,
    'trade_notional_usd': 200.0,           # upper bound; real size is liquidity-scaled
    'max_daily_loss_usd': 0.0,             # no daily gate: size learning is the brake
    # Owner-requested fixed PAPER exits on executable net PnL. Cost/impact
    # preflight still rejects entries whose modeled round trip is too expensive.
    'stop_loss_pct': 4.0,
    'take_profit_pct': 10.0,
    'trailing_pct': 0.0,                   # fixed exit policy does not trail
    'max_hold_minutes': 60,                # fixed policy fallback timeout
    'win_reentry_seconds': 120,
    'loss_reentry_seconds': 600,
    'entry_flow_window_seconds': 60,
    'max_quoted_candidates_per_scan': 6,
    'max_feed_age_ms': 30_000,
    'max_entry_quote_age_ms': 10_000,
    'max_entry_impact_pct': 2.0,
    'max_roundtrip_cost_pct': 4.0,
    'max_worst_case_cost_pct': 6.0,
    'max_position_full_loss_usd': 250.0,
    'max_total_exposure_pct': 100.0,
    'max_drawdown_pct': 0.0,
    'entry_on_position_guard': False,
    'daily_budget_sizing': False,
    # A position with no sell route keeps its capital reserved, but it does not
    # freeze the whole engine or occupy one of the trading slots.
    'unsellable_blocks_entries': False,
})

TIER_LIMITS = MappingProxyType({
    'CORE': oct4.ENTRY_LIMITS,
    'EXPLORE': MappingProxyType({
        'min_score': 70.0,
        'min_liquidity_usd': 10_000.0,
        'min_conviction': 58.0,
        'min_m5_pct': -8.0,
        'max_m5_pct': 40.0,
        'min_flow_trades': 2,
        'min_flow_buy_sell_usd_ratio': 1.10,
        'min_unique_wallets': 1,
        'large_sell_floor_usd': 1000.0,
        'large_sell_buy_fraction': 1.0,
    }),
})

SIZING = MappingProxyType({
    'target_impact_pct': 0.75,     # size so the modeled one-side impact stays near this
    'explore_fraction': 0.5,       # EXPLORE trades at half the CORE size
    'min_notional_usd': 40.0,      # below this the fixed account/network costs dominate
    'max_notional_usd': 200.0,
})

LEARNING = MappingProxyType({
    'half_life_hours': 24.0,       # a result counts half as much a day later
    'max_trades': 400,             # most recent closed trades considered
    'prior_trades': 8.0,           # shrinkage: a bucket starts at zero edge
    'min_bucket_weight': 5.0,      # weighted trades before a bucket has a say
    'edge_scale_pct': 4.0,         # +4% learned edge -> +1.0 on the multiplier
    'min_multiplier': 0.25,
    'max_multiplier': 1.5,
    'avoid_min_weight': 8.0,
    'avoid_mean_pct': -2.5,
    'avoid_win_rate': 0.30,
    # Only a distinguishing context can be skipped. A bucket that holds most of
    # the recent trades just shrinks the size, so a bad day never halts trading.
    'avoid_max_share': 0.60,
    'loss_streak': 5,              # this many straight losses halves every size
    'loss_streak_multiplier': 0.5,
})

# The engine ignores same-named environment variables while this profile is active.
OWNED_ENV = oct4.OWNED_ENV


def _n(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def entry_tier(coin, flow, context, *, now):
    """Return (tier, rejections). `rejections` names what kept it out of EXPLORE."""
    if not oct4.signal_rejections(coin, flow, context, now=now, limits=TIER_LIMITS['CORE'],
                                  max_feed_age_ms=CONFIG['max_feed_age_ms']):
        return 'CORE', []
    rejected = oct4.signal_rejections(coin, flow, context, now=now, limits=TIER_LIMITS['EXPLORE'],
                                      max_feed_age_ms=CONFIG['max_feed_age_ms'])
    return (None, rejected) if rejected else ('EXPLORE', [])


def base_notional(coin, tier):
    """Liquidity-scaled paper size before the learned multiplier."""
    liquidity = _n(coin.get('liquidityUsd'))
    # Constant-product impact per side is roughly 2 * notional / liquidity.
    size = liquidity * SIZING['target_impact_pct'] / 200.0
    if tier != 'CORE':
        size *= SIZING['explore_fraction']
    return round(max(SIZING['min_notional_usd'], min(SIZING['max_notional_usd'], size)), 2)


def _band(value, edges, labels):
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def _buckets(*, tier, mode, liquidity, market_cap, age, m5, ratio, score, dex):
    return {
        'tier': str(tier or 'UNKNOWN'),
        'mode': str(mode or 'UNKNOWN'),
        'liquidity': _band(liquidity, (20_000, 50_000, 150_000), ('<20k', '20-50k', '50-150k', '150k+')),
        'market_cap': _band(market_cap, (100_000, 1_000_000), ('<100k', '100k-1M', '1M+')),
        'age': _band(age, (30, 180, 1440), ('<30m', '30m-3h', '3-24h', '24h+')),
        'm5': _band(m5, (0, 5, 12), ('<0', '0-5', '5-12', '12+')),
        'flow_ratio': _band(ratio, (1.5, 2.5), ('<1.5', '1.5-2.5', '2.5+')),
        'score': _band(score, (80, 90), ('<80', '80-90', '90+')),
        'dex': 'pumpswap' if str(dex or '').lower() == 'pumpswap' else 'other',
    }


def features(coin, flow, context, tier):
    """Context buckets of an entry candidate."""
    return _buckets(
        tier=tier, mode=context.get('mode'), liquidity=_n(coin.get('liquidityUsd')),
        market_cap=_n(coin.get('marketCap') or coin.get('fdv')), age=_n(coin.get('ageMinutes'), 1e9),
        m5=_n((coin.get('priceChange') or {}).get('m5')), ratio=_n(flow.get('buy_sell_usd_ratio')),
        score=_n(coin.get('score')), dex=coin.get('dexId'))


def features_from_trade(trade):
    """Buckets of a closed trade: recorded at entry, or rebuilt for older trades."""
    recorded = trade.get('learn_features')
    if isinstance(recorded, dict) and recorded:
        return {str(k): str(v) for k, v in recorded.items()}
    snapshot = trade.get('coin_snapshot') or {}
    mode = str(trade.get('entry_mode') or '')
    return _buckets(
        tier=mode if mode in TIER_LIMITS else 'LEGACY', mode=trade.get('entry_hold_mode'),
        liquidity=_n(trade.get('entry_liquidity_usd'), _n(snapshot.get('liquidityUsd'))),
        market_cap=_n(trade.get('entry_market_cap'), _n(snapshot.get('marketCap') or snapshot.get('fdv'))),
        age=_n(snapshot.get('ageMinutes'), 1e9),
        m5=_n(trade.get('entry_change_m5'), _n((snapshot.get('priceChange') or {}).get('m5'))),
        ratio=_n((trade.get('entry_flow') or {}).get('buy_sell_usd_ratio')),
        score=_n(trade.get('score'), _n(snapshot.get('score'))), dex=snapshot.get('dexId'))


def usable_trade(trade):
    """Only trades booked with quote evidence teach anything; older fills were not honest."""
    return (isinstance(trade, dict) and bool(trade.get('execution_verification_version'))
            and math.isfinite(_n(trade.get('pnl_pct'), math.nan)) and _n(trade.get('closed_at')) > 0)


def build_table(history, now):
    """Aggregate recent closed trades into per-bucket, time-decayed results."""
    trades = [t for t in history if usable_trade(t)]
    trades.sort(key=lambda t: _n(t.get('closed_at')), reverse=True)
    trades = trades[:LEARNING['max_trades']]
    buckets = {}
    half_life_ms = LEARNING['half_life_hours'] * 3_600_000
    for trade in trades:
        age_ms = max(0.0, now - _n(trade.get('closed_at')))
        weight = 0.5 ** (age_ms / half_life_ms)
        pnl = _n(trade.get('pnl_pct'))
        for dimension, bucket in features_from_trade(trade).items():
            row = buckets.setdefault(f'{dimension}={bucket}', {'weight': 0.0, 'pnl': 0.0, 'wins': 0.0, 'trades': 0})
            row['weight'] += weight
            row['pnl'] += weight * pnl
            row['wins'] += weight * (pnl > 0)
            row['trades'] += 1
    for row in buckets.values():
        row['mean_pct'] = row['pnl'] / (row['weight'] + LEARNING['prior_trades'])
        row['win_rate'] = row['wins'] / row['weight'] if row['weight'] > 0 else 0.0
    streak = 0
    for trade in trades:
        if _n(trade.get('pnl_pct')) > 0:
            break
        streak += 1
    return {'buckets': buckets, 'trades': len(trades), 'loss_streak': streak}


def _avoided(row, table):
    return (row['weight'] >= LEARNING['avoid_min_weight']
            and row['mean_pct'] <= LEARNING['avoid_mean_pct']
            and row['win_rate'] < LEARNING['avoid_win_rate']
            and row['trades'] <= LEARNING['avoid_max_share'] * table['trades'])


def assess(table, candidate_features):
    """Turn what was learned into a size multiplier, or a reason to avoid the entry."""
    evidence, avoid = [], None
    for dimension, bucket in candidate_features.items():
        key = f'{dimension}={bucket}'
        row = table['buckets'].get(key)
        if not row or row['weight'] < LEARNING['min_bucket_weight']:
            continue
        evidence.append((key, row))
        if avoid is None and _avoided(row, table):
            avoid = key
    edge = sum(row['mean_pct'] for _, row in evidence) / len(evidence) if evidence else 0.0
    multiplier = 1.0 + edge / LEARNING['edge_scale_pct']
    streak = table['loss_streak'] >= LEARNING['loss_streak']
    if streak:
        multiplier *= LEARNING['loss_streak_multiplier']
    multiplier = max(LEARNING['min_multiplier'], min(LEARNING['max_multiplier'], multiplier))
    return {
        'edge_pct': round(edge, 3), 'size_multiplier': round(multiplier, 3), 'avoid': avoid,
        'loss_streak_brake': streak, 'sample': table['trades'],
        'evidence': [{'bucket': key, 'trades': row['trades'], 'weight': round(row['weight'], 2),
                      'mean_pct': round(row['mean_pct'], 3), 'win_rate': round(row['win_rate'], 3)}
                     for key, row in evidence],
    }


def summary(table, limit=12):
    """What the engine currently believes, for /state."""
    rows = [{'bucket': key, 'trades': row['trades'], 'weight': round(row['weight'], 2),
             'mean_pct': round(row['mean_pct'], 3), 'win_rate': round(row['win_rate'], 3),
             'avoided': _avoided(row, table)}
            for key, row in table['buckets'].items() if row['weight'] >= LEARNING['min_bucket_weight']]
    rows.sort(key=lambda row: row['mean_pct'], reverse=True)
    return {'mode': LEARNING_MODE, 'trades_used': table['trades'], 'loss_streak': table['loss_streak'],
            'loss_streak_brake': table['loss_streak'] >= LEARNING['loss_streak'],
            'best': rows[:limit], 'worst': rows[-limit:][::-1] if len(rows) > limit else [],
            'avoided': [row['bucket'] for row in rows if row['avoided']]}


def describe():
    return {
        'profile': PROFILE, 'strategy_id': STRATEGY_ID, 'strategy_version': STRATEGY_VERSION,
        'entry_policy_version': ENTRY_POLICY_VERSION, 'learning_mode': LEARNING_MODE,
        'exit_policy': EXIT_POLICY, 'exit_policy_version': EXIT_POLICY_VERSION,
        'config': dict(CONFIG), 'tiers': {name: dict(limits) for name, limits in TIER_LIMITS.items()},
        'sizing': dict(SIZING), 'learning': dict(LEARNING), 'exit_limits': {},
        'hold_modes': [],
    }

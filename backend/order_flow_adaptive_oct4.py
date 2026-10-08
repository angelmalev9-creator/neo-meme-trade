"""ORDER_FLOW_ADAPTIVE — the 2026-10-04 PAPER decision policy, restored as one unit.

This module is pure: no network, no clock, no account state, no swap
submission. It owns exactly four things, each recovered from the 2026-10-04
engine (`5b78efd`, entry policy ORDER_FLOW_BALANCED_V4):

1. the effective configuration (so systemd environment cannot drift it),
2. the entry filter and its named rejection reasons,
3. the conviction / market-context model and adaptive hold modes,
4. the exit decision order.

Everything about *execution* stays in the modern engine: exact pool and mint
validation, rug guard, price integrity, quote evidence, uncapped modeled
losses and the durable ledger. A decision made here is an intent, never a
fill; a planned stop does not promise the proceeds through a price gap.
"""
import math
import re
from types import MappingProxyType

PROFILE = 'ORDER_FLOW_ADAPTIVE_OCT4'
STRATEGY_ID = 'ORDER_FLOW_ADAPTIVE'
STRATEGY_VERSION = 'gold-2026-10-04'
ENTRY_POLICY_VERSION = 'ORDER_FLOW_BALANCED_V4'
ENTRY_MODE = 'BALANCED_V4'
LEARNING_MODE = 'ADAPTIVE_CONTEXT_HOLD'
EXIT_POLICY = 'oct4_adaptive'
# Historical decision order, evaluated on honest net executable marks for the
# stop and on the exact-pool observed price for the context triggers.
EXIT_POLICY_VERSION = 'ADAPTIVE_CONTEXT_HOLD_NET_V1'
SOURCE_COMMIT = '5b78efdba8121f2a8ce11e8ecc400a3e97443ad5'

# One owner for every effective setting. The engine copies these values and
# ignores same-named environment variables while this profile is active.
CONFIG = MappingProxyType({
    'scan_seconds': 15,
    'position_scan_seconds': 2.0,
    'max_positions': 1,
    'trade_notional_usd': 200.0,
    'max_daily_loss_usd': 100.0,
    'stop_loss_pct': 5.0,
    'take_profit_pct': 18.0,      # base value only; exits use the hold modes
    'trailing_pct': 4.0,          # base value only; exits use the hold modes
    'max_hold_minutes': 7,        # base value only; exits use the hold modes
    'same_token_cooldown_seconds': 20 * 60,
    'entry_flow_window_seconds': 60,
    'max_quoted_candidates_per_scan': 2,
    'max_feed_age_ms': 30_000,
    'max_entry_quote_age_ms': 10_000,
    'max_entry_impact_pct': 2.0,
    'max_roundtrip_cost_pct': 2.75,
    'max_worst_case_cost_pct': 4.50,
    'absolute_max_hold_minutes': 120.0,
    # Capital limits that must not shrink the fixed historical position size.
    'max_position_full_loss_usd': 250.0,
    'max_total_exposure_pct': 100.0,
    'max_drawdown_pct': 0.0,
    # Entries are evaluated once per market scan, as on 2026-10-04.
    'entry_on_position_guard': False,
    # The daily limit is a hard gate; it does not resize the position.
    'daily_budget_sizing': False,
})

ENTRY_LIMITS = MappingProxyType({
    'min_score': 85.0,
    'min_liquidity_usd': 30_000.0,
    'min_conviction': 72.0,
    'min_m5_pct': -3.0,
    'max_m5_pct': 25.0,
    'min_h1_pct': -30.0,
    'max_h1_pct': 150.0,
    'min_market_buy_sell_ratio': 1.0,
    'min_liquidity_market_cap_ratio': 0.03,
    'min_flow_trades': 4,
    'min_flow_buy_sell_usd_ratio': 1.30,
    'min_buy_usd': 150.0,
    'min_unique_wallets': 4,
    'min_buyer_wallets': 3,
    'min_wallet_buy_sell_ratio': 1.0,
    'large_sell_floor_usd': 250.0,
    'large_sell_buy_fraction': 0.50,
})

# (name, minimum conviction, max hold minutes, fixed target %, trail arm %, trail %)
HOLD_MODES = (
    ('RUNNER', 85.0, 60.0, None, 15.0, 7.0),
    ('STRONG', 72.0, 30.0, None, 12.0, 6.0),
    ('NORMAL', 58.0, 15.0, 20.0, 9.0, 5.0),
    ('CAUTIOUS', 45.0, 8.0, 14.0, 7.0, 4.0),
    ('WEAK', 0.0, 4.0, 8.0, 5.0, 3.0),
)

EXIT_LIMITS = MappingProxyType({
    'conviction_exit_below': 35.0,
    'orderflow_min_trades': 4,
    'orderflow_min_sells': 3,
    'orderflow_sell_floor_usd': 200.0,
    'orderflow_sell_buy_multiple': 2.0,
    'orderflow_max_signal_pct': 3.0,
    'profit_lock_peak_pct': 10.0,
    'profit_lock_conviction_below': 50.0,
    'profit_lock_min_profit_pct': 2.0,
    'max_hold_conviction_below': 72.0,
})

_FLOW_QUALITY_OK = frozenset({'GOOD', 'COMPLETE', 'HEALTHY', 'VALID'})
_ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

# Environment names this profile owns. If any is set, the engine reports it as
# ignored instead of letting it silently change the strategy.
OWNED_ENV = (
    'NEO_SCAN_SECONDS', 'NEO_POSITION_SCAN_SECONDS', 'NEO_MAX_POSITIONS',
    'NEO_WIN_REENTRY_SECONDS', 'NEO_LOSS_REENTRY_SECONDS', 'NEO_ENTRY_COOLDOWN_SECONDS',
    'NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD', 'NEO_MAX_POSITION_RISK_USD',
    'NEO_MAX_TOTAL_EXPOSURE_PCT', 'NEO_MAX_DRAWDOWN_PCT', 'NEO_STOP_LOSS_PCT',
    'NEO_TAKE_PROFIT_PCT', 'NEO_TRAILING_PCT', 'NEO_MAX_HOLD_MINUTES',
    'NEO_TARGET_TRADES_PER_HOUR', 'NEO_STRICT_ENTRY_SCORE', 'NEO_STRICT_MIN_CONVICTION',
    'NEO_STRICT_MIN_LIQUIDITY_USD', 'NEO_STRICT_MAX_ENTRY_IMPACT_PCT',
    'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT', 'NEO_STRICT_MAX_WORST_CASE_COST_PCT',
)


def _n(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def signal_rejections(coin, flow, context, *, now, limits=ENTRY_LIMITS,
                      max_feed_age_ms=CONFIG['max_feed_age_ms']):
    """Return every failed ORDER_FLOW_BALANCED_V4 check, in a stable order.

    Missing or non-finite inputs fail their check: absent evidence never
    counts as clearance. Rug, price-integrity and quote checks run afterwards
    in the engine and are not replaced by this filter.
    """
    changes = coin.get('priceChange') or {}
    tx = (coin.get('txns') or {}).get('m5') or {}
    liquidity = _n(coin.get('liquidityUsd'))
    market_cap = _n(coin.get('marketCap') or coin.get('fdv'))
    observed = _n(coin.get('updatedAt'))
    market_ratio = _n(tx.get('buys')) / max(_n(tx.get('sells')), 1.0)
    buy_usd = _n(flow.get('buy_usd'))
    quality = str(flow.get('quality', flow.get('status', 'UNKNOWN'))).upper()
    checks = (
        ('invalid_pair', bool(_ADDRESS.fullmatch(str(coin.get('address') or '')))
                         and bool(_ADDRESS.fullmatch(str(coin.get('pairAddress') or '')))),
        ('invalid_price', _n(coin.get('priceUsd')) > 0),
        ('stale_feed', observed > 0 and 0 <= now - observed <= max_feed_age_ms),
        ('flow_quality', quality in _FLOW_QUALITY_OK),
        ('score', _n(coin.get('score')) >= limits['min_score']),
        ('liquidity', liquidity >= limits['min_liquidity_usd']),
        ('momentum', limits['min_m5_pct'] <= _n(changes.get('m5'), -999.0) <= limits['max_m5_pct']),
        ('hour_trend', limits['min_h1_pct'] <= _n(changes.get('h1'), -999.0) <= limits['max_h1_pct']),
        ('market_buyers', market_ratio >= limits['min_market_buy_sell_ratio']),
        ('liquidity_ratio', market_cap > 0
                            and liquidity / market_cap >= limits['min_liquidity_market_cap_ratio']),
        ('flow_count', _n(flow.get('trades')) >= limits['min_flow_trades']),
        ('flow_ratio', _n(flow.get('buy_sell_usd_ratio')) >= limits['min_flow_buy_sell_usd_ratio']),
        ('buy_volume', buy_usd >= limits['min_buy_usd']),
        ('wallet_count', _n(flow.get('unique_wallets')) >= limits['min_unique_wallets']),
        ('buyer_count', _n(flow.get('buyer_wallets')) >= limits['min_buyer_wallets']),
        ('wallet_ratio', _n(flow.get('wallet_buy_sell_ratio')) >= limits['min_wallet_buy_sell_ratio']),
        ('large_sells', _n(flow.get('max_sell_usd'), math.inf)
                        < max(limits['large_sell_floor_usd'], buy_usd * limits['large_sell_buy_fraction'])),
        ('conviction', _n(context.get('conviction'), -1.0) >= limits['min_conviction']),
    )
    return [name for name, passed in checks if not passed]


def hold_mode(conviction):
    """Map conviction to the adaptive hold mode."""
    conviction = _n(conviction)
    for index, (name, minimum, max_hold, target, trail_arm, trail) in enumerate(HOLD_MODES):
        if conviction >= minimum or index == len(HOLD_MODES) - 1:
            return {'mode': name, 'max_hold_minutes': max_hold, 'target_pct': target,
                    'trail_arm_pct': trail_arm, 'trail_pct': trail}


def conviction_score(coin, fast, slow, entry_liquidity=None):
    """The 2026-10-04 conviction model: start at 50, adjust, clamp to 0..100."""
    changes = coin.get('priceChange') or {}
    tx_m5 = (coin.get('txns') or {}).get('m5') or {}
    m5 = _n(changes.get('m5'))
    h1 = _n(changes.get('h1'))
    market_ratio = _n(tx_m5.get('buys')) / max(_n(tx_m5.get('sells')), 1.0)
    liquidity = _n(coin.get('liquidityUsd'))
    liquidity_ratio = liquidity / max(_n(entry_liquidity, liquidity), 1.0)
    score = 50.0

    fast_ratio = _n(fast.get('buy_sell_usd_ratio'))
    if fast_ratio >= 3.0: score += 18
    elif fast_ratio >= 2.0: score += 12
    elif fast_ratio >= 1.4: score += 6
    elif fast_ratio < 0.8: score -= 20
    elif fast_ratio < 1.0: score -= 10

    slow_ratio = _n(slow.get('buy_sell_usd_ratio'))
    if slow_ratio >= 2.0: score += 12
    elif slow_ratio >= 1.4: score += 7
    elif slow_ratio < 0.8: score -= 15
    elif slow_ratio < 1.0: score -= 7

    wallets = _n(slow.get('unique_wallets'))
    if wallets >= 10: score += 6
    elif wallets >= 5: score += 3
    elif wallets <= 2: score -= 5

    repeat_buyers = _n(slow.get('repeat_buy_wallets'))
    if repeat_buyers >= 3: score += 6
    elif repeat_buyers >= 1: score += 3

    whale_buy = _n(slow.get('whale_buy_usd'))
    whale_sell = _n(slow.get('whale_sell_usd'))
    if whale_buy > 0 and whale_buy >= whale_sell * 1.3: score += 6
    elif whale_sell > 0 and whale_sell >= max(whale_buy * 1.3, 750.0): score -= 8

    if 0 <= m5 <= 10: score += 8
    elif -2 <= m5 < 0: score += 2
    elif m5 > 20: score -= 8
    elif m5 < -5: score -= 15

    if 0 <= h1 <= 120: score += 4
    elif h1 < -15: score -= 8
    elif h1 > 250: score -= 5

    if market_ratio >= 1.4: score += 8
    elif market_ratio >= 1.1: score += 4
    elif market_ratio < 0.8: score -= 8

    if liquidity_ratio >= 0.95: score += 3
    elif liquidity_ratio < 0.80: score -= 15
    elif liquidity_ratio < 0.90: score -= 7

    neo_score = _n(coin.get('score'))
    if neo_score >= 95: score += 4
    elif neo_score < 85: score -= 4

    return {
        'conviction': round(max(0.0, min(100.0, score)), 1),
        'm5': round(m5, 3), 'h1': round(h1, 3),
        'market_buy_sell_ratio': round(market_ratio, 3),
        'liquidity_ratio_vs_entry': round(liquidity_ratio, 3),
    }


def market_context(coin, fast, slow, entry_liquidity=None):
    """Conviction, hold mode and the flow evidence that produced them."""
    scored = conviction_score(coin, fast, slow, entry_liquidity)
    mode = hold_mode(scored['conviction'])
    return {
        'conviction': scored['conviction'], **mode,
        'm5': scored['m5'], 'h1': scored['h1'],
        'market_buy_sell_ratio': scored['market_buy_sell_ratio'],
        'liquidity_ratio_vs_entry': scored['liquidity_ratio_vs_entry'],
        'fast_flow': fast, 'slow_flow': slow,
        'holder_proxy': {
            'unique_wallets_5m': slow.get('unique_wallets', 0),
            'repeat_buy_wallets_5m': slow.get('repeat_buy_wallets', 0),
            'wallet_buy_sell_ratio_5m': slow.get('wallet_buy_sell_ratio', 0),
            'whale_buy_usd_5m': slow.get('whale_buy_usd', 0),
            'whale_sell_usd_5m': slow.get('whale_sell_usd', 0),
        },
    }


def exit_reason(context, *, net_pct, peak_net_pct, hold_minutes,
                signal_pct=None, peak_signal_pct=None,
                stop_pct=CONFIG['stop_loss_pct'], limits=EXIT_LIMITS):
    """Decide whether to exit, in the 2026-10-04 priority order.

    `net_pct` is the net PnL of a full simulated sale right now. `signal_pct`
    is the move of the exact entry pool's observed price since entry. The stop
    uses only the net figure. The context triggers use the observed price as
    they did historically, and fall back to the net figure whenever a fresh
    observed price is unavailable (pass None) so a stale chart cannot hold a
    position open or fake a profit.
    """
    net_pct = _n(net_pct, -math.inf)
    if net_pct <= -stop_pct:
        return 'STOP_LOSS'
    signal = net_pct if signal_pct is None else _n(signal_pct, net_pct)
    peak = _n(peak_net_pct, net_pct) if signal_pct is None else _n(peak_signal_pct, signal)
    peak = max(peak, signal)
    conviction = _n(context.get('conviction'))
    fast = context.get('fast_flow') or {}

    if conviction < limits['conviction_exit_below'] and signal < 0:
        return 'CONVICTION_EXIT'
    if (_n(fast.get('trades')) >= limits['orderflow_min_trades']
            and _n(fast.get('sells')) >= limits['orderflow_min_sells']
            and _n(fast.get('sell_usd')) >= max(limits['orderflow_sell_floor_usd'],
                                                _n(fast.get('buy_usd')) * limits['orderflow_sell_buy_multiple'])
            and signal < limits['orderflow_max_signal_pct']):
        return 'ORDERFLOW_EXIT'
    target = context.get('target_pct')
    if target is not None and signal >= _n(target, math.inf):
        return f'ADAPTIVE_TP_{_n(target):.0f}'
    if (peak >= limits['profit_lock_peak_pct']
            and conviction < limits['profit_lock_conviction_below']
            and signal > limits['profit_lock_min_profit_pct']):
        return 'CONVICTION_PROFIT_LOCK'
    trail_arm = _n(context.get('trail_arm_pct'), 8.0)
    trail = _n(context.get('trail_pct'), CONFIG['trailing_pct'])
    # price <= peak_price * (1 - trail/100), expressed in percent since entry.
    if peak >= trail_arm and 100.0 + signal <= (100.0 + peak) * (1.0 - trail / 100.0):
        return 'ADAPTIVE_TRAILING'
    max_hold = _n(context.get('max_hold_minutes'), CONFIG['max_hold_minutes'])
    if hold_minutes >= max_hold and conviction < limits['max_hold_conviction_below']:
        return 'ADAPTIVE_MAX_HOLD'
    if hold_minutes >= CONFIG['absolute_max_hold_minutes']:
        return 'ABSOLUTE_MAX_HOLD'
    return None


def cooldown_addresses(history, now, seconds=CONFIG['same_token_cooldown_seconds']):
    """Tokens closed within the cooldown, win or loss alike."""
    cutoff = now - seconds * 1000
    blocked = set()
    for trade in history:
        address = trade.get('address')
        closed_at = _n(trade.get('closed_at'))
        if address and closed_at > 0 and closed_at >= cutoff:
            blocked.add(address)
    return blocked


def describe():
    """Static, serialisable description for /state and the strategy lock."""
    return {
        'profile': PROFILE, 'strategy_id': STRATEGY_ID, 'strategy_version': STRATEGY_VERSION,
        'entry_policy_version': ENTRY_POLICY_VERSION, 'learning_mode': LEARNING_MODE,
        'exit_policy': EXIT_POLICY, 'exit_policy_version': EXIT_POLICY_VERSION,
        'source_commit': SOURCE_COMMIT, 'config': dict(CONFIG),
        'entry_limits': dict(ENTRY_LIMITS), 'exit_limits': dict(EXIT_LIMITS),
        'hold_modes': [
            {'mode': name, 'min_conviction': minimum, 'max_hold_minutes': max_hold,
             'target_pct': target, 'trail_arm_pct': trail_arm, 'trail_pct': trail}
            for name, minimum, max_hold, target, trail_arm, trail in HOLD_MODES
        ],
    }

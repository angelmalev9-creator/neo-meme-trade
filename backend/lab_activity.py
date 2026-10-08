"""Activity controls for the isolated paper Strategy Lab only.

No live order execution. These are experimental entry filters, not a promised
win rate. Fees and fill assumptions remain in strategy_lab.py unchanged.
"""
from dataclasses import dataclass
import math
import re
from typing import Any, Callable

POLICY_VERSION = 'LAB_ACTIVE_V3_MOMENTUM_HUNTER'
REENTRY_SECONDS = 60
LOSS_REENTRY_SECONDS = 180
MAX_FEED_AGE_MS = 20_000
MAX_ENTRY_COST_PCT = 2.75
MIN_NOTIONAL_USD = 10.0
ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(frozen=True)
class EntryRule:
    score: float
    liquidity: float
    move: tuple[float, float]
    buy_sell: float = 0.9
    liquidity_cap: float = 0.05
    age: tuple[float, float] = (2, 1440)
    hour: tuple[float, float] = (-100, 1000)
    volume_liquidity: tuple[float, float] = (0, 1_000_000)
    flow_trades: int = 0
    flow_ratio: float = 0
    flow_buy: float = 0
    wallets: int = 0
    max_sell: float = math.inf
    market_cap: float = 0

    def matches(self, f: dict[str, Any]) -> bool:
        flow = f.get('flow') or {}
        return (
            number(f.get('score')) >= self.score
            and number(f.get('liq')) >= max(10_000, self.liquidity)
            and self.move[0] <= number(f.get('m5'), -math.inf) <= self.move[1]
            and number(f.get('bs')) >= self.buy_sell
            and number(f.get('lmc')) >= self.liquidity_cap
            and self.age[0] <= number(f.get('age'), math.inf) <= self.age[1]
            and self.hour[0] <= number(f.get('h1'), -math.inf) <= self.hour[1]
            and self.volume_liquidity[0] <= number(f.get('vol_liq')) <= self.volume_liquidity[1]
            and number(flow.get('trades')) >= self.flow_trades
            and number(flow.get('ratio')) >= self.flow_ratio
            and number(flow.get('buy_usd')) >= self.flow_buy
            and number(flow.get('unique_wallets')) >= self.wallets
            and number(flow.get('max_sell')) <= self.max_sell
            and number(f.get('mc')) >= self.market_cap
        )


RULES = {
    'ULTRA_PRECISION': EntryRule(93, 25000, (1, 25), 1.0, .12, (5, 360)),
    'PRECISION': EntryRule(90, 20000, (0, 30), .95, .09, (3, 480)),
    'MOMENTUM': EntryRule(85, 15000, (3, 40), 1.05, .06, (2, 720)),
    # Broader PAPER-only momentum funnel; global feed/price/cost safety stays unchanged.
    'MOMENTUM_HUNTER': EntryRule(78, 10000, (.5, 45), .90, .03, (2, 1440), (-35, 350), (.03, 1000)),
    'BREAKOUT': EntryRule(86, 20000, (10, 60), 1.2, .06, (2, 720)),
    'LIQUIDITY': EntryRule(80, 40000, (-3, 25), .85, .08, (3, 1440)),
    'ORDER_FLOW': EntryRule(80, 15000, (-5, 30), .8, .04, (2, 1e7), flow_trades=3, flow_ratio=1.2, wallets=2),
    'EARLY': EntryRule(85, 15000, (0, 25), .95, .08, (2, 120)),
    'TREND': EntryRule(83, 20000, (-2, 20), .85, .07, (5, 960), (-5, 200)),
    'SCALPER': EntryRule(78, 10000, (-5, 20), .85, .04, (2, 720)),
    'VOLUME_SURGE': EntryRule(83, 15000, (1, 35), 1.0, .06, (2, 720), volume_liquidity=(.2, 1e6)),
    'REVERSAL': EntryRule(80, 20000, (-12, 6), 1.0, .08, (5, 960), (-35, 1000)),
    'FLOW_MOMENTUM': EntryRule(80, 15000, (-1, 35), .8, .04, (2, 1e7), flow_trades=3, flow_ratio=1.5, flow_buy=100),
    'FLOW_MOMENTUM_SCALE_OUT': EntryRule(81, 15000, (0, 30), .9, .04, (2, 1e7), flow_trades=4, flow_ratio=1.5, flow_buy=100),
    'FLOW_ELITE': EntryRule(87, 30000, (-1, 25), 1, .06, (2, 1e7), flow_trades=4, flow_ratio=1.8, flow_buy=180, wallets=3),
    'FLOW_SNIPER': EntryRule(90, 25000, (0, 18), 1.05, .07, (2, 1e7), flow_trades=4, flow_ratio=2.0, flow_buy=200, wallets=4),
    'LIQ_FLOW_CONFLUENCE': EntryRule(85, 50000, (-2, 22), .95, .09, (2, 1e7), flow_trades=3, flow_ratio=1.5, flow_buy=150),
    'BREAKOUT_CONFIRM': EntryRule(87, 30000, (7, 40), 1.15, .06, (2, 1e7), volume_liquidity=(.1, 8), flow_trades=3, flow_ratio=1.3, flow_buy=150),
    'EARLY_FLOW': EntryRule(87, 22000, (0, 22), .95, .09, (2, 150), flow_trades=3, flow_ratio=1.5, wallets=2),
    'TREND_FLOW': EntryRule(85, 30000, (0, 18), 1.0, .06, (2, 1e7), (-5, 120), flow_trades=3, flow_ratio=1.4),
    'DEEP_LIQ_MOMENTUM': EntryRule(85, 80000, (1, 28), 1.0, .05, (2, 1e7), volume_liquidity=(.06, 7)),
    'LOW_VOL_FLOW': EntryRule(85, 30000, (-2, 12), .95, .06, (2, 1e7), (-30, 1000), flow_trades=4, flow_ratio=1.6),
    'HIGH_LMC_FLOW': EntryRule(85, 25000, (-1, 22), .95, .17, (2, 1e7), flow_trades=3, flow_ratio=1.3),
    'VOLUME_QUALITY': EntryRule(87, 30000, (0, 25), 1.05, .06, (2, 1e7), volume_liquidity=(.2, 6), flow_ratio=1.25),
    'BUY_PRESSURE': EntryRule(85, 25000, (-1, 25), 1.25, .06, (2, 1e7), flow_trades=4, flow_ratio=1.6, flow_buy=180),
    'SELL_WALL_SAFE': EntryRule(85, 30000, (-1, 22), 1.0, .06, (2, 1e7), flow_trades=4, flow_ratio=1.5, flow_buy=150, max_sell=250),
    'MICRO_BREAKOUT_SAFE': EntryRule(89, 35000, (3, 22), 1.1, .12, (2, 1e7), flow_ratio=1.3),
    'MATURE_FLOW': EntryRule(85, 40000, (-2, 20), .95, .06, (20, 1440), (-40, 1000), flow_trades=3, flow_ratio=1.5),
    'YOUNG_LIQ': EntryRule(88, 35000, (-1, 22), .95, .12, (2, 180), flow_ratio=1.25),
    'HIGH_SCORE_FLOW': EntryRule(92, 20000, (-2, 22), .95, .06, (2, 1e7), flow_trades=3, flow_ratio=1.3, flow_buy=120),
    'FLOW_PULLBACK': EntryRule(85, 30000, (-6, 6), .95, .06, (2, 1e7), (-5, 1000), flow_trades=4, flow_ratio=1.6, flow_buy=180),
    'SECOND_WAVE': EntryRule(85, 30000, (1, 18), 1.0, .06, (2, 1e7), (5, 180), (.1, 7), flow_trades=3, flow_ratio=1.4),
    'CLEAN_MOMENTUM': EntryRule(87, 30000, (1, 25), 1.05, .06, (2, 1e7), volume_liquidity=(.12, 5), flow_ratio=1.25),
    'CONFLUENCE_MAX': EntryRule(90, 40000, (0, 18), 1.1, .12, (2, 1e7), (-15, 1000), (.12, 6), flow_trades=4, flow_ratio=1.6, wallets=3),
    # TikTok Strategy: PAPER sniper with the lightest entry filter in the lab.
    # Any pool from the first minute, market cap of at least $30k, no order-flow
    # requirement. Feed, price-integrity and liquidity-floor safety are unchanged.
    'TIKTOK': EntryRule(65, 10000, (-5, 60), .80, .02, (0, 1440), (-50, 1000), market_cap=30000),
}

# Per-strategy overrides. Everything not listed uses the lab-wide defaults.
EXIT_OVERRIDES = {
    'TIKTOK': {'stop_loss': 12.0, 'take_profit': 17.0},
}
# Maximum modeled round-trip cost at entry. The pool fee is whatever the pool
# charges (PumpSwap is about 1.25% per side near a $30k market cap), so a
# strategy allowed to enter that low needs room above the 2.75% default.
ENTRY_COST_CAPS = {
    'TIKTOK': 4.0,
}
# Books whose open position is re-checked on the fast interval.
SNIPER_IDS = frozenset({'TIKTOK'})
# Books whose take profit is a resting limit order: once the net mark reaches
# the target the sale is booked at the target, never above it. The stop is a
# market sale and is always booked at the observed mark, including any gap.
LIMIT_TAKE_PROFIT_IDS = frozenset({'TIKTOK'})
SNIPER_POLL_SECONDS = 1.0


def exit_rules(strategy_id: str, stop_loss: float, take_profit: float, max_hold_minutes: float) -> dict:
    """Net exit thresholds for one book: lab defaults plus its overrides."""
    override = EXIT_OVERRIDES.get(strategy_id, {})
    return {'stop_loss': float(override.get('stop_loss', stop_loss)),
            'take_profit': float(override.get('take_profit', take_profit)),
            'max_hold_minutes': float(override.get('max_hold_minutes', max_hold_minutes))}


def entry_cost_cap(strategy_id: str) -> float:
    return float(ENTRY_COST_CAPS.get(strategy_id, MAX_ENTRY_COST_PCT))


MOMENTUM_HUNTER_MIN_RANK = 32.0


def momentum_hunter_rank(f: dict[str, Any], entry_cost_pct: float = 0.0) -> float:
    """Rank broad PAPER momentum candidates using current/past-only features."""
    flow = f.get('flow') or {}

    def clamp01(value: float) -> float:
        return max(0.0, min(1.0, number(value)))

    score = clamp01((number(f.get('score')) - 70.0) / 30.0)
    m5 = number(f.get('m5'))
    momentum = clamp01(m5 / 12.0) if m5 <= 12 else clamp01(1.0 - (m5 - 12.0) / 38.0)
    trend = clamp01((number(f.get('h1')) + 20.0) / 100.0)
    buy_sell = clamp01((number(f.get('bs')) - .80) / 1.20)
    flow_ratio = clamp01((number(flow.get('ratio')) - .80) / 2.20)
    tape = clamp01(number(flow.get('trades')) / 10.0)
    wallets = clamp01(number(flow.get('unique_wallets')) / 6.0)
    volume = clamp01(number(f.get('vol_liq')) / .50)
    lmc = clamp01(number(f.get('lmc')) / .15)
    cost = clamp01((MAX_ENTRY_COST_PCT + number(entry_cost_pct)) / MAX_ENTRY_COST_PCT)

    buy_usd = number(flow.get('buy_usd'))
    max_sell = number(flow.get('max_sell'))
    sell_pressure = clamp01(max_sell / max(buy_usd, 1.0))

    rank = (
        .17 * score + .20 * momentum + .08 * trend + .12 * buy_sell
        + .14 * flow_ratio + .08 * tape + .06 * wallets + .05 * volume
        + .04 * lmc + .06 * cost - .08 * sell_pressure
    )
    return round(max(0.0, min(1.0, rank)) * 100.0, 4)


def cooldown_remaining_ms(book: dict, address: str, now: int) -> int:
    latest = max((t for t in book.get('history', []) if t.get('address') == address),
                 key=lambda t: number(t.get('closed_at')), default=None)
    if latest is not None:
        seconds = LOSS_REENTRY_SECONDS if number(latest.get('pnl_usd')) < 0 else REENTRY_SECONDS
        return max(0, int(number(latest.get('closed_at'))) + seconds * 1000 - now)
    last = number((book.get('last_entry_by_address') or {}).get(address))
    return max(0, int(last) + REENTRY_SECONDS * 1000 - now) if last else 0


def usable_feed_coin(coin: dict, now: int) -> bool:
    stamp = number(coin.get('updatedAt'))
    return (bool(ADDRESS.fullmatch(str(coin.get('address') or '')))
            and bool(ADDRESS.fullmatch(str(coin.get('pairAddress') or '')))
            and number(coin.get('priceUsd')) > 0 and number(coin.get('liquidityUsd')) >= 10000
            and stamp > 0 and -5000 <= now - stamp <= MAX_FEED_AGE_MS)


def affordable_entry(coin: dict, balance: float, limit: float,
                     entry: Callable, exit: Callable,
                     max_cost_pct: float = MAX_ENTRY_COST_PCT) -> dict | None:
    """Try smaller paper sizes without changing the cost model or using leverage."""
    balance, limit = number(balance), number(limit)
    if min(balance, limit) < MIN_NOTIONAL_USD:
        return None
    probe = entry(coin, MIN_NOTIONAL_USD)
    network = number(probe.get('network_fee_usd'), math.inf)
    cap = math.floor(min(limit, balance - network) * 100) / 100
    size = cap
    attempted = set()
    for _ in range(50):
        if size < MIN_NOTIONAL_USD:
            return None
        size = max(MIN_NOTIONAL_USD, math.floor(size * 100) / 100)
        if size in attempted:
            return None
        attempted.add(size)
        opening = entry(coin, size)
        committed = number(opening.get('capital_committed_usd'), math.inf)
        quantity = number(opening.get('quantity'))
        closing = exit(coin, quantity)
        net = number(closing.get('net_proceeds_usd')) - committed
        pct = net / size * 100
        if quantity > 0 and committed <= balance + 1e-9 and -max_cost_pct <= pct <= 0:
            return {'notional': size, 'entry': opening, 'mark': closing,
                    'initial_pnl_usd': net, 'initial_pnl_pct': pct}
        if size == MIN_NOTIONAL_USD:
            return None
        size = max(MIN_NOTIONAL_USD, size * 0.85)
    return None


def policy_config() -> dict:
    return {'version': POLICY_VERSION, 'reentry_seconds': REENTRY_SECONDS,
            'loss_reentry_seconds': LOSS_REENTRY_SECONDS,
            'max_entry_roundtrip_cost_pct': MAX_ENTRY_COST_PCT,
            'feed_max_age_seconds': MAX_FEED_AGE_MS / 1000,
            'exit_overrides': EXIT_OVERRIDES, 'entry_cost_caps': ENTRY_COST_CAPS,
            'sniper_ids': sorted(SNIPER_IDS), 'limit_take_profit_ids': sorted(LIMIT_TAKE_PROFIT_IDS), 'sniper_poll_seconds': SNIPER_POLL_SECONDS,
            'execution_basis': 'ESTIMATED_PAPER_COSTS_NOT_LIVE_FILLS'}

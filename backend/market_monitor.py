#!/usr/bin/env python3
import copy, hashlib, json, math, os, re, shutil, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
import requests
import engine_execution as paper_quotes
import engine_rug_guard as rug_guard
import engine_entry_policy as entry_policy
import gold_order_flow as order_flow
import pair_price_integrity as price_integrity
import pumpswap_stop_quote as pumpswap_stop
import coin_flow
import coin_wallets
import hype_radar
import discovery_universe
import engine_runtime as runtime
import engine_exit_policy as exit_policy
import order_flow_adaptive_oct4 as oct4
import adaptive_learning as learner
import training_bridge
from lab_dashboard_projection import book_trades, compact_strategy_lab

HOST = os.getenv('NEO_MONITOR_HOST', '127.0.0.1')
PORT = int(os.getenv('NEO_MONITOR_PORT', '8788'))
SCAN_SECONDS = max(2, int(os.getenv('NEO_SCAN_SECONDS', '3')))
POSITION_SCAN_SECONDS = float(os.getenv('NEO_POSITION_SCAN_SECONDS', '0.5'))
STATE_PATH = Path(os.getenv('NEO_MARKET_STATE_PATH', '/var/lib/neo-market/state.json'))
AUDIT_PATH = Path(os.getenv('NEO_MARKET_AUDIT_PATH', '/var/lib/neo-market/audit.jsonl'))
LIVE_TAPE_PATH = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
STRATEGY_LAB_PATH = Path(os.getenv('NEO_STRATEGY_LAB_PATH', '/var/lib/neo-market/strategy_lab.json'))
STRATEGY_LAB_COMPACT_PATH = Path(os.getenv('NEO_STRATEGY_LAB_COMPACT_PATH', str(STRATEGY_LAB_PATH.parent / 'strategy_lab_compact.json')))
ALL_TIME_HISTORY_PATH = Path(os.getenv('NEO_ALL_TIME_HISTORY_PATH', str(STATE_PATH.with_name('all_time_history.json'))))
DISCOVERY_UNIVERSE_PATH = Path(os.getenv('NEO_DISCOVERY_UNIVERSE_PATH', str(STATE_PATH.with_name('discovery_universe.json'))))
POSITION_SCAN_SECONDS = float(os.getenv('NEO_POSITION_SCAN_SECONDS', '0.5'))
DEX_API = 'https://api.dexscreener.com'
DISCOVERY_UNIVERSE_MAX = max(240, int(os.getenv('NEO_DISCOVERY_UNIVERSE_MAX', '5000')))
DISCOVERY_SCAN_BATCH = max(60, min(DISCOVERY_UNIVERSE_MAX, int(os.getenv('NEO_DISCOVERY_SCAN_BATCH', '240'))))
DISCOVERY_UNIVERSE_TTL_MS = max(60_000, int(float(os.getenv('NEO_DISCOVERY_UNIVERSE_TTL_MS', '86400000'))))
DISCOVERY_REFRESH_SECONDS = max(4.0, float(os.getenv('NEO_DISCOVERY_REFRESH_SECONDS', '6')))
MARKET_BATCH_WORKERS = max(1, min(6, int(os.getenv('NEO_MARKET_BATCH_WORKERS', '4'))))
MARKET_BATCH_TIMEOUT_SECONDS = max(2.0, min(8.0, float(os.getenv('NEO_MARKET_BATCH_TIMEOUT_SECONDS', '5'))))
_BACKEND_HEALTH_LOCK = threading.Lock()
_MARKET_DATA_HEALTH: dict[str, Any] = {
    'status': 'starting', 'checked_at': 0, 'requested_tokens': 0,
    'unavailable_tokens': 0, 'returned_pairs': 0,
}
_DISCOVERY_HEALTH: dict[str, Any] = {
    'status': 'starting', 'checked_at': 0, 'sources_ok': 0,
    'sources_failed': 0, 'last_error': None,
}
MAX_FEED = DISCOVERY_SCAN_BATCH
ENTRY_SCORE = 60.0
MAX_POSITIONS = max(1, int(os.getenv('NEO_MAX_POSITIONS', '16')))
WIN_REENTRY_SECONDS = max(0, int(float(os.getenv('NEO_WIN_REENTRY_SECONDS', '120'))))
LOSS_REENTRY_SECONDS = max(WIN_REENTRY_SECONDS, int(float(os.getenv('NEO_LOSS_REENTRY_SECONDS', '300'))))
STOP_LOSS_PCT = 5.0
STOP_EXECUTION_BUFFER_PCT = 0.5  # Planned risk allowance, never a fill clamp.
STOP_EXECUTION_ARM_NET_PCT = 5.0
EXIT_IMPACT_EMERGENCY_PCT = 0.75
TAKE_PROFIT_PCT = 10.0
TRAILING_PCT = 4.0
MAX_HOLD_MINUTES = 60
WEAK_CHECK_MINUTES = 5
STALE_EXIT_MINUTES = 7
STALE_MIN_PROFIT_PCT = 3.0
LEARNING_WINDOW = 60
HEALTH_WINDOW = 12
STARTING_BALANCE_USD = 1000.0
TRADE_NOTIONAL_USD = float(os.getenv('NEO_TRADE_NOTIONAL_USD', '200'))
MAX_DAILY_LOSS_USD = float(os.getenv('NEO_MAX_DAILY_LOSS_USD', '0'))
MAX_POSITION_RISK_USD = float(os.getenv('NEO_MAX_POSITION_RISK_USD', '250'))
MAX_TOTAL_EXPOSURE_PCT = float(os.getenv('NEO_MAX_TOTAL_EXPOSURE_PCT', '100'))
MAX_DRAWDOWN_PCT = float(os.getenv('NEO_MAX_DRAWDOWN_PCT', '0'))
MIN_LIQUIDITY_USD = 10000.0

# One validated effective threshold object governs every EARLY signal call.
STRICT_ENTRY_SCORE = float(os.getenv('NEO_STRICT_ENTRY_SCORE', '40'))
STRICT_MIN_CONVICTION = float(os.getenv('NEO_STRICT_MIN_CONVICTION', '22'))
STRICT_MIN_LIQUIDITY_USD = float(os.getenv('NEO_STRICT_MIN_LIQUIDITY_USD', '3000'))
STRICT_MAX_ENTRY_IMPACT_PCT = float(os.getenv('NEO_STRICT_MAX_ENTRY_IMPACT_PCT', '1.75'))
STRICT_MAX_ROUNDTRIP_COST_PCT = float(os.getenv('NEO_STRICT_MAX_ROUNDTRIP_COST_PCT', '2.75'))
STRICT_MAX_WORST_CASE_COST_PCT = float(os.getenv('NEO_STRICT_MAX_WORST_CASE_COST_PCT', '4.0'))
EFFECTIVE_ENTRY_THRESHOLDS = order_flow.EntryThresholds(STRICT_ENTRY_SCORE, STRICT_MIN_LIQUIDITY_USD, STRICT_MIN_CONVICTION)
for _threshold in (STRICT_MAX_ENTRY_IMPACT_PCT, STRICT_MAX_ROUNDTRIP_COST_PCT, STRICT_MAX_WORST_CASE_COST_PCT,
                   TRADE_NOTIONAL_USD, MAX_DAILY_LOSS_USD, POSITION_SCAN_SECONDS, MAX_POSITION_RISK_USD,
                   MAX_TOTAL_EXPOSURE_PCT, MAX_DRAWDOWN_PCT):
    if not math.isfinite(_threshold) or _threshold < 0: raise ValueError('invalid PAPER configuration')
if TRADE_NOTIONAL_USD <= 0 or POSITION_SCAN_SECONDS <= 0: raise ValueError('invalid PAPER configuration')
if MAX_POSITION_RISK_USD <= 0 or not 0 < MAX_TOTAL_EXPOSURE_PCT <= 100 or not 0 <= MAX_DRAWDOWN_PCT <= 100:
    raise ValueError('invalid PAPER risk limits')

# --- Strategy profile -------------------------------------------------------
# Exactly one versioned profile decides entries and exits for this process.
# ORDER_FLOW_ADAPTIVE_LEARNING (the primary strategy) and ORDER_FLOW_ADAPTIVE_OCT4
# own their whole configuration in adaptive_learning.CONFIG and
# order_flow_adaptive_oct4.CONFIG and ignore same-named environment variables;
# EARLY_SCOUT_V10 keeps the 2026-10-05 environment-driven values captured
# above. An unknown profile refuses to start.
V10_PROFILE = 'EARLY_SCOUT_V10'
OCT4_FIXED_PROFILE = 'ORDER_FLOW_OCT4_USER_FIXED'
OCT4_FIXED_PROFILE_LEGACY = 'ORDER_FLOW_OCT4_FIXED_5_10'
USER_ENGINE_SETTINGS_VERSION = exit_policy.USER_FIXED_VERSION
USER_ENGINE_SETTINGS_DEFAULTS = {
    'trade_notional_usd': 200.0,
    'stop_loss_pct': 5.0,
    'take_profit_pct': 10.0,
}
USER_ENGINE_SETTINGS_LIMITS = {
    'trade_notional_usd': (10.0, 5000.0),
    'stop_loss_pct': (0.5, 50.0),
    'take_profit_pct': (0.5, 200.0),
}
DEFAULT_STRATEGY_PROFILE = learner.PROFILE
_V10_SETTINGS = dict(
    SCAN_SECONDS=SCAN_SECONDS, POSITION_SCAN_SECONDS=POSITION_SCAN_SECONDS, MAX_POSITIONS=MAX_POSITIONS,
    WIN_REENTRY_SECONDS=WIN_REENTRY_SECONDS, LOSS_REENTRY_SECONDS=LOSS_REENTRY_SECONDS,
    STOP_LOSS_PCT=STOP_LOSS_PCT, TAKE_PROFIT_PCT=TAKE_PROFIT_PCT, TRAILING_PCT=TRAILING_PCT,
    MAX_HOLD_MINUTES=MAX_HOLD_MINUTES, TRADE_NOTIONAL_USD=TRADE_NOTIONAL_USD,
    MAX_DAILY_LOSS_USD=MAX_DAILY_LOSS_USD, MAX_POSITION_RISK_USD=MAX_POSITION_RISK_USD,
    MAX_TOTAL_EXPOSURE_PCT=MAX_TOTAL_EXPOSURE_PCT, MAX_DRAWDOWN_PCT=MAX_DRAWDOWN_PCT,
    STRICT_ENTRY_SCORE=STRICT_ENTRY_SCORE, STRICT_MIN_CONVICTION=STRICT_MIN_CONVICTION,
    STRICT_MIN_LIQUIDITY_USD=STRICT_MIN_LIQUIDITY_USD,
    STRICT_MAX_ENTRY_IMPACT_PCT=STRICT_MAX_ENTRY_IMPACT_PCT,
    STRICT_MAX_ROUNDTRIP_COST_PCT=STRICT_MAX_ROUNDTRIP_COST_PCT,
    STRICT_MAX_WORST_CASE_COST_PCT=STRICT_MAX_WORST_CASE_COST_PCT,
    EFFECTIVE_ENTRY_THRESHOLDS=EFFECTIVE_ENTRY_THRESHOLDS,
    SIGNAL_STRATEGY='ORDER_FLOW_EARLY_SCOUT_PAPER_V10', STRATEGY_VERSION=order_flow.SOURCE_COMMIT,
    ENTRY_POLICY_VERSION=entry_policy.POLICY_VERSION, SIGNAL_SOURCE=order_flow.SOURCE_COMMIT,
    EXIT_POLICY='fixed', EXIT_POLICY_VERSION=exit_policy.VERSION,
    LEARNING_MODE='HIGH_FREQ_SCOUT_SIZE_LEARNING+SEPARATE_VALIDATED_TRAINING',
    MAX_QUOTED_CANDIDATES=entry_policy.MAX_QUOTED_CANDIDATES,
    ENTRY_ON_POSITION_GUARD=True, DAILY_BUDGET_SIZING=True, UNSELLABLE_BLOCKS_ENTRIES=True,
    CONFIG_SOURCE='environment+market_monitor defaults', IGNORED_ENV_OVERRIDES=(),
)


def _oct4_settings() -> dict[str, Any]:
    c, limits = oct4.CONFIG, oct4.ENTRY_LIMITS
    return dict(
        SCAN_SECONDS=c['scan_seconds'], POSITION_SCAN_SECONDS=c['position_scan_seconds'],
        MAX_POSITIONS=c['max_positions'],
        WIN_REENTRY_SECONDS=c['same_token_cooldown_seconds'],
        LOSS_REENTRY_SECONDS=c['same_token_cooldown_seconds'],
        STOP_LOSS_PCT=c['stop_loss_pct'], TAKE_PROFIT_PCT=c['take_profit_pct'],
        TRAILING_PCT=c['trailing_pct'], MAX_HOLD_MINUTES=c['max_hold_minutes'],
        TRADE_NOTIONAL_USD=c['trade_notional_usd'], MAX_DAILY_LOSS_USD=c['max_daily_loss_usd'],
        MAX_POSITION_RISK_USD=c['max_position_full_loss_usd'],
        MAX_TOTAL_EXPOSURE_PCT=c['max_total_exposure_pct'], MAX_DRAWDOWN_PCT=c['max_drawdown_pct'],
        STRICT_ENTRY_SCORE=limits['min_score'], STRICT_MIN_CONVICTION=limits['min_conviction'],
        STRICT_MIN_LIQUIDITY_USD=limits['min_liquidity_usd'],
        STRICT_MAX_ENTRY_IMPACT_PCT=c['max_entry_impact_pct'],
        STRICT_MAX_ROUNDTRIP_COST_PCT=c['max_roundtrip_cost_pct'],
        STRICT_MAX_WORST_CASE_COST_PCT=c['max_worst_case_cost_pct'],
        EFFECTIVE_ENTRY_THRESHOLDS=order_flow.EntryThresholds(
            limits['min_score'], limits['min_liquidity_usd'], limits['min_conviction']),
        SIGNAL_STRATEGY=oct4.STRATEGY_ID, STRATEGY_VERSION=oct4.STRATEGY_VERSION,
        ENTRY_POLICY_VERSION=oct4.ENTRY_POLICY_VERSION, SIGNAL_SOURCE=oct4.SOURCE_COMMIT,
        EXIT_POLICY=oct4.EXIT_POLICY, EXIT_POLICY_VERSION=oct4.EXIT_POLICY_VERSION,
        LEARNING_MODE=oct4.LEARNING_MODE,
        MAX_QUOTED_CANDIDATES=c['max_quoted_candidates_per_scan'],
        ENTRY_ON_POSITION_GUARD=c['entry_on_position_guard'],
        DAILY_BUDGET_SIZING=c['daily_budget_sizing'], UNSELLABLE_BLOCKS_ENTRIES=True,
        CONFIG_SOURCE='order_flow_adaptive_oct4.CONFIG',
        IGNORED_ENV_OVERRIDES=tuple(sorted(name for name in oct4.OWNED_ENV if os.getenv(name) is not None)),
    )


def _oct4_fixed_settings() -> dict[str, Any]:
    """Oct-4 GOLD entries with user-controlled PAPER size and fixed SL/TP.

    Entry selection and every rug/price/quote check stay identical to Oct-4.
    Position exits are target-only: the position remains open until its own
    executable-net stop or take-profit is reached (or no sell route exists).
    """
    settings = _oct4_settings()
    settings.update(
        STOP_LOSS_PCT=USER_ENGINE_SETTINGS_DEFAULTS['stop_loss_pct'],
        TAKE_PROFIT_PCT=USER_ENGINE_SETTINGS_DEFAULTS['take_profit_pct'],
        MAX_HOLD_MINUTES=0,
        TRADE_NOTIONAL_USD=USER_ENGINE_SETTINGS_DEFAULTS['trade_notional_usd'],
        MAX_POSITION_RISK_USD=USER_ENGINE_SETTINGS_LIMITS['trade_notional_usd'][1] + 50.0,
        STRATEGY_VERSION='gold-2026-10-04-user-fixed-targets-20261009',
        EXIT_POLICY='fixed_targets', EXIT_POLICY_VERSION=exit_policy.USER_FIXED_VERSION,
        LEARNING_MODE='OCT4_GOLD_USER_FIXED_TARGETS',
        CONFIG_SOURCE='order_flow_adaptive_oct4.CONFIG + persisted user PAPER controls',
    )
    return settings


def _learner_settings() -> dict[str, Any]:
    c, loosest = learner.CONFIG, learner.TIER_LIMITS['EXPLORE']
    return dict(
        _oct4_settings(),
        SCAN_SECONDS=c['scan_seconds'], POSITION_SCAN_SECONDS=c['position_scan_seconds'],
        MAX_POSITIONS=c['max_positions'],
        WIN_REENTRY_SECONDS=c['win_reentry_seconds'], LOSS_REENTRY_SECONDS=c['loss_reentry_seconds'],
        STOP_LOSS_PCT=c['stop_loss_pct'], TAKE_PROFIT_PCT=c['take_profit_pct'],
        TRAILING_PCT=c['trailing_pct'], MAX_HOLD_MINUTES=c['max_hold_minutes'],
        TRADE_NOTIONAL_USD=c['trade_notional_usd'], MAX_DAILY_LOSS_USD=c['max_daily_loss_usd'],
        MAX_POSITION_RISK_USD=c['max_position_full_loss_usd'],
        MAX_TOTAL_EXPOSURE_PCT=c['max_total_exposure_pct'], MAX_DRAWDOWN_PCT=c['max_drawdown_pct'],
        # The loosest tier decides what is worth pre-warming and evaluating.
        STRICT_ENTRY_SCORE=loosest['min_score'], STRICT_MIN_CONVICTION=loosest['min_conviction'],
        STRICT_MIN_LIQUIDITY_USD=loosest['min_liquidity_usd'],
        STRICT_MAX_ENTRY_IMPACT_PCT=c['max_entry_impact_pct'],
        STRICT_MAX_ROUNDTRIP_COST_PCT=c['max_roundtrip_cost_pct'],
        STRICT_MAX_WORST_CASE_COST_PCT=c['max_worst_case_cost_pct'],
        EFFECTIVE_ENTRY_THRESHOLDS=order_flow.EntryThresholds(
            loosest['min_score'], loosest['min_liquidity_usd'], loosest['min_conviction']),
        SIGNAL_STRATEGY=learner.STRATEGY_ID, STRATEGY_VERSION=learner.STRATEGY_VERSION,
        ENTRY_POLICY_VERSION=learner.ENTRY_POLICY_VERSION, SIGNAL_SOURCE=learner.STRATEGY_VERSION,
        EXIT_POLICY=learner.EXIT_POLICY, EXIT_POLICY_VERSION=learner.EXIT_POLICY_VERSION,
        LEARNING_MODE=learner.LEARNING_MODE,
        MAX_QUOTED_CANDIDATES=c['max_quoted_candidates_per_scan'],
        ENTRY_ON_POSITION_GUARD=c['entry_on_position_guard'],
        DAILY_BUDGET_SIZING=c['daily_budget_sizing'],
        UNSELLABLE_BLOCKS_ENTRIES=c['unsellable_blocks_entries'],
        CONFIG_SOURCE='adaptive_learning.CONFIG',
        IGNORED_ENV_OVERRIDES=tuple(sorted(name for name in learner.OWNED_ENV if os.getenv(name) is not None)),
    )


def apply_strategy_profile(name: str | None = None) -> str:
    """Select the strategy profile. Production calls this once, at import."""
    global STRATEGY_PROFILE
    profile = str(name or os.getenv('NEO_STRATEGY_PROFILE') or DEFAULT_STRATEGY_PROFILE).strip().upper()
    if profile == learner.PROFILE:
        settings = _learner_settings()
    elif profile == oct4.PROFILE:
        settings = _oct4_settings()
    elif profile in (OCT4_FIXED_PROFILE, OCT4_FIXED_PROFILE_LEGACY):
        settings = _oct4_fixed_settings()
        profile = OCT4_FIXED_PROFILE
    elif profile == V10_PROFILE:
        settings = _V10_SETTINGS
    else:
        raise ValueError(f'unknown NEO_STRATEGY_PROFILE {profile!r}; refusing to start')
    globals().update(settings)
    STRATEGY_PROFILE = profile
    return profile


def is_learner() -> bool:
    return STRATEGY_PROFILE == learner.PROFILE


def is_adaptive() -> bool:
    """Profiles that use the Oct-4 conviction/GOLD entry family."""
    return STRATEGY_PROFILE in (oct4.PROFILE, OCT4_FIXED_PROFILE, learner.PROFILE)


def policy_module():
    return learner if is_learner() else oct4


def slots_in_use(positions) -> int:
    """Open positions that count against MAX_POSITIONS."""
    if UNSELLABLE_BLOCKS_ENTRIES:
        return len(positions)
    return sum(1 for p in positions if p.get('exit_state') != 'UNSELLABLE')


_LEARNING_CACHE: dict[str, Any] = {'key': None, 'table': None}


def learning_table(history, now: int) -> dict[str, Any]:
    """Per-bucket results of recent closed trades; rebuilt when history or the minute changes."""
    key = (len(history), (history[0].get('id') if history else None), now // 60_000)
    if _LEARNING_CACHE['key'] != key:
        _LEARNING_CACHE.update(key=key, table=learner.build_table(history, now))
    return _LEARNING_CACHE['table']


apply_strategy_profile()

# Legacy deterministic PAPER friction model; quote-backed positions use the
# versioned engine_execution adapter. PumpSwap canonical fee tiers mirror pump.fun fees
# published 2026-05-20. Non-PumpSwap pools use the conservative fallback below.
GENERIC_DEX_FEE_BPS = float(os.getenv('NEO_EXEC_GENERIC_DEX_FEE_BPS', '30'))
BASE_SLIPPAGE_BPS = float(os.getenv('NEO_EXEC_BASE_SLIPPAGE_BPS', '10'))
LATENCY_BUFFER_BPS = float(os.getenv('NEO_EXEC_LATENCY_BUFFER_BPS', '10'))
NETWORK_FEE_SOL = float(os.getenv('NEO_EXEC_NETWORK_FEE_SOL', '0.0001'))
MAX_PRICE_IMPACT_PCT = float(os.getenv('NEO_EXEC_MAX_PRICE_IMPACT_PCT', '20'))

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NEO-Meme-Market-Monitor/2.0', 'Accept': 'application/json'})
SOL_MINT = 'So11111111111111111111111111111111111111112'
_SOL_USD_CACHE = {'price': 0.0, 'ts': 0}
_SOL_USD_LOCK = threading.Lock()
GECKO_NEW_POOLS_URL = 'https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1'
GECKO_NEW_POOLS_TTL_MS = 5_000
_GECKO_NEW_POOLS_CACHE = {'ts': 0, 'pairs': []}
_GECKO_NEW_POOLS_LOCK = threading.Lock()

def now_ms() -> int:
    return int(time.time() * 1000)


def num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def normalize_engine_settings(raw: Any, current: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(USER_ENGINE_SETTINGS_DEFAULTS)
    if isinstance(current, dict):
        for key in USER_ENGINE_SETTINGS_DEFAULTS:
            if key in current:
                base[key] = current[key]
    if raw is not None and not isinstance(raw, dict):
        raise ValueError('settings payload must be an object')
    for key, (minimum, maximum) in USER_ENGINE_SETTINGS_LIMITS.items():
        if isinstance(raw, dict) and key in raw:
            try:
                value = float(raw[key])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f'invalid {key}') from exc
            if not math.isfinite(value) or value < minimum or value > maximum:
                raise ValueError(f'{key} must be between {minimum:g} and {maximum:g}')
            base[key] = round(value, 4)
    updated_at = ((raw or {}).get('updated_at') if isinstance(raw, dict) and 'updated_at' in raw
                  else (current or {}).get('updated_at'))
    return {
        'version': USER_ENGINE_SETTINGS_VERSION,
        'updated_at': int(updated_at or 0),
        **{key: float(base[key]) for key in USER_ENGINE_SETTINGS_DEFAULTS},
    }


def clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


SOLANA_ADDRESS_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def is_valid_solana_address(value: Any) -> bool:
    return isinstance(value, str) and bool(SOLANA_ADDRESS_RE.fullmatch(value.strip()))


def sol_usd_from_coin(coin: dict[str, Any]) -> float:
    price_usd = num(coin.get('priceUsd'))
    price_native = num(coin.get('priceNative'))
    if coin.get('quoteTokenAddress') not in (None, 'So11111111111111111111111111111111111111112'):
        return 0.0
    if price_usd > 0 and price_native > 0:
        return price_usd / price_native
    return 0.0


def pumpswap_fee_bps(coin: dict[str, Any]) -> float:
    if str(coin.get('dexId') or '').lower() != 'pumpswap':
        return GENERIC_DEX_FEE_BPS
    sol_usd = sol_usd_from_coin(coin)
    market_cap_usd = num(coin.get('marketCap') or coin.get('fdv'))
    if sol_usd <= 0 or market_cap_usd <= 0:
        return 125.0
    market_cap_sol = market_cap_usd / sol_usd
    tiers = (
        (420, 125.0), (1470, 120.0), (2460, 115.0), (3440, 110.0),
        (4420, 105.0), (9820, 100.0), (14740, 95.0), (19650, 90.0),
        (24560, 85.0), (29470, 80.0), (34380, 75.0), (39300, 70.0),
        (44210, 65.0), (49120, 60.0), (54030, 55.0), (58940, 52.5),
        (63860, 50.0), (68770, 47.5), (73681, 45.0), (78590, 42.5),
        (83500, 40.0), (88400, 37.5), (93330, 35.0), (98240, 32.5),
    )
    for max_mc_sol, fee_bps in tiers:
        if market_cap_sol < max_mc_sol:
            return fee_bps
    return 30.0


def execution_friction(coin: dict[str, Any], trade_value_usd: float) -> dict[str, float]:
    liquidity = max(num(coin.get('liquidityUsd')), 1.0)
    trade_value = max(0.0, trade_value_usd)
    # DexScreener liquidity is approximately both sides of the pool in USD.
    # For a constant-product AMM, quote-side reserve is roughly half of that,
    # so average fill impact is approximately trade_value / (liquidity / 2).
    impact_pct = min(MAX_PRICE_IMPACT_PCT, (2.0 * trade_value / liquidity) * 100.0)
    slippage_pct = BASE_SLIPPAGE_BPS / 100.0
    latency_pct = LATENCY_BUFFER_BPS / 100.0
    fee_bps = pumpswap_fee_bps(coin)
    network_fee_usd = NETWORK_FEE_SOL * sol_usd_from_coin(coin)
    return {
        'impact_pct': impact_pct,
        'slippage_pct': slippage_pct,
        'latency_pct': latency_pct,
        'total_price_penalty_pct': impact_pct + slippage_pct + latency_pct,
        'dex_fee_bps': fee_bps,
        'network_fee_usd': network_fee_usd,
    }


def entry_execution(coin: dict[str, Any], notional_usd: float) -> dict[str, float]:
    market_price = num(coin.get('priceUsd'))
    friction = execution_friction(coin, notional_usd)
    penalty = friction['total_price_penalty_pct'] / 100.0
    fill_price = market_price * (1.0 + penalty)
    dex_fee_usd = notional_usd * friction['dex_fee_bps'] / 10000.0
    token_budget_usd = max(0.0, notional_usd - dex_fee_usd)
    quantity = token_budget_usd / fill_price if fill_price > 0 else 0.0
    return {
        **friction,
        'market_price': market_price,
        'fill_price': fill_price,
        'dex_fee_usd': dex_fee_usd,
        'quantity': quantity,
        'capital_committed_usd': notional_usd + friction['network_fee_usd'],
    }


def exit_execution(coin: dict[str, Any], quantity: float) -> dict[str, float]:
    market_price = num(coin.get('priceUsd'))
    market_value_usd = max(0.0, quantity * market_price)
    friction = execution_friction(coin, market_value_usd)
    penalty = friction['total_price_penalty_pct'] / 100.0
    fill_price = max(0.0, market_price * (1.0 - penalty))
    gross_proceeds_usd = max(0.0, quantity * fill_price)
    dex_fee_usd = gross_proceeds_usd * friction['dex_fee_bps'] / 10000.0
    net_proceeds_usd = max(0.0, gross_proceeds_usd - dex_fee_usd - friction['network_fee_usd'])
    return {
        **friction,
        'market_price': market_price,
        'fill_price': fill_price,
        'market_value_usd': market_value_usd,
        'gross_proceeds_usd': gross_proceeds_usd,
        'dex_fee_usd': dex_fee_usd,
        'net_proceeds_usd': net_proceeds_usd,
    }


def enforce_paper_stop_cap(
    quote: dict[str, Any], notional: float, entry_cost: float, quantity: float
) -> tuple[dict[str, Any], float, float, bool]:
    """Compatibility entry point: return the observed modeled proceeds unchanged.

    Historical V8 silently invented proceeds through gaps. The name remains
    solely for older callers; there is no accounting cap in the repaired model.
    """
    pnl = num(quote.get('net_proceeds_usd')) - notional - entry_cost
    return quote, pnl, pnl / max(notional, 1e-18) * 100.0, False


def read_strategy_lab() -> dict[str, Any]:
    """Read the prebuilt compact Lab snapshot; full histories stay on disk."""
    try:
        data = json.loads(STRATEGY_LAB_COMPACT_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        data = json.loads(STRATEGY_LAB_PATH.read_text(encoding='utf-8'))
        return compact_strategy_lab(data)
    except Exception:
        return {'status': 'offline', 'books': {}, 'stats': {}}

def read_lab_book(book_id: str) -> dict[str, Any]:
    """Full trade list of one Strategy Lab book, read from the lab's own state file."""
    try:
        data = json.loads(STRATEGY_LAB_PATH.read_text(encoding='utf-8'))
    except Exception:
        data = {}
    return book_trades(data, book_id)

def _gecko_json(url: str) -> dict[str, Any]:
    response = SESSION.get(url, headers=coin_flow.GECKO_HEADERS, timeout=(1.0, 4.0))
    response.raise_for_status()
    return response.json()


_DASHBOARD_PAIR_CACHE: dict[str, dict[str, Any]] = {}
_DASHBOARD_PAIR_CACHE_LOCK = threading.Lock()
_DASHBOARD_PAIR_TTL_MS = 60_000


def _dashboard_coin(address: str) -> dict[str, Any] | None:
    """Resolve the selected token to an exact pool without touching trading state.

    Normally the current scanner feed already carries ``pairAddress``.  Older
    browser tabs can keep a token selected after it rotates out of that bounded
    feed, though, which used to turn /coin-flow and /coin-wallets into no_pair.
    For that dashboard-only case resolve the most liquid current DexScreener
    pair by mint and cache it briefly.
    """
    with STATE.lock:
        current = next((dict(c) for c in STATE.feed if c.get('address') == address), None)
    if current and current.get('pairAddress'):
        return current

    now = now_ms()
    with _DASHBOARD_PAIR_CACHE_LOCK:
        cached = _DASHBOARD_PAIR_CACHE.get(address)
        if cached and 0 <= now - int(cached.get('fetched_at') or 0) <= _DASHBOARD_PAIR_TTL_MS:
            coin = cached.get('coin')
            return dict(coin) if isinstance(coin, dict) else None

    resolved: dict[str, Any] | None = None
    try:
        pair_row = best_pairs(fetch_pairs([address])).get(address)
        pair_address = str((pair_row or {}).get('pairAddress') or '')
        if pair_address:
            base = (pair_row or {}).get('baseToken') or {}
            resolved = {
                'address': address,
                'pairAddress': pair_address,
                'dexId': (pair_row or {}).get('dexId') or '',
                'symbol': base.get('symbol') or '?',
                'priceUsd': num((pair_row or {}).get('priceUsd')),
                'priceNative': num((pair_row or {}).get('priceNative')),
            }
    except Exception:
        resolved = None

    with _DASHBOARD_PAIR_CACHE_LOCK:
        _DASHBOARD_PAIR_CACHE[address] = {'fetched_at': now, 'coin': resolved}
        if len(_DASHBOARD_PAIR_CACHE) > 200:
            stale = sorted(_DASHBOARD_PAIR_CACHE, key=lambda key: int(_DASHBOARD_PAIR_CACHE[key].get('fetched_at') or 0))[:50]
            for key in stale:
                _DASHBOARD_PAIR_CACHE.pop(key, None)
    return dict(resolved) if resolved else current


def read_coin_flow(address: str, pair: str) -> dict[str, Any]:
    """Dashboard-only buyers/sellers windows for one pool (verified tape + GeckoTerminal)."""
    address, pair = str(address or '')[:64], str(pair or '')[:64]
    if not address:
        return {'error': 'address_required'}
    coin = _dashboard_coin(address)
    if not pair:
        pair = str((coin or {}).get('pairAddress') or '')
    return coin_flow.build(address, pair, coin=coin, tape=read_live_tape(), fetch=_gecko_json)


def _dashboard_rpc(method: str, params: list[Any]) -> Any:
    """Read-only Solana RPC for dashboard lookups; slower budget than the stop guard's."""
    last_error: Exception | None = None
    for url in dict.fromkeys([pumpswap_stop.RPC_URL, pumpswap_stop.RPC_FALLBACK_URL]):
        if not url:
            continue
        try:
            response = SESSION.post(url, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, timeout=(1.0, 6.0))
            response.raise_for_status()
            payload = response.json()
            if payload.get('error'):
                raise RuntimeError(str(payload['error'])[:180])
            return payload.get('result')
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError(str(last_error or 'RPC unavailable'))


def _dashboard_rpc_batch(calls: list[tuple[str, list[Any]]]) -> list[dict[str, Any]]:
    """Small read-only JSON-RPC batch with fast fallback for the visible wallet feed."""
    if not calls:
        return []
    payload = [{'jsonrpc': '2.0', 'id': index, 'method': method, 'params': params}
               for index, (method, params) in enumerate(calls, 1)]
    last_error: Exception | None = None
    # SolanaTracker public RPC handles JSON-RPC batches well; the canonical
    # public endpoint remains a fallback. Keep timeouts below the 3s UI cadence.
    for url in dict.fromkeys([pumpswap_stop.RPC_FALLBACK_URL, pumpswap_stop.RPC_URL]):
        if not url:
            continue
        try:
            response = SESSION.post(url, json=payload, timeout=(0.8, 3.5))
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise RuntimeError('RPC batch response is not a list')
            by_id = {row.get('id'): row for row in data if isinstance(row, dict) and type(row.get('id')) is int}
            return [by_id.get(index, {'id': index, 'error': {'code': 'MISSING_RPC_ID'}})
                    for index in range(1, len(calls) + 1)]
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError(str(last_error or 'RPC batch unavailable'))


def read_coin_wallets(address: str, pair: str) -> dict[str, Any]:
    """Dashboard-only wallet activity and top holders for one token."""
    address, pair = str(address or '')[:64], str(pair or '')[:64]
    if not address:
        return {'error': 'address_required'}
    coin = _dashboard_coin(address)
    if not pair:
        pair = str((coin or {}).get('pairAddress') or '')
    direct = coin_wallets.direct_pool_trades(pair, address, coin=coin, batch_rpc=_dashboard_rpc_batch) if pair else None
    return coin_wallets.build(address, pair, tape=read_live_tape(), fetch=_gecko_json, rpc=_dashboard_rpc, direct=direct)


HYPE_RADAR_PATH = Path(os.getenv('NEO_HYPE_STATE_PATH', '/var/lib/neo-market/hype_radar.json'))


def read_hype_radar() -> dict[str, Any]:
    """Hype Radar themes plus which coins of the current feed match them (dashboard only)."""
    try:
        data = json.loads(HYPE_RADAR_PATH.read_text(encoding='utf-8'))
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    now = now_ms()
    themes = hype_radar.active_themes(data, now=now)
    with STATE.lock:
        feed = [dict(c) for c in STATE.feed]
    matches = []
    for coin in feed:
        hit = hype_radar.match_token(coin, themes, now=now)
        if hit:
            matches.append({'address': coin.get('address'), 'pairAddress': coin.get('pairAddress'), 'symbol': coin.get('symbol'),
                            'name': coin.get('name'), 'score': coin.get('score'), 'liquidityUsd': coin.get('liquidityUsd'),
                            'marketCap': coin.get('marketCap') or coin.get('fdv'), 'ageMinutes': coin.get('ageMinutes'),
                            'priceChangeM5': (coin.get('priceChange') or {}).get('m5'), 'imageUrl': coin.get('imageUrl'), **hit})
    matches.sort(key=lambda m: -m['score'])
    return {'status': data.get('status', 'offline'), 'updated_at': data.get('updated_at'), 'last_success_at': data.get('last_success_at'),
            'model': data.get('model'), 'poll_seconds': data.get('poll_seconds'), 'daily_budget_usd': data.get('daily_budget_usd'),
            'budget': data.get('budget'), 'error': data.get('error'), 'source_status': data.get('source_status'),
            'theme_ttl_seconds': data.get('theme_ttl_seconds'), 'min_hype': data.get('min_hype'),
            'themes': themes, 'stale_themes': len(data.get('themes') or []) - len(themes),
            'source_lines': (data.get('source_lines') or [])[:MAX_HYPE_SOURCE_LINES], 'matches': matches[:40],
            'feed_count': len(feed), 'checked_at': now}


MAX_HYPE_SOURCE_LINES = 90


def read_live_tape() -> dict[str, Any]:
    try:
        data = json.loads(LIVE_TAPE_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {'status': 'offline', 'events': []}
    except Exception:
        return {'status': 'offline', 'events': []}


def active_tape_pins(tape: dict[str, Any]) -> dict[str, str]:
    """Map currently recorded mints to their exact pool so scan rotation cannot outrun flow evidence."""
    pins: dict[str, str] = {}
    coverage = tape.get('pair_coverage') or {}
    if not isinstance(coverage, dict):
        return pins
    for row in coverage.values():
        if not isinstance(row, dict):
            continue
        address = str(row.get('address') or '').strip()
        pair = str(row.get('pairAddress') or '').strip()
        if is_valid_solana_address(address) and is_valid_solana_address(pair):
            pins[address] = pair
    return pins


def prefer_exact_tape_pairs(chosen: dict[str, dict[str, Any]], pairs: list[dict[str, Any]], pins: dict[str, str]) -> None:
    """Use the exact pool whose on-chain tape is being verified, never a different pool for the same mint."""
    if not pins:
        return
    for pair in pairs:
        address = str((pair.get('baseToken') or {}).get('address') or '')
        exact = pins.get(address)
        if exact and str(pair.get('pairAddress') or '') == exact:
            chosen[address] = pair

PUBLIC_HISTORY_MIN_NOTIONAL_USD = 200.0

_ALL_TIME_HISTORY_CACHE: dict[str, Any] = {'mtime_ns': None, 'rows': []}
_ALL_TIME_HISTORY_LOCK = threading.Lock()

def public_history_trade_visible(trade: dict[str, Any]) -> bool:
    """Hide legacy sub-$200 rows but show future owner-selected sizes."""
    if 'notional_usd' not in trade or trade.get('notional_usd') is None:
        return True
    return (num(trade.get('notional_usd')) + 1e-9 >= PUBLIC_HISTORY_MIN_NOTIONAL_USD
            or trade.get('engine_settings_version') == USER_ENGINE_SETTINGS_VERSION)

def read_all_time_history(current_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge restored PAPER history for display without touching engine state."""
    archive_rows: list[dict[str, Any]] = []
    try:
        stat = ALL_TIME_HISTORY_PATH.stat()
        with _ALL_TIME_HISTORY_LOCK:
            if _ALL_TIME_HISTORY_CACHE.get('mtime_ns') != stat.st_mtime_ns:
                data = json.loads(ALL_TIME_HISTORY_PATH.read_text(encoding='utf-8'))
                rows = data.get('history', []) if isinstance(data, dict) else []
                _ALL_TIME_HISTORY_CACHE['rows'] = [
                    row for row in rows
                    if isinstance(row, dict) and public_history_trade_visible(row)
                ]
                _ALL_TIME_HISTORY_CACHE['mtime_ns'] = stat.st_mtime_ns
            archive_rows = list(_ALL_TIME_HISTORY_CACHE.get('rows') or [])
    except Exception:
        archive_rows = []

    merged: dict[str, dict[str, Any]] = {}
    for trade in archive_rows:
        trade_id = str(trade.get('id') or '')
        if trade_id:
            merged[trade_id] = trade
    filtered_current = [
        trade for trade in current_history
        if isinstance(trade, dict) and public_history_trade_visible(trade)
    ]
    for trade in filtered_current:
        trade_id = str(trade.get('id') or '')
        if trade_id:
            merged[trade_id] = trade
    if not merged:
        return filtered_current
    return sorted(merged.values(), key=lambda trade: int(trade.get('closed_at') or trade.get('updated_at') or 0), reverse=True)

def compact_public_trade(trade: dict[str, Any]) -> dict[str, Any]:
    fields = (
        'id', 'address', 'pairAddress', 'name', 'symbol', 'imageUrl',
        'entry_price', 'current_price', 'peak_price', 'execution_entry_price',
        'execution_exit_price', 'notional_usd', 'score', 'current_score',
        'opened_at', 'updated_at', 'closed_at', 'exit_price', 'exit_reason',
        'trade_no', 'session_id', 'pnl_usd', 'pnl_pct', 'balance_before',
        'balance_after', 'dex_url', 'strategy_id', 'entry_policy_version',
        'exit_policy_version', 'signal_pnl_pct', 'entry_roundtrip_pnl_pct',
        'observed_exit_pnl_pct', 'observed_exit_pnl_usd', 'paper_stop_capped',
        'stop_execution_source', 'stop_loss_pct', 'take_profit_net_pct',
        'exit_policy', 'engine_settings_version',
    )
    return {field: trade.get(field) for field in fields if field in trade}


def api(path: str) -> Any:
    # DexScreener occasionally closes or stalls a single request. Retry once
    # before declaring discovery degraded so a transient 5s blip does not drop
    # a source from the rotating universe.
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = SESSION.get(f'{DEX_API}{path}', timeout=(1.5, 5.0))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError, TypeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.15)
    if last_error is not None:
        raise last_error
    raise RuntimeError('DexScreener request failed')


def sol_usd_market_price() -> float:
    current = now_ms()
    with _SOL_USD_LOCK:
        cached_price = num(_SOL_USD_CACHE.get('price'))
        cached_at = int(_SOL_USD_CACHE.get('ts') or 0)
    if cached_price > 0 and 0 <= current - cached_at <= 60_000:
        return cached_price
    try:
        response = SESSION.get(f'{DEX_API}/latest/dex/tokens/{SOL_MINT}', timeout=(1.0, 3.0))
        response.raise_for_status()
        pairs = response.json().get('pairs') or []
        candidates = []
        for pair in pairs:
            base = pair.get('baseToken') or {}
            if pair.get('chainId') != 'solana' or base.get('address') != SOL_MINT:
                continue
            price = num(pair.get('priceUsd'))
            liquidity = num((pair.get('liquidity') or {}).get('usd'))
            if price > 0:
                candidates.append((liquidity, price))
        if candidates:
            price = max(candidates)[1]
            with _SOL_USD_LOCK:
                _SOL_USD_CACHE.update(price=price, ts=current)
            return price
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        pass
    return cached_price if cached_price > 0 else 0.0


def signal(kind: str, title: str, detail: str) -> dict[str, str]:
    return {'kind': kind, 'title': title, 'detail': detail}


class State:
    def __init__(self, load_state: bool = True) -> None:
        self.lock = threading.RLock()
        self.running = True
        self.status = 'starting'
        self.message = 'Starting NEO live market monitor.'
        self.last_scan_at = 0
        self.scan_count = 0
        self.scanned_address_slots_total = 0
        self.feed: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.position_market: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.price_history: dict[str, list[dict[str, Any]]] = {}
        self.source_status = {'dexscreener': 'starting'}
        self.demo_starting_balance_usd = STARTING_BALANCE_USD
        self.demo_balance_usd = STARTING_BALANCE_USD
        self.demo_started_at = now_ms()
        self.demo_session_id = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
        self.trade_seq = 0
        self.pending_audit: list[dict[str, Any]] = []
        self.audit_status = 'ok'
        self.equity_peak_usd = STARTING_BALANCE_USD
        self.entry_diagnostics = {'status': 'starting', 'policy_version': ENTRY_POLICY_VERSION}
        self.discovery_stats: dict[str, Any] = {}
        self.engine_settings = normalize_engine_settings(None)
        self.risk_day_key = time.strftime('%Y-%m-%d', time.gmtime())
        self.risk_day_start_balance_usd = STARTING_BALANCE_USD
        if load_state: self.load()

    def load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('positions', []), list) or not isinstance(data.get('history', []), list):
                raise ValueError('invalid account schema')
            if int(data.get('schema_version') or 1) > 3:
                raise ValueError('unsupported future account schema')
            ids = [p.get('id') for p in data.get('positions', [])]
            if any(not i for i in ids) or len(ids) != len(set(ids)):
                raise ValueError('missing or duplicate open position identifiers')
            if not math.isfinite(float(data.get('demo_balance_usd', STARTING_BALANCE_USD))):
                raise ValueError('nonfinite balance')
            self.positions = data.get('positions', [])
            self.running = bool(data.get('running', True))
            self.status = str(data.get('status') or ('starting' if self.running else 'paused'))
            self.history = data.get('history', [])
            self.position_market = data.get('position_market', {})
            self.pending_audit = data.get('pending_audit', [])
            self.audit_status = 'pending' if self.pending_audit else 'ok'
            self.events = data.get('events', [])[-100:]
            self.demo_starting_balance_usd = num(data.get('demo_starting_balance_usd'), STARTING_BALANCE_USD)
            self.demo_balance_usd = num(data.get('demo_balance_usd'), self.demo_starting_balance_usd)
            self.equity_peak_usd = num(data.get('equity_peak_usd'), max(self.demo_starting_balance_usd,self.demo_balance_usd))
            self.demo_started_at = int(data.get('demo_started_at') or self.demo_started_at)
            self.demo_session_id = str(data.get('demo_session_id') or self.demo_session_id)
            self.trade_seq = int(data.get('trade_seq') or 0)
            self.scan_count = max(0, int(data.get('scan_count') or 0))
            self.scanned_address_slots_total = max(0, int(data.get('scanned_address_slots_total') or 0))
            self.engine_settings = normalize_engine_settings(data.get('engine_settings'), self.engine_settings)
            today = time.strftime('%Y-%m-%d', time.gmtime())
            if str(data.get('risk_day_key') or '') == today:
                self.risk_day_key = today
                self.risk_day_start_balance_usd = num(
                    data.get('risk_day_start_balance_usd'), self.demo_balance_usd
                )
            else:
                self.risk_day_key = today
                self.risk_day_start_balance_usd = self.demo_balance_usd
            raw = data.get('price_history', {})
            if isinstance(raw, dict):
                self.price_history = {k: v[-480:] for k, v in raw.items() if isinstance(v, list)}
        except Exception as exc:
            # Never boot a silently reset $1000 account from an unreadable file.
            raise RuntimeError('Account state is unreadable; refusing automatic reset') from exc

    def _persist(self) -> None:
        self.equity_peak_usd = max(self.equity_peak_usd, self.equity_usd())
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        keep = {c.get('address') for c in self.feed[:50]}
        keep |= {p.get('address') for p in self.positions}
        keep |= {t.get('address') for t in self.history[:100]}
        price_history = {k: v[-480:] for k, v in self.price_history.items() if k in keep}
        runtime.atomic_json(STATE_PATH,{
            'schema_version': 3,
            'running': self.running, 'status': self.status,
            'positions': self.positions,
            'position_market': self.position_market,
            'history': self.history,
            'pending_audit': self.pending_audit,
            'events': self.events[-100:],
            'demo_starting_balance_usd': self.demo_starting_balance_usd,
            'demo_balance_usd': self.demo_balance_usd,
            'equity_peak_usd': self.equity_peak_usd,
            'demo_started_at': self.demo_started_at,
            'demo_session_id': self.demo_session_id,
            'trade_seq': self.trade_seq,
            'scan_count': self.scan_count,
            'scanned_address_slots_total': self.scanned_address_slots_total,
            'engine_settings': self.engine_settings,
            'risk_day_key': self.risk_day_key,
            'risk_day_start_balance_usd': self.risk_day_start_balance_usd,
            'price_history': price_history,
        })

    def save(self) -> None:
        """Commit the authoritative ledger before draining the durable audit outbox."""
        with self.lock:
            self._persist()
            if not self.pending_audit: return
            try:
                for row in list(self.pending_audit): append_audit(row['event'], row['payload'])
                old_pending = self.pending_audit
                self.pending_audit = []
                try: self._persist()
                except Exception:
                    self.pending_audit = old_pending
                    raise
                self.audit_status = 'ok'
            except Exception as exc:
                self.audit_status = 'pending:' + type(exc).__name__
                self.message = 'Audit write pending; ledger committed and retryable.'

    def commit(self, event, payload, **changes):
        """Atomic balance/position/history transition; failed state write rolls back memory."""
        with self.lock:
            previous = {key: getattr(self, key) for key in changes}
            previous_peak = self.equity_peak_usd
            previous_pending = self.pending_audit
            payload = dict(payload, event_id=f"{payload.get('session_id', self.demo_session_id)}:{event}:{payload.get('id', uuid.uuid4().hex)}")
            for key, value in changes.items(): setattr(self, key, value)
            self.pending_audit = previous_pending + [{'event': event, 'payload': payload}]
            try: self.save()
            except Exception:
                for key, value in previous.items(): setattr(self, key, value)
                self.pending_audit = previous_pending
                self.equity_peak_usd = previous_peak
                raise

    def reset_with_archive(self):
        """An explicit PAPER reset archives the full pre-reset state and audit first."""
        with self.lock:
            self.save()
            archive = STATE_PATH.parent / 'archives' / (str(now_ms()) + '-' + uuid.uuid4().hex[:8])
            archive.mkdir(parents=True, exist_ok=False)
            manifest = {'old_session_id': self.demo_session_id, 'created_at': now_ms(), 'files': []}
            for source in (STATE_PATH, AUDIT_PATH):
                if source.exists():
                    target = archive / source.name
                    shutil.copy2(source, target)
                    manifest['files'].append({'source': str(source), 'archive': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
            runtime.atomic_json(archive / 'manifest.json', manifest)
            new_session = time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
            self.commit('RESET', {'id': new_session, 'session_id': new_session, 'starting_balance_usd': STARTING_BALANCE_USD, 'archive': str(archive)},
                positions=[], history=[], events=[], price_history={}, position_market={},
                demo_starting_balance_usd=STARTING_BALANCE_USD, demo_balance_usd=STARTING_BALANCE_USD,
                demo_started_at=now_ms(), demo_session_id=new_session, trade_seq=0,
                equity_peak_usd=STARTING_BALANCE_USD,
                risk_day_key=time.strftime('%Y-%m-%d', time.gmtime()), risk_day_start_balance_usd=STARTING_BALANCE_USD,
                entry_diagnostics={'status': 'reset', 'policy_version': ENTRY_POLICY_VERSION})
            self.event(f'New PAPER session with ${STARTING_BALANCE_USD:.2f}; archive {archive.name}.')
            self.save()
            return str(archive)

    def event(self, text: str) -> None:
        self.events.insert(0, {'ts': now_ms(), 'text': text[:500]})
        self.events = self.events[:100]
        self.message = text[:500]

    def reserved_usd(self) -> float:
        return sum(num(p.get('capital_committed_usd'), num(p.get('notional_usd'))) for p in self.positions)

    def unrealized_pnl_usd(self) -> float:
        return sum(num(p.get('pnl_usd')) for p in self.positions)

    def available_balance_usd(self) -> float:
        return max(0.0, self.demo_balance_usd - self.reserved_usd())

    def equity_usd(self) -> float:
        return self.demo_balance_usd + self.unrealized_pnl_usd()

    def refresh_risk_day(self) -> None:
        today = time.strftime('%Y-%m-%d', time.gmtime())
        if self.risk_day_key != today:
            self.risk_day_key = today
            self.risk_day_start_balance_usd = self.demo_balance_usd

    def risk_day_pnl(self) -> float:
        self.refresh_risk_day()
        return self.equity_usd() - self.risk_day_start_balance_usd

    def realized_today(self) -> float:
        day = time.strftime('%Y-%m-%d', time.gmtime())
        total = 0.0
        for trade in self.history:
            stamp = int(trade.get('closed_at', 0)) / 1000
            if stamp and time.strftime('%Y-%m-%d', time.gmtime(stamp)) == day:
                total += num(trade.get('pnl_usd'))
        return total

    def live_flow(self, address: str, seconds: int = 30, pair_address: str | None = None) -> dict[str, Any]:
        tape = read_live_tape()
        decision_at = now_ms()
        cutoff = decision_at - seconds * 1000
        coverage = (tape.get('pair_coverage') or {}).get(pair_address, {})
        quality = str(coverage.get('status') or 'UNKNOWN').upper()
        if not pair_address or num(coverage.get('complete_since_ms'), decision_at+1) > cutoff:
            quality = 'DEGRADED' if quality == 'COMPLETE' else 'UNKNOWN'
        available_rows = [e for e in tape.get('events', []) if e.get('address') == address
                and (not pair_address or e.get('pairAddress') == pair_address)
                and cutoff <= num(e.get('ts')) <= decision_at
                and 0 < num(e.get('available_at', e.get('ingested_at'))) <= decision_at]
        if any(e.get('quality_flags') or num(e.get('usd_amount')) <= 0 for e in available_rows):
            quality = 'DEGRADED'
        rows = []
        event_ids = set()
        for event in available_rows:
            if event.get('quality_flags') or num(event.get('usd_amount')) <= 0: continue
            event_id = event.get('event_id')
            if event_id and event_id in event_ids: continue
            if event_id: event_ids.add(event_id)
            rows.append(event)
        buys = [e for e in rows if e.get('direction') == 'BUY']
        sells = [e for e in rows if e.get('direction') == 'SELL']
        buy_usd = sum(num(e.get('usd_amount')) for e in buys)
        sell_usd = sum(num(e.get('usd_amount')) for e in sells)
        buy_counts: dict[str, int] = {}
        sell_counts: dict[str, int] = {}
        for e in buys:
            wallet = e.get('wallet')
            if wallet:
                buy_counts[wallet] = buy_counts.get(wallet, 0) + 1
        for e in sells:
            wallet = e.get('wallet')
            if wallet:
                sell_counts[wallet] = sell_counts.get(wallet, 0) + 1
        buyer_wallets = set(buy_counts)
        seller_wallets = set(sell_counts)
        repeat_buy_wallets = sum(1 for count in buy_counts.values() if count >= 2)
        whale_buy_usd = sum(num(e.get('usd_amount')) for e in buys if num(e.get('usd_amount')) >= 750)
        whale_sell_usd = sum(num(e.get('usd_amount')) for e in sells if num(e.get('usd_amount')) >= 750)
        return {
            'quality': quality, 'coverage': coverage, 'decision_at': decision_at,
            'fresh': quality == 'COMPLETE',
            'latest_at': max([num(e.get('available_at', e.get('ingested_at'))) for e in rows] or [0]),
            'seconds': seconds, 'trades': len(rows), 'buys': len(buys), 'sells': len(sells),
            'buy_usd': round(buy_usd, 2), 'sell_usd': round(sell_usd, 2),
            'net_buy_usd': round(buy_usd - sell_usd, 2),
            'buy_sell_usd_ratio': round(buy_usd / max(sell_usd, 1.0), 2),
            'unique_wallets': len(buyer_wallets | seller_wallets),
            'buyer_wallets': len(buyer_wallets), 'seller_wallets': len(seller_wallets),
            'wallet_buy_sell_ratio': round(len(buyer_wallets) / max(len(seller_wallets), 1), 2),
            'repeat_buy_wallets': repeat_buy_wallets,
            'whale_buy_usd': round(whale_buy_usd, 2), 'whale_sell_usd': round(whale_sell_usd, 2),
            'max_buy_usd': round(max([num(e.get('usd_amount')) for e in buys] or [0]), 2),
            'max_sell_usd': round(max([num(e.get('usd_amount')) for e in sells] or [0]), 2),
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            display_history = read_all_time_history(self.history)
            lifetime = trade_metrics(display_history)
            wins, closed = lifetime['wins'], lifetime['closed_trades']
            closed_total = closed
            tape = read_live_tape()
            tape_coverage = tape.get('pair_coverage') or {}
            complete_pairs = sum(1 for row in tape_coverage.values() if isinstance(row, dict) and row.get('status') == 'COMPLETE')
            tracked_pairs = int(tape.get('tracked_pairs') or len(tape_coverage) or 0)
            warming_pairs = int(tape.get('warming_pairs') if tape.get('warming_pairs') is not None else max(0, tracked_pairs - complete_pairs))
            with _BACKEND_HEALTH_LOCK:
                market_health = dict(_MARKET_DATA_HEALTH)
                discovery_health = dict(_DISCOVERY_HEALTH)
            tape_health = {
                'status': tape.get('status') or 'offline', 'checked_at': tape.get('updated_at') or 0,
                'tracked_pairs': tracked_pairs, 'complete_pairs': complete_pairs,
                'warming_pairs': warming_pairs, 'coverage': tape.get('coverage'),
                'source': tape.get('source'), 'error': tape.get('error'),
            }
            active_issues: list[dict[str, Any]] = []
            if market_health.get('status') in {'degraded', 'error'}:
                active_issues.append({'component': 'market_data', 'status': market_health.get('status'),
                    'text': f"DexScreener: {market_health.get('unavailable_tokens', 0)}/{market_health.get('requested_tokens', 0)} token-а недостъпни след retry",
                    'at': market_health.get('checked_at') or 0})
            if discovery_health.get('status') in {'degraded', 'error'}:
                active_issues.append({'component': 'discovery', 'status': discovery_health.get('status'),
                    'text': f"Discovery: {discovery_health.get('sources_failed', 0)} source-а с проблем",
                    'at': discovery_health.get('checked_at') or 0})
            if tape_health.get('status') in {'degraded', 'offline'}:
                active_issues.append({'component': 'order_flow', 'status': tape_health.get('status'),
                    'text': f"Order flow: {str(tape_health.get('status')).upper()}" + (f" · {tape_health.get('error')}" if tape_health.get('error') else ''),
                    'at': tape_health.get('checked_at') or 0})
            runtime_health = {
                'checked_at': now_ms(), 'market_data': market_health, 'discovery': discovery_health,
                'order_flow': tape_health, 'active_issues': active_issues,
            }
            user_controls_enabled = STRATEGY_PROFILE == OCT4_FIXED_PROFILE
            controls = normalize_engine_settings(None, self.engine_settings) if user_controls_enabled else {
                'trade_notional_usd': TRADE_NOTIONAL_USD, 'stop_loss_pct': STOP_LOSS_PCT,
                'take_profit_pct': TAKE_PROFIT_PCT, 'version': None, 'updated_at': 0,
            }
            effective_notional = num(controls.get('trade_notional_usd'), TRADE_NOTIONAL_USD)
            effective_stop = num(controls.get('stop_loss_pct'), STOP_LOSS_PCT)
            effective_tp = num(controls.get('take_profit_pct'), TAKE_PROFIT_PCT)
            return {
                'running': self.running,
                'status': self.status,
                'message': self.message,
                'last_scan_at': self.last_scan_at,
                'scan_count': self.scan_count,
                'feed': self.feed,
                'positions': self.positions,
                'history': [compact_public_trade(t) for t in display_history],
                'events': self.events[:30],
                'source_status': self.source_status,
                'runtime_health': runtime_health,
                'entry_diagnostics': self.entry_diagnostics,
                'discovery_stats': self.discovery_stats,
                'learning': learner.summary(learning_table(self.history, now_ms())) if is_learner() else None,
                'live_tape': [],
                'live_tape_status': {
                    'status': tape_health['status'], 'tracked_pairs': tracked_pairs,
                    'complete_pairs': complete_pairs, 'warming_pairs': warming_pairs,
                    'coverage': tape_health['coverage'], 'updated_at': tape_health['checked_at'],
                    'source': tape_health['source'], 'error': tape_health['error'],
                },
                'strategy_lab': read_strategy_lab(),
                'paper_training': training_bridge.snapshot(),
                'stats': {
                    'feed_count': len(self.feed),
                    'open_positions': len(self.positions),
                    'closed_trades': closed_total,
                    'wins': wins,
                    'win_rate': round((wins / closed) * 100, 1) if closed else 0,
                    'metrics': {'lifetime': lifetime,
                        'session': trade_metrics([t for t in self.history if t.get('session_id') == self.demo_session_id]),
                        'rolling_100': trade_metrics(display_history[:100]),
                        'policy_versions': {v: trade_metrics([t for t in display_history if str(t.get('exit_policy_version') or 'legacy_unknown') == v])
                            for v in {str(t.get('exit_policy_version') or 'legacy_unknown') for t in display_history}}},
                    'historical_records_missing': max(0, self.trade_seq - len(self.positions) - len(self.history)),
                    'audit_status': self.audit_status,
                    'unavailable_liquidation_positions': sum(1 for p in self.positions if p.get('valuation_status') == 'unavailable'),
                    'conservative_open_risk_usd': sum(num(p.get('conservative_risk_usd'), num(p.get('capital_committed_usd'))) for p in self.positions),
                    'drawdown_pct': max(0,1-self.equity_usd()/max(self.equity_peak_usd,1))*100,
                    'realized_today_usd': round(self.realized_today(), 2),
                    'risk_day_pnl_usd': round(self.risk_day_pnl(), 2),
                    'daily_risk_remaining_usd': (round(max(0.,MAX_DAILY_LOSS_USD+self.risk_day_pnl()),4) if MAX_DAILY_LOSS_USD > 0 else None),
                    'daily_risk_cap_enabled': MAX_DAILY_LOSS_USD > 0,
                    'risk_day_start_balance_usd': round(self.risk_day_start_balance_usd, 2),
                    'demo_starting_balance_usd': round(self.demo_starting_balance_usd, 2),
                    'demo_balance_usd': round(self.demo_balance_usd, 2),
                    'demo_equity_usd': round(self.equity_usd(), 2),
                    'demo_available_usd': round(self.available_balance_usd(), 2),
                    'demo_reserved_usd': round(self.reserved_usd(), 2),
                    'unrealized_pnl_usd': round(self.unrealized_pnl_usd(), 2),
                    'realized_total_usd': round(self.demo_balance_usd - self.demo_starting_balance_usd, 2),
                    'return_pct': round(((self.equity_usd() - self.demo_starting_balance_usd) / max(self.demo_starting_balance_usd, 1)) * 100, 3),
                    'demo_started_at': self.demo_started_at,
                    'demo_session_id': self.demo_session_id,
                },
                'config': {
                    'scan_seconds': SCAN_SECONDS,
                    'position_scan_seconds': POSITION_SCAN_SECONDS,
                    'entry_score': STRICT_ENTRY_SCORE,
                    'max_positions': MAX_POSITIONS,
                    'stop_loss_pct': effective_stop,
                    'stop_loss_basis': 'EXECUTABLE_NET_PNL',
                    'stop_trigger_net_pct': -effective_stop,
                    'take_profit_basis': 'EXECUTABLE_NET_PNL',
                    'reentry_seconds': WIN_REENTRY_SECONDS, 'loss_reentry_seconds': LOSS_REENTRY_SECONDS,
                    'signal_strategy': SIGNAL_STRATEGY,
                    'strategy_profile': STRATEGY_PROFILE, 'strategy_version': STRATEGY_VERSION,
                    'learning_mode': LEARNING_MODE,
                    'config_source': CONFIG_SOURCE,
                    'ignored_env_overrides': list(IGNORED_ENV_OVERRIDES),
                    'same_token_cooldown_seconds': LOSS_REENTRY_SECONDS if is_adaptive() and not is_learner() else None,
                    'entry_flow_window_seconds': policy_module().CONFIG['entry_flow_window_seconds'] if is_adaptive() else None,
                    'entry_on_position_guard': ENTRY_ON_POSITION_GUARD,
                    'unsellable_blocks_entries': UNSELLABLE_BLOCKS_ENTRIES,
                    'adaptive_strategy': policy_module().describe() if is_adaptive() else None,
                    'risk_overlay': 'PLANNED_NET_STOP_NO_FILL_GUARANTEE',
                    'execution_verification_version': 'QUOTE_EVIDENCE_V9',
                    'rug_guard': rug_guard.VERSION,
                    'paper_only': True, 'public_history_min_notional_usd': PUBLIC_HISTORY_MIN_NOTIONAL_USD,
                    'user_controls': {
                        'enabled': user_controls_enabled, 'version': controls.get('version'),
                        'updated_at': controls.get('updated_at'), 'limits': USER_ENGINE_SETTINGS_LIMITS,
                        'target_only_exits': user_controls_enabled,
                        'max_hold_enabled': False if user_controls_enabled else MAX_HOLD_MINUTES > 0,
                    },
                    'runtime_version': runtime.VERSION,
                    'daily_budget_sizing': DAILY_BUDGET_SIZING,
                    'stop_execution_buffer_pct': STOP_EXECUTION_BUFFER_PCT,
                    'exit_impact_emergency_pct': None if user_controls_enabled else EXIT_IMPACT_EMERGENCY_PCT,
                    'take_profit_pct': effective_tp,
                    'trailing_pct': TRAILING_PCT,
                    'max_hold_minutes': None if user_controls_enabled else MAX_HOLD_MINUTES,
                    'min_liquidity_usd': STRICT_MIN_LIQUIDITY_USD,
                    'trade_notional_usd': effective_notional,
                    'max_daily_loss_usd': MAX_DAILY_LOSS_USD,
                    'max_position_full_loss_risk_usd': MAX_POSITION_RISK_USD,
                    'max_total_exposure_pct': MAX_TOTAL_EXPOSURE_PCT,
                    'max_drawdown_pct': MAX_DRAWDOWN_PCT,
                    'drawdown_cap_enabled': MAX_DRAWDOWN_PCT > 0,
                    'daily_loss_cap_enabled': MAX_DAILY_LOSS_USD > 0,
                    'starting_balance_usd': STARTING_BALANCE_USD,
                    'execution_mode': 'PAPER_QUOTE_OR_OBSERVED_POOL_MODEL',
                    'execution_note': (
                        'PAPER only; tiered high-frequency entries, liquidity-scaled size and size/avoid learning from closed trades, '
                        'with ORDER_FLOW_ADAPTIVE conviction exits. Observed quotes with modeled fills, full fees and uncapped gap losses; '
                        'learned results are small paper samples, not a forecast.'
                        if is_learner() else
                        'PAPER only; 2026-10-04 ORDER_FLOW_ADAPTIVE decisions with adaptive conviction holds. '
                        'Observed quotes with modeled fills, full fees and uncapped gap losses; a planned stop is not a guaranteed fill.'
                        if is_adaptive() else
                        'PAPER only; high-frequency early micro scouts, observed quotes with modeled fills, full fees and uncapped gap losses. Main journal learns scout size; validated strategy experiments remain separate.'),
                    'entry_policy_version': ENTRY_POLICY_VERSION,
                    'exit_policy': EXIT_POLICY, 'exit_policy_version': EXIT_POLICY_VERSION,
                    'effective_config_hash': effective_config_hash(),
                    'effective_entry_thresholds': effective_entry_thresholds(),
                    'signal_source_commit': SIGNAL_SOURCE,
                    'max_quoted_candidates_per_scan': MAX_QUOTED_CANDIDATES,
                    'strict_entry_score': STRICT_ENTRY_SCORE,
                    'strict_min_conviction': STRICT_MIN_CONVICTION,
                    'strict_min_liquidity_usd': STRICT_MIN_LIQUIDITY_USD,
                    'strict_max_entry_impact_pct': STRICT_MAX_ENTRY_IMPACT_PCT,
                    'strict_max_roundtrip_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
                    'strict_max_worst_case_cost_pct': STRICT_MAX_WORST_CASE_COST_PCT,
                    'jupiter_slippage_bps': paper_quotes.SLIPPAGE_BPS,
                    'generic_dex_fee_bps': GENERIC_DEX_FEE_BPS,
                    'base_slippage_bps': BASE_SLIPPAGE_BPS,
                    'latency_buffer_bps': LATENCY_BUFFER_BPS,
                    'network_fee_sol_per_leg': NETWORK_FEE_SOL,
                    'max_price_impact_pct': MAX_PRICE_IMPACT_PCT,
                },
            }
    def token_snapshot(self, address: str) -> dict[str, Any] | None:
        with self.lock:
            coin = next((c for c in self.feed if c.get('address') == address), None)
            if not coin:
                trade = next((t for t in self.history if t.get('address') == address), None)
                if trade:
                    coin = trade.get('coin_snapshot')
            if not coin:
                return None
            return {
                'coin': coin,
                'history': self.price_history.get(address, [])[-480:],
                'position': next((p for p in self.positions if p.get('address') == address), None),
                'trades': [t for t in self.history if t.get('address') == address][:20],
                'live_tape': [e for e in read_live_tape().get('events', []) if e.get('address') == address][:80],
                'flow': self.live_flow(address, 60),
            }


def append_audit(event: str, payload: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {'schema_version': 3, 'ts': now_ms(), 'event': event, 'session_id': STATE.demo_session_id, **payload}
    event_id = record.get('event_id')
    if event_id and AUDIT_PATH.exists():
        # Repair incomplete final append after an interruption, then deduplicate
        # the outbox retry by the transaction ID committed with account state.
        with AUDIT_PATH.open('rb+') as handle:
            content = handle.read()
            if content and not content.endswith(b'\n'):
                tail_start = content.rfind(b'\n') + 1
                try:
                    json.loads(content[tail_start:])
                    handle.seek(0, 2); handle.write(b'\n'); handle.flush(); os.fsync(handle.fileno())
                except (ValueError, UnicodeDecodeError):
                    handle.truncate(tail_start); handle.flush(); os.fsync(handle.fileno())
        for line in AUDIT_PATH.read_text(encoding='utf-8').splitlines():
            if json.loads(line).get('event_id') == event_id: return
    with AUDIT_PATH.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        handle.flush(); os.fsync(handle.fileno())


def trade_metrics(trades):
    known = [t for t in trades if isinstance(t.get('pnl_usd'), (int, float)) and math.isfinite(t['pnl_usd'])]
    wins = sum(t['pnl_usd'] > 0 for t in known)
    loss = -sum(min(0., t['pnl_usd']) for t in known)
    gain = sum(max(0., t['pnl_usd']) for t in known)
    return {'closed_trades': len(known), 'wins': wins, 'losses': sum(t['pnl_usd'] < 0 for t in known),
            'breakeven': sum(t['pnl_usd'] == 0 for t in known), 'unknown_results': len(trades)-len(known),
            'win_rate': round(100*wins/len(known), 4) if known else None,
            'net_pnl_usd': round(gain-loss, 8), 'profit_factor': gain/loss if loss else None,
            'profit_factor_status': 'defined' if loss else ('infinite_no_losses' if gain else 'undefined_no_losses')}


def effective_config_hash():
    config = {'entry': EFFECTIVE_ENTRY_THRESHOLDS.as_dict(), 'entry_version': ENTRY_POLICY_VERSION,
              'exit_version': EXIT_POLICY_VERSION, 'stop_pct': STOP_LOSS_PCT, 'take_profit_pct': TAKE_PROFIT_PCT,
              'risk_buffer_pct': STOP_EXECUTION_BUFFER_PCT, 'daily_loss_usd': MAX_DAILY_LOSS_USD,
              'max_positions': MAX_POSITIONS, 'notional_usd': TRADE_NOTIONAL_USD,
              'win_reentry_seconds': WIN_REENTRY_SECONDS, 'loss_reentry_seconds': LOSS_REENTRY_SECONDS,
              'micro_flow_seconds': 10, 'ultra_flow_seconds': 20,
              'max_position_full_loss_usd': MAX_POSITION_RISK_USD,'max_exposure_pct': MAX_TOTAL_EXPOSURE_PCT,'max_drawdown_pct':MAX_DRAWDOWN_PCT,
              'max_impact_pct': STRICT_MAX_ENTRY_IMPACT_PCT, 'max_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
              'max_conservative_cost_pct': STRICT_MAX_WORST_CASE_COST_PCT,
              'execution_evidence_version': 'QUOTE_EVIDENCE_V9',
              'quote_adapter': paper_quotes.transport.ADAPTER_VERSION,
              'slippage_tolerance_bps': paper_quotes.SLIPPAGE_BPS,
              'assumed_execution_buffer_bps': paper_quotes.BUFFER_BPS,
              'simulated_execution_delay_ms': paper_quotes.SIMULATED_DELAY_MS,
              'max_signal_age_ms': paper_quotes.MAX_SIGNAL_AGE_MS}
    if is_adaptive():
        # The whole owned policy is part of the identity of an ORDER_FLOW_ADAPTIVE run.
        for key in ('micro_flow_seconds', 'ultra_flow_seconds'): config.pop(key)
        config['adaptive_strategy'] = policy_module().describe()
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def effective_entry_thresholds() -> dict[str, Any]:
    if is_learner():
        return {tier: dict(limits) for tier, limits in learner.TIER_LIMITS.items()}
    return dict(oct4.ENTRY_LIMITS) if is_adaptive() else EFFECTIVE_ENTRY_THRESHOLDS.as_dict()


def adaptive_scout_profile(entry_mode: str) -> dict[str, Any]:
    """Learn from the main PAPER journal by changing size, never by stopping samples."""
    similar = [
        trade for trade in STATE.history[:LEARNING_WINDOW]
        if str(trade.get('entry_mode') or '') == str(entry_mode or '')
        and math.isfinite(num(trade.get('pnl_pct'), math.nan))
    ][:30]
    count = len(similar)
    wins = sum(num(trade.get('pnl_pct')) > 0 for trade in similar)
    avg_pct = sum(num(trade.get('pnl_pct')) for trade in similar) / count if count else 0.0
    win_rate = wins / count * 100 if count else 0.0
    recent_losses = sum(num(trade.get('pnl_pct')) <= 0 for trade in similar[:5])

    # Keep trading to gather evidence; repeated mistakes only shrink the next scouts.
    size_multiplier = 1.0
    if count >= 4 and recent_losses >= 4:
        size_multiplier = 0.45
    elif count >= 5 and win_rate < 35 and avg_pct < 0:
        size_multiplier = 0.60
    elif count >= 5 and avg_pct < 0:
        size_multiplier = 0.75
    elif count >= 8 and win_rate >= 60 and avg_pct > 0.5:
        size_multiplier = 1.10

    return {
        'sample': count, 'wins': wins, 'win_rate': round(win_rate, 1),
        'avg_pnl_pct': round(avg_pct, 3), 'profit_factor': None,
        'recent_losses': recent_losses, 'size_multiplier': round(size_multiplier, 2),
        'bonus': round((size_multiplier - 1.0) * 100, 1),
    }


def early_requested_notional(coin: dict[str, Any], learning: dict[str, Any]) -> float:
    """Prefer many cheap scouts over a few $200 bets, especially in newborn pools."""
    liquidity = num(coin.get('liquidityUsd'))
    age = num(coin.get('ageMinutes'), 999999)
    if liquidity < 5000:
        base = 12.0
    elif liquidity < 8000:
        base = 15.0
    elif liquidity < 15000:
        base = 20.0
    elif liquidity < 30000:
        base = 30.0
    elif liquidity < 60000:
        base = 45.0
    elif liquidity < 120000:
        base = 65.0
    else:
        base = min(TRADE_NOTIONAL_USD, 90.0)

    if age <= 5:
        base *= 0.80
    elif age <= 15:
        base *= 0.90

    base *= num(learning.get('size_multiplier'), 1.0)
    return round(max(10.0, min(TRADE_NOTIONAL_USD, 100.0, base)), 2)

def _iso_ms(value: Any) -> int:
    try:
        text = str(value or '').strip()
        if not text:
            return 0
        return int(datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return 0


def gecko_new_pumpswap_pairs() -> list[dict[str, Any]]:
    """Discover brand-new PumpSwap pools without waiting for profile/boost indexing.

    Cached for 15s (~4 public requests/minute). These rows still pass the same
    rug, exact-pool Jupiter, impact and stop checks before any PAPER entry.
    """
    current = now_ms()
    with _GECKO_NEW_POOLS_LOCK:
        cached_at = int(_GECKO_NEW_POOLS_CACHE.get('ts') or 0)
        cached_pairs = list(_GECKO_NEW_POOLS_CACHE.get('pairs') or [])
    if cached_pairs and 0 <= current - cached_at <= GECKO_NEW_POOLS_TTL_MS:
        return cached_pairs

    parsed: list[dict[str, Any]] = []
    try:
        response = SESSION.get(
            GECKO_NEW_POOLS_URL,
            headers={'Accept': 'application/json;version=20230203'},
            timeout=(1.0, 3.0),
        )
        response.raise_for_status()
        rows = response.json().get('data') or []
        for row in rows:
            attrs = row.get('attributes') or {}
            rel = row.get('relationships') or {}
            dex_id = str((((rel.get('dex') or {}).get('data') or {}).get('id')) or '').lower()
            if dex_id != 'pumpswap':
                continue
            base_id = str((((rel.get('base_token') or {}).get('data') or {}).get('id')) or '')
            quote_id = str((((rel.get('quote_token') or {}).get('data') or {}).get('id')) or '')
            mint = base_id.removeprefix('solana_')
            quote_mint = quote_id.removeprefix('solana_')
            pair = str(attrs.get('address') or '')
            if quote_mint != SOL_MINT or not is_valid_solana_address(mint) or not is_valid_solana_address(pair):
                continue

            price_usd = num(attrs.get('base_token_price_usd'))
            sol_usd = num(attrs.get('quote_token_price_usd'))
            price_native = num(attrs.get('base_token_price_native_currency'))
            if price_native <= 0 and price_usd > 0 and sol_usd > 0:
                price_native = price_usd / sol_usd
            liquidity = num(attrs.get('reserve_in_usd'))
            if price_usd <= 0 or liquidity <= 0:
                continue

            name = str(attrs.get('name') or 'TOKEN / SOL')
            symbol = (name.split('/')[0].strip() or 'TOKEN')[:32]
            changes = attrs.get('price_change_percentage') or {}
            volumes = attrs.get('volume_usd') or {}
            transactions = attrs.get('transactions') or {}
            created = _iso_ms(attrs.get('pool_created_at'))
            txns = {}
            for window in ('m5', 'h1', 'h6', 'h24'):
                src = transactions.get(window) or transactions.get('m5') or {}
                txns[window] = {
                    'buys': int(num(src.get('buys'))),
                    'sells': int(num(src.get('sells'))),
                }
            volume = {window: num(volumes.get(window) or volumes.get('m5')) for window in ('m5','h1','h6','h24')}
            change = {window: num(changes.get(window) or changes.get('m5')) for window in ('m5','h1','h6','h24')}
            fdv = num(attrs.get('fdv_usd'))
            market_cap = num(attrs.get('market_cap_usd')) or fdv

            parsed.append({
                'chainId': 'solana',
                'pairAddress': pair,
                'dexId': 'pumpswap',
                'url': f'https://www.geckoterminal.com/solana/pools/{pair}',
                'baseToken': {'address': mint, 'name': symbol, 'symbol': symbol},
                'quoteToken': {'address': SOL_MINT, 'name': 'Wrapped SOL', 'symbol': 'SOL'},
                'priceUsd': price_usd,
                'priceNative': price_native,
                'marketCap': market_cap,
                'fdv': fdv,
                'liquidity': {'usd': liquidity},
                'volume': volume,
                'priceChange': change,
                'txns': txns,
                'pairCreatedAt': created,
                'info': {},
                '_early_source': 'gecko-new-pools',
            })
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return cached_pairs

    with _GECKO_NEW_POOLS_LOCK:
        _GECKO_NEW_POOLS_CACHE.update(ts=current, pairs=list(parsed))
    return parsed


STATE = State(load_state=False)


def discover() -> tuple[list[str], dict[str, dict[str, Any]]]:
    metadata: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    source_failures: list[str] = []
    sources_ok = 0
    sources = [
        ('latest', '/token-profiles/latest/v1'),
        ('boosted', '/token-boosts/top/v1'),
        ('boosted-latest', '/token-boosts/latest/v1'),
        ('community-takeover', '/community-takeovers/latest/v1'),
        ('ads-latest', '/ads/latest/v1'),
    ]
    for source_name, path in sources:
        try:
            rows = api(path)
        except Exception as exc:
            source_failures.append(f'{source_name}: {type(exc).__name__}')
            STATE.event(f'{source_name} discovery warning: {exc}')
            continue
        if not isinstance(rows, list):
            source_failures.append(f'{source_name}: invalid response')
            continue
        sources_ok += 1
        for row in rows:
            if row.get('chainId') != 'solana':
                continue
            address = str(row.get('tokenAddress') or '').strip()
            if not is_valid_solana_address(address):
                continue
            if address not in metadata:
                metadata[address] = {
                    'sources': [], 'icon': row.get('icon') or '',
                    'header': row.get('header') or '',
                    'description': row.get('description') or '',
                    'links': row.get('links') or [], 'boost_amount': 0,
                }
                order.append(address)
            info = metadata[address]
            if source_name not in info['sources']:
                info['sources'].append(source_name)
            info['icon'] = info['icon'] or row.get('icon') or ''
            info['header'] = info['header'] or row.get('header') or ''
            info['description'] = info['description'] or row.get('description') or ''
            info['links'] = info['links'] or row.get('links') or []
            info['boost_amount'] = max(num(info['boost_amount']), num(row.get('amount')), num(row.get('totalAmount')))
    checked_at = now_ms()
    with _BACKEND_HEALTH_LOCK:
        _DISCOVERY_HEALTH.update(
            status='online' if not source_failures else ('degraded' if sources_ok else 'error'),
            checked_at=checked_at, sources_ok=sources_ok, sources_failed=len(source_failures),
            last_error=source_failures[-1] if source_failures else None,
        )
    return order, metadata


def fetch_pairs(addresses: list[str], progress=None) -> list[dict[str, Any]]:
    """Refresh a rotating token batch without serially blocking the scan loop.

    DexScreener supports up to 30 token addresses per call. Four workers keep a
    240-token PAPER scan comfortably below the documented 300 request/minute
    market-data limit while bounding provider stalls.
    """
    clean: list[str] = []
    seen: set[str] = set()
    for raw in addresses:
        address = str(raw or '').strip()
        if not is_valid_solana_address(address) or address in seen:
            continue
        clean.append(address); seen.add(address)
    batches = [clean[i:i + 30] for i in range(0, len(clean), 30)]
    if not batches:
        return []

    def request_batch(batch: list[str]) -> list[dict[str, Any]] | None:
        try:
            response = requests.get(
                f"{DEX_API}/tokens/v1/solana/{','.join(batch)}",
                headers={'User-Agent': 'NEO-Meme-Market-Monitor/2.0', 'Accept': 'application/json'},
                timeout=(1.5, MARKET_BATCH_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
            rows = response.json()
            return rows if isinstance(rows, list) else None
        except (requests.RequestException, ValueError, TypeError):
            return None

    def one(batch: list[str]) -> tuple[list[dict[str, Any]], int]:
        rows = request_batch(batch)
        if rows is not None:
            return rows, 0
        # A failed 30-token call is retried as two smaller calls. This keeps the
        # normal request rate unchanged and only spends extra requests when the
        # provider actually timed out/closed the connection.
        recovered: list[dict[str, Any]] = []
        unavailable = 0
        time.sleep(0.12)
        for start in range(0, len(batch), 15):
            part = batch[start:start + 15]
            retry_rows = request_batch(part)
            if retry_rows is None:
                unavailable += len(part)
            else:
                recovered.extend(retry_rows)
        return recovered, unavailable

    pairs: list[dict[str, Any]] = []
    unavailable_addresses = 0
    completed_addresses = 0
    total_addresses = len(clean)
    with ThreadPoolExecutor(max_workers=min(MARKET_BATCH_WORKERS, len(batches))) as pool:
        futures = {pool.submit(one, batch): len(batch) for batch in batches}
        for future in as_completed(futures):
            rows, unavailable = future.result()
            if rows:
                pairs.extend(rows)
            unavailable_addresses += unavailable
            completed_addresses += futures[future]
            if progress is not None:
                try:
                    progress(completed_addresses, total_addresses)
                except Exception:
                    pass
    checked_at = now_ms()
    with _BACKEND_HEALTH_LOCK:
        _MARKET_DATA_HEALTH.update(
            status='online' if unavailable_addresses == 0 else 'degraded',
            checked_at=checked_at, requested_tokens=total_addresses,
            unavailable_tokens=unavailable_addresses, returned_pairs=len(pairs),
        )
    if unavailable_addresses:
        STATE.event(f'Market data warning: {unavailable_addresses}/{total_addresses} token-а unavailable след retry')
    return pairs


def best_pairs(pairs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if pair.get('chainId') != 'solana':
            continue
        address = (pair.get('baseToken') or {}).get('address')
        if not address:
            continue
        liquidity = num((pair.get('liquidity') or {}).get('usd'))
        old = best.get(address)
        old_liquidity = num((old.get('liquidity') or {}).get('usd')) if old else -1
        if old is None or liquidity > old_liquidity:
            best[address] = pair
    return best


def exact_position_pair(position: dict[str, Any], pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    address = str(position.get('address') or '')
    pair_address = str(position.get('pairAddress') or '')
    if not address or not pair_address:
        return None
    for pair in pairs:
        if pair.get('chainId') != 'solana':
            continue
        base_address = str((pair.get('baseToken') or {}).get('address') or '')
        current_pair = str(pair.get('pairAddress') or '')
        if base_address == address and current_pair == pair_address:
            return pair
    return None


def score_pair(pair: dict[str, Any], meta: dict[str, Any]):
    liq = num((pair.get('liquidity') or {}).get('usd'))
    volume = pair.get('volume') or {}
    vol_h1 = num(volume.get('h1'))
    mc = num(pair.get('marketCap')) or num(pair.get('fdv'))
    changes = pair.get('priceChange') or {}
    change_m5 = num(changes.get('m5'))
    change_h1 = num(changes.get('h1'))
    tx5 = (pair.get('txns') or {}).get('m5') or {}
    buys, sells = num(tx5.get('buys')), num(tx5.get('sells'))
    tx_count = buys + sells
    created = int(pair.get('pairCreatedAt') or 0)
    age = max(0.0, (now_ms() - created) / 60000) if created else 999999
    buy_sell = buys / max(sells, 1)
    vol_liq = vol_h1 / max(liq, 1)
    liq_mc = liq / max(mc, 1) if mc else 0
    score, signals = 36.0, []

    if liq >= 50000:
        score += 18; signals.append(signal('positive', 'Силна ликвидност', f'${liq:,.0f}'))
    elif liq >= 20000:
        score += 13; signals.append(signal('positive', 'Добра ликвидност', f'${liq:,.0f}'))
    elif liq >= 10000:
        score += 7; signals.append(signal('neutral', 'Приемлива ликвидност', f'${liq:,.0f}'))
    elif liq >= 5000:
        score -= 4; signals.append(signal('risk', 'Тънка ликвидност', f'${liq:,.0f}'))
    elif liq >= 3000:
        score -= 10; signals.append(signal('risk', 'Много ранна тънка ликвидност', f'${liq:,.0f}'))
    else:
        score -= 25; signals.append(signal('risk', 'Недостатъчна ликвидност', f'${liq:,.0f}'))

    if vol_h1 >= 50000:
        score += 12; signals.append(signal('positive', 'Силен 1h volume', f'${vol_h1:,.0f}'))
    elif vol_h1 >= 10000:
        score += 7; signals.append(signal('positive', 'Активен 1h volume', f'${vol_h1:,.0f}'))
    elif vol_h1 < 1000 and age > 15:
        score -= 8; signals.append(signal('risk', 'Слаб volume', f'${vol_h1:,.0f}'))
    elif vol_h1 < 1000:
        signals.append(signal('neutral', 'Нов pool — volume още се натрупва', f'${vol_h1:,.0f}'))

    if tx_count >= 120:
        score += 10; signals.append(signal('positive', 'Много активни сделки', f'{int(tx_count)} tx / 5m'))
    elif tx_count >= 35:
        score += 6; signals.append(signal('positive', 'Добра активност', f'{int(tx_count)} tx / 5m'))
    elif tx_count < 8 and age > 15:
        score -= 6; signals.append(signal('risk', 'Малко сделки', f'{int(tx_count)} tx / 5m'))
    elif tx_count < 8:
        signals.append(signal('neutral', 'Първи сделки', f'{int(tx_count)} tx / 5m'))

    if 1.05 <= buy_sell <= 2.8:
        score += 8; signals.append(signal('positive', 'Купувачите водят', f'Buy/Sell {buy_sell:.2f}x'))
    elif buy_sell > 5:
        score -= 6; signals.append(signal('risk', 'Неестествен buy imbalance', f'{buy_sell:.2f}x'))
    elif buy_sell < 0.65:
        score -= 8; signals.append(signal('risk', 'Продавачите доминират', f'{buy_sell:.2f}x'))

    if 1.5 <= change_m5 <= 18:
        score += 8; signals.append(signal('positive', 'Здрав кратък momentum', f'{change_m5:+.1f}% / 5m'))
    elif 18 < change_m5 <= 45:
        score += 3; signals.append(signal('neutral', 'Бърз pump', f'{change_m5:+.1f}% / 5m'))
    elif change_m5 > 60:
        score -= 12; signals.append(signal('risk', 'Вертикален pump', f'{change_m5:+.1f}% / 5m'))
    elif change_m5 < -20:
        score -= 12; signals.append(signal('risk', 'Силен спад', f'{change_m5:+.1f}% / 5m'))

    if 0.25 <= age <= 360:
        score += 8; signals.append(signal('positive', 'Ранен етап', f'{age:.1f} мин.'))
    elif age < 0.25:
        score += 4; signals.append(signal('neutral', 'Нов pool под 15 секунди', f'{age:.2f} мин.'))
    elif age > 4320:
        score -= 4
    if mc > 0:
        if liq_mc >= 0.15:
            score += 9; signals.append(signal('positive', 'Добро liquidity/MC', f'{liq_mc * 100:.1f}%'))
        elif liq_mc < 0.03:
            score -= 10; signals.append(signal('risk', 'Слаб liquidity/MC', f'{liq_mc * 100:.1f}%'))

    if 0.10 <= vol_liq <= 4.0:
        score += 6
    elif vol_liq > 7:
        score -= 12; signals.append(signal('risk', 'Volume/liquidity extreme', f'{vol_liq:.1f}x'))

    if any('boost' in source for source in meta.get('sources', [])):
        score += 4; signals.append(signal('neutral', 'Boosted discovery', 'Повишена market видимост'))
    if abs(change_h1) > 250:
        score -= 8; signals.append(signal('risk', 'Екстремен 1h move', f'{change_h1:+.0f}%'))

    score = round(clamp(score), 1)
    risk = round(100 - score, 1)
    posture = 'SETUP' if score >= ENTRY_SCORE else 'WATCH' if score >= 60 else 'WAIT' if score >= 45 else 'SKIP'
    return score, risk, posture, signals[:8]


def make_coin(address: str, pair: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    score, risk, posture, signals = score_pair(pair, meta)
    base, info = pair.get('baseToken') or {}, pair.get('info') or {}
    volume, changes, txns = pair.get('volume') or {}, pair.get('priceChange') or {}, pair.get('txns') or {}
    created = int(pair.get('pairCreatedAt') or 0)
    return {
        'address': address,
        'pairAddress': pair.get('pairAddress') or '',
        'dexId': pair.get('dexId') or '',
        'dexUrl': pair.get('url') or '',
        'name': base.get('name') or 'Unknown token',
        'symbol': base.get('symbol') or 'TOKEN',
        'imageUrl': info.get('imageUrl') or meta.get('icon') or '',
        'headerUrl': info.get('header') or meta.get('header') or '',
        'description': meta.get('description') or '',
        'priceUsd': num(pair.get('priceUsd')),
        'priceNative': num(pair.get('priceNative')),
        'quoteTokenAddress': (pair.get('quoteToken') or {}).get('address'),
        'marketCap': num(pair.get('marketCap')),
        'fdv': num(pair.get('fdv')),
        'liquidityUsd': num((pair.get('liquidity') or {}).get('usd')),
        'volume': {k: num(volume.get(k)) for k in ('m5', 'h1', 'h6', 'h24')},
        'priceChange': {k: num(changes.get(k)) for k in ('m5', 'h1', 'h6', 'h24')},
        'txns': {
            k: {'buys': int(num((txns.get(k) or {}).get('buys'))), 'sells': int(num((txns.get(k) or {}).get('sells')))}
            for k in ('m5', 'h1', 'h6', 'h24')
        },
        'pairCreatedAt': created,
        'ageMinutes': round(max(0, (now_ms() - created) / 60000), 1) if created else None,
        'sources': meta.get('sources', []),
        'boostAmount': num(meta.get('boost_amount')),
        'websites': (info.get('websites') or [])[:3],
        'socials': (info.get('socials') or [])[:5],
        'score': score, 'riskScore': risk, 'posture': posture,
        'signals': signals, 'updatedAt': now_ms(),
    }

class Monitor:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.scan_lock = threading.Lock()
        self.position_lock = threading.Lock()
        self.position_poll_lock = threading.Lock()
        self.entry_lock = threading.Lock()
        self.discovery = runtime.DiscoveryCache(discover, refresh_seconds=DISCOVERY_REFRESH_SECONDS, max_age_seconds=90)
        self.universe = discovery_universe.RollingUniverse(
            DISCOVERY_UNIVERSE_PATH, max_items=DISCOVERY_UNIVERSE_MAX,
            ttl_ms=DISCOVERY_UNIVERSE_TTL_MS, batch_size=DISCOVERY_SCAN_BATCH,
            writer=runtime.atomic_json,
        )
        self.universe_seeded = self.universe.stats()['size'] > 0
        self.last_discovery_snapshot: set[str] = set()
        self.new_universe_since_start = 0
        self.scanned_address_slots_since_start = int(getattr(STATE, 'scanned_address_slots_total', 0) or 0)

    def ensure_universe_seeded(self) -> None:
        if self.universe_seeded:
            return
        seeds = [a for a in discovery_universe.seed_addresses_from_json(
            [ALL_TIME_HISTORY_PATH, STRATEGY_LAB_PATH, STRATEGY_LAB_COMPACT_PATH]
        ) if is_valid_solana_address(a)]
        self.universe.observe(seeds, {a: {'sources': ['paper-history-seed']} for a in seeds})
        self.universe_seeded = True

    def prewarm_entry_checks(self, feed: list[dict[str, Any]]) -> None:
        """Warm only the most time-sensitive candidates; avoid provider queues."""
        candidates = []
        for coin in feed:
            age = num(coin.get('ageMinutes'), 999999)
            if num(coin.get('score')) < STRICT_ENTRY_SCORE or num(coin.get('liquidityUsd')) < STRICT_MIN_LIQUIDITY_USD or (age > 360 and not is_adaptive()):
                continue
            tx = (coin.get('txns') or {}).get('m5') or {}
            activity = num(tx.get('buys')) + num(tx.get('sells'))
            priority = (
                1 if age <= 45 else 0,
                1 if age <= 180 else 0,
                activity,
                num(coin.get('score')),
            )
            candidates.append((priority, coin))
        candidates.sort(key=lambda row: row[0], reverse=True)
        for _, coin in candidates[:8]:
            price_integrity.check(coin)
            rug_guard.check(coin)

    def update_price_history(self, feed: list[dict[str, Any]]) -> None:
        stamp = now_ms()
        for coin in feed[:50]:
            price = num(coin.get('priceUsd'))
            if price <= 0:
                continue
            address = coin['address']
            points = STATE.price_history.setdefault(address, [])
            points.append({
                'ts': stamp,
                'price': price,
                'liquidity': round(num(coin.get('liquidityUsd')), 2),
                'volumeH1': round(num((coin.get('volume') or {}).get('h1')), 2),
                'score': num(coin.get('score')),
            })
            STATE.price_history[address] = points[-480:]

    def market_context(self, coin: dict[str, Any], position: dict[str, Any] | None = None) -> dict[str, Any]:
        address = coin.get('address') or (position or {}).get('address')
        fast = STATE.live_flow(address, 30, str(coin.get('pairAddress') or ''))
        slow = STATE.live_flow(address, 300, str(coin.get('pairAddress') or ''))
        # One conviction model for entries and exits: order_flow_adaptive_oct4.
        return oct4.market_context(coin, fast, slow, (position or {}).get('entry_liquidity_usd'))

    def _quote_unavailable(self, position, session, reason, error='no_sell_route'):
        with STATE.lock:
            live = next((p for p in STATE.positions if p.get('id') == position.get('id')), None)
            if live is None or STATE.demo_session_id != session: return
            attempts = int(live.get('exit_retry_count') or 0) + 1
            delay = min(60_000, 1000 * 2 ** min(attempts-1, 6))
            quote_at = num(live.get('execution_quote_at'), num(live.get('updated_at')))
            live.update(quote_status='unavailable', valuation_status='unavailable',
                        exit_state='UNSELLABLE' if attempts >= 6 else ('PENDING_EXIT' if reason else 'VALUATION_UNAVAILABLE'),
                        quote_error=error, quote_error_at=now_ms(), exit_retry_count=attempts,
                        next_exit_retry_at=now_ms()+delay,
                        last_known_pnl_usd=live.get('pnl_usd'), valuation_age_ms=max(0, now_ms()-quote_at),
                        conservative_risk_usd=num(live.get('capital_committed_usd'), num(live.get('notional_usd'))))
            if reason: live['pending_exit_reason'] = reason
            if attempts == 1 or attempts == 6:
                STATE.event('Unavailable sell route; PAPER exposure retained, bounded retries scheduled.')
            STATE.save()

    def book_paper_exit(self, position, quote, reason, coin=None):
        """Book a simulated sale once; partial legs aggregate into one completed trade.

        No submission/signing is reachable here. Net proceeds already include
        quote AMM fees/impact and the exit network fee; entry costs are allocated
        once to each disposed fraction of the original position.
        """
        coin = coin or {}
        with STATE.lock:
            live = next((p for p in STATE.positions if p.get('id') == position.get('id')), None)
            if live is None or position.get('session_id', STATE.demo_session_id) != STATE.demo_session_id: return None
            if any(t.get('id') == live.get('id') for t in STATE.history): return None
            raw = int(live.get('jupiter_token_raw_amount') or 0)
            sold_raw = int(quote.get('token_input_raw') or raw)
            if raw and not 0 < sold_raw <= raw: raise ValueError('exit raw quantity exceeds position')
            fraction = sold_raw/raw if raw else 1.0
            notional = num(live.get('notional_usd'))
            entry_cost = num(live.get('entry_network_fee_usd')) + num(live.get('entry_account_reserve_usd'))
            allocated = (notional+entry_cost)*fraction
            net = num(quote.get('net_proceeds_usd'), math.nan)
            if not math.isfinite(net): raise ValueError('invalid exit net proceeds')
            pnl = net-allocated
            total_pnl = num(live.get('realized_partial_pnl_usd'))+pnl
            original_notional = num(live.get('original_notional_usd'), notional)
            before = STATE.demo_balance_usd
            balance = round(before+pnl, 8)
            leg = {'sold_token_raw': sold_raw, 'fraction_remaining_sold': fraction, 'net_proceeds_usd': net,
                   'allocated_entry_cost_usd': allocated, 'pnl_usd': pnl, 'quoted_at': quote.get('quoted_at'),
                   'execution_source': quote.get('execution_source'), 'exit_network_fee_usd': num(quote.get('network_fee_usd'))}
            legs = list(live.get('partial_fills') or [])+[leg]
            event_id = f"{live['id']}:{raw}:{sold_raw}:{quote.get('quoted_at',now_ms())}"
            if fraction < 1:
                remaining = dict(live, quantity=num(live.get('quantity'))*(1-fraction),
                    jupiter_token_raw_amount=raw-sold_raw, notional_usd=notional*(1-fraction),
                    entry_network_fee_usd=num(live.get('entry_network_fee_usd'))*(1-fraction),
                    entry_account_reserve_usd=num(live.get('entry_account_reserve_usd'))*(1-fraction),
                    capital_committed_usd=(notional+entry_cost)*(1-fraction), original_notional_usd=original_notional,
                    realized_partial_pnl_usd=total_pnl, partial_fills=legs, pending_exit_reason=reason,
                    pnl_usd=num(live.get('pnl_usd'))*(1-fraction), planned_risk_usd=num(live.get('planned_risk_usd'))*(1-fraction))
                positions = [remaining if p.get('id') == live['id'] else p for p in STATE.positions]
                STATE.commit('PARTIAL_EXIT', dict(leg, id=event_id, position_id=live['id']),
                             positions=positions, demo_balance_usd=balance)
                return remaining
            closed = dict(position, id=live['id'], session_id=STATE.demo_session_id,
                notional_usd=original_notional, pnl_usd=round(total_pnl, 8), pnl_pct=round(total_pnl/max(original_notional,1e-18)*100, 8),
                closed_at=now_ms(), exit_price=coin.get('priceUsd'), execution_exit_price=quote.get('fill_price'),
                exit_reason=reason, exit_net_proceeds_usd=net, exit_gross_proceeds_usd=quote.get('gross_proceeds_usd'),
                exit_dex_fee_usd=num(quote.get('dex_fee_usd')), exit_network_fee_usd=num(quote.get('network_fee_usd')),
                exit_price_impact_pct=num(quote.get('impact_pct')), exit_slippage_pct=num(quote.get('slippage_pct')),
                exit_quote=quote.get('raw_quote'), exit_liquidity_usd=coin.get('liquidityUsd'), partial_fills=legs,
                balance_before=before, balance_after=balance, paper_stop_capped=False, simulated_fill=True,
                jupiter_exit_quote_at=quote.get('quoted_at'), exit_policy_version=position.get('exit_policy_version', exit_policy.VERSION),
                exit_route_matches_entry_pool=quote.get('route_matches_entry_pool'), fees_included_in_quote=True,
                execution_source=quote.get('execution_source', 'MODEL_V1'), exit_state='CLOSED')
            STATE.commit('EXIT', closed, demo_balance_usd=balance,
                         positions=[p for p in STATE.positions if p.get('id') != live['id']], history=[closed]+STATE.history)
            STATE.event(f"PAPER EXIT #{closed.get('trade_no')} {closed.get('symbol')} · {reason} · {closed['pnl_pct']:+.2f}% net")
            return closed

    def update_positions(self, by_address: dict[str, dict[str, Any]]) -> None:
        # Called under position_lock; never hold STATE.lock across network calls.
        with STATE.lock:
            positions = [dict(p) for p in STATE.positions]
            session = STATE.demo_session_id
            pinned = {k:dict(v) for k,v in STATE.position_market.items()}
        for position in positions:
            address, pair = position.get('address'), position.get('pairAddress')
            key = f'{address}:{pair}'
            incoming = by_address.get((address,pair)) or by_address.get(address)
            coin = incoming if incoming and incoming.get('pairAddress') == pair else pinned.get(key)
            coin = coin or position.get('coin_snapshot') or {}
            if coin.get('pairAddress') != pair: coin = {}
            stamp = now_ms()
            policy = str(position.get('exit_policy') or 'fixed')
            target_only = policy == 'fixed_targets'
            # Target-only positions always re-evaluate against the current fresh
            # executable mark; a previously queued stop/TP may disappear if the
            # market recovered before a sell route became available.
            reason = None if target_only else position.get('pending_exit_reason')
            quote_at = num(position.get('execution_quote_at'), num(position.get('updated_at')))
            if stamp < num(position.get('next_exit_retry_at')):
                with STATE.lock:
                    live = next((p for p in STATE.positions if p.get('id') == position.get('id')),None)
                    if live is not None: live['valuation_age_ms'] = max(0, stamp-quote_at)
                continue
            fresh_market = 0 <= stamp-num(coin.get('updatedAt')) <= entry_policy.MAX_FEED_AGE_MS
            if not target_only and fresh_market and num(coin.get('liquidityUsd')) < num(position.get('entry_liquidity_usd'))*.80:
                reason = reason or 'LIQUIDITY_EMERGENCY'
            if not target_only and not fresh_market and stamp-num(coin.get('updatedAt')) > 60_000:
                reason = reason or 'STALE_MARKET_EXIT'
            is_quote = position.get('execution_mode') in {'JUPITER_QUOTE_V2', 'PUMPSWAP_RPC_ENTRY_V1'}
            sol_usd = sol_usd_from_coin(coin) or sol_usd_market_price()
            network = max(.03, NETWORK_FEE_SOL*sol_usd)
            # Every quote-backed position uses one full token->USDC route.
            # Native token->SOL reserve marks remain diagnostics and cannot
            # invent realized USDC or add a second request on route failure.
            quote = (paper_quotes.position_mark(position,coin,network,force=bool(reason)) if is_quote
                     else exit_execution(coin,num(position.get('quantity'))) if fresh_market else None)
            mark_valid = quote is not None and math.isfinite(num(quote.get('net_proceeds_usd'),math.nan)) and (not is_quote or 0 <= now_ms()-num(quote.get('quoted_at')) <= entry_policy.MAX_ENTRY_QUOTE_AGE_MS)
            if training_bridge.accepting():
                training_bridge.observe(coin,STATE.live_flow(address,pair_address=pair),
                    safety=rug_guard.check(coin),validation=price_integrity.check(coin),
                    context=self.market_context(coin,position),
                    quotes={'mark':quote} if mark_valid else None, reasons=['exit_quote'] if not mark_valid else None,
                    now=now_ms())
            if not mark_valid:
                self._quote_unavailable(position,session,reason)
                continue
            notional = num(position.get('notional_usd'))
            entry_cost = num(position.get('entry_network_fee_usd'))+num(position.get('entry_account_reserve_usd'))
            pnl = num(quote.get('net_proceeds_usd'))-notional-entry_cost
            pct = pnl/max(notional,1e-18)*100
            peak_pct = max(pct,num(position.get('peak_net_pnl_pct'), pct))
            hold = (now_ms()-int(position.get('opened_at',now_ms())))/60000
            # A position is managed by the policy recorded when it was opened,
            # so changing the active profile never rewrites an open trade's rules.
            context = self.market_context(coin,position) if policy in ('adaptive', oct4.EXIT_POLICY) else {}
            market = num(coin.get('priceUsd'), num(position.get('current_price')))
            entry = num(position.get('entry_price'))
            peak_price = max(market, num(position.get('peak_price')))
            # The observed exact-pool price drives context triggers only while
            # it is fresh; otherwise the decision falls back to the net mark.
            signal_ok = fresh_market and num(coin.get('priceUsd')) > 0 and entry > 0
            signal_pct = (market-entry)/entry*100 if signal_ok else None
            peak_signal_pct = (peak_price-entry)/entry*100 if signal_ok else None
            # The stop recorded at entry stays with the position when the profile changes.
            own_stop = num(position.get('stop_loss_pct'), 0.0) or oct4.CONFIG['stop_loss_pct']
            def decide(net_now, peak_now):
                if policy == oct4.EXIT_POLICY:
                    return oct4.exit_reason(context, net_pct=net_now, peak_net_pct=peak_now, hold_minutes=hold,
                        signal_pct=signal_pct, peak_signal_pct=peak_signal_pct, stop_pct=own_stop)
                return exit_policy.exit_reason(position, context, net_pct=net_now, peak_net_pct=peak_now,
                    hold_minutes=hold, stop_pct=own_stop,
                    take_profit_pct=num(position.get('take_profit_net_pct'), TAKE_PROFIT_PCT) if policy in ('fixed', 'fixed_targets') else TAKE_PROFIT_PCT,
                    policy=policy)
            reason = reason or decide(pct, peak_pct)
            if not target_only and num(quote.get('impact_pct')) >= max(EXIT_IMPACT_EMERGENCY_PCT,num(position.get('entry_price_impact_pct'))+.50):
                reason = reason or 'EXIT_IMPACT_EMERGENCY'
            if reason and is_quote and quote.get('from_cache'):
                quote = paper_quotes.position_mark(position,coin,network,force=True)
                if quote is None:
                    self._quote_unavailable(position,session,reason)
                    continue
                if not math.isfinite(num(quote.get('net_proceeds_usd'),math.nan)):
                    self._quote_unavailable(position,session,reason,'invalid_sell_quote')
                    continue
                if not 0 <= now_ms()-num(quote.get('quoted_at')) <= entry_policy.MAX_ENTRY_QUOTE_AGE_MS:
                    self._quote_unavailable(position,session,reason,'stale_sell_quote')
                    continue
                pnl = num(quote.get('net_proceeds_usd'))-notional-entry_cost
                pct = pnl/max(notional,1e-18)*100
                peak_pct = max(peak_pct,pct)
                # For target-only mode both TP and SL must still hold on the
                # forced fresh quote. Never realize an old cached threshold.
                if target_only or reason.startswith(('TAKE_PROFIT', 'ADAPTIVE_TP', 'ADAPTIVE_TRAILING', 'CONVICTION_PROFIT')):
                    reason = decide(pct, peak_pct)
            adaptive_fields = {}
            if policy == oct4.EXIT_POLICY:
                adaptive_fields = dict(conviction=context.get('conviction'), hold_mode=context.get('mode'),
                    adaptive_target_pct=context.get('target_pct'),
                    adaptive_max_hold_minutes=context.get('max_hold_minutes'),
                    adaptive_trail_arm_pct=context.get('trail_arm_pct'),
                    adaptive_trail_pct=context.get('trail_pct'),
                    peak_signal_pnl_pct=peak_signal_pct, exit_signal_basis='OBSERVED_POOL_PRICE' if signal_ok else 'NET_MARK_FALLBACK',
                    current_score=coin.get('score', position.get('current_score')))
            updated = dict(position, **adaptive_fields, current_price=market,peak_price=peak_price,
                pnl_usd=round(pnl,8),pnl_pct=round(pct,8),peak_net_pnl_pct=peak_pct,
                mfe_net_pct=max(peak_pct,num(position.get('mfe_net_pct'),pct)),
                mae_net_pct=min(pct,num(position.get('mae_net_pct'),pct)),
                signal_pnl_pct=(market-entry)/entry*100 if entry else None,
                active_stop_signal_trigger_pct=None, legacy_chart_stop_ignored=position.get('stop_signal_trigger_pct'),
                planned_stop_net_pct=-own_stop,hard_stop_net_pct=None,paper_stop_capped=False,
                current_execution_price=quote.get('fill_price'), quote_status='fresh', valuation_status='available',
                valuation_age_ms=max(0,now_ms()-num(quote.get('quoted_at'),now_ms())), last_known_pnl_usd=pnl,
                conservative_risk_usd=notional+entry_cost, execution_quote_at=quote.get('quoted_at',now_ms()),
                execution_quote_source=quote.get('execution_source','MODEL_V1'), updated_at=now_ms(),
                pending_exit_reason=reason,exit_state='PENDING_EXIT' if reason else 'OPEN',exit_retry_count=0,next_exit_retry_at=0,
                exit_policy_version=(oct4.EXIT_POLICY_VERSION if policy == oct4.EXIT_POLICY
                    else exit_policy.ADAPTIVE_VERSION if policy == 'adaptive'
                    else exit_policy.USER_FIXED_VERSION if policy == 'fixed_targets' else exit_policy.VERSION),
                market_context=context, estimated_exit_dex_fee_usd=num(quote.get('dex_fee_usd')),
                estimated_exit_network_fee_usd=num(quote.get('network_fee_usd')),
                estimated_exit_price_impact_pct=num(quote.get('impact_pct')),
                estimated_exit_slippage_pct=num(quote.get('slippage_pct'))+num(quote.get('latency_pct')))
            with STATE.lock:
                live = next((p for p in STATE.positions if p.get('id') == position.get('id')),None)
                if live is None or STATE.demo_session_id != session: continue
                if not reason:
                    live.update(updated)
                    continue
            self.book_paper_exit(updated,quote,reason,coin)

    def fast_position_check(self) -> None:
        if not self.position_lock.acquire(blocking=False): return
        try:
            with STATE.lock:
                coins={c['address']:dict(c) for c in STATE.feed}
                for key, coin in STATE.position_market.items():
                    coins[(coin.get('address'),coin.get('pairAddress'))]=dict(coin)
            # Quote the held raw token amount directly; do not block a stop on a
            # slow DexScreener discovery request or select a different chart pool.
            self.update_positions(coins)
            with STATE.lock: STATE.save()
        except Exception as exc:
            with STATE.lock: STATE.event('Проверка на позицията: '+type(exc).__name__)
        finally:
            self.position_lock.release()

    def run_position_guard(self) -> None:
        while not self.stop_event.is_set():
            if STATE.positions: self.fast_position_check()
            # ORDER_FLOW_ADAPTIVE evaluates entries once per market scan only.
            if STATE.running and ENTRY_ON_POSITION_GUARD:
                with STATE.lock: feed=[dict(c) for c in STATE.feed]
                self.maybe_open(feed)
            self.stop_event.wait(POSITION_SCAN_SECONDS)

    def maybe_open(self, feed: list[dict[str, Any]]) -> None:
        if not STATE.running or not self.entry_lock.acquire(blocking=False): return
        report = {'policy_version': ENTRY_POLICY_VERSION, 'strategy': SIGNAL_STRATEGY, 'checked_at': now_ms(),
                  'candidates': len(feed), 'evaluated': 0, 'signal_passed': 0, 'safety_passed': 0,
                  'quoted': 0, 'quote_returned': 0, 'quote_passed': 0, 'opened': 0,
                  'rejections': {}, 'post_signal_rejections': {}, 'examples': [], 'max_positions': MAX_POSITIONS}
        try:
            self._maybe_open_checked(feed, report)
        except Exception as exc:
            entry_policy.record(report,['entry_error'],metrics={'type':type(exc).__name__})
        finally:
            with STATE.lock:
                STATE.entry_diagnostics = entry_policy.finish(report)
            self.entry_lock.release()

    def _maybe_open_checked(self, feed: list[dict[str, Any]], report: dict[str, Any]) -> None:
        post_signal_addresses: set[str] = set()
        def reject(report, reasons, coin=None, metrics=None):
            entry_policy.record(report,reasons,coin,metrics)
            if coin and str(coin.get('address') or '') in post_signal_addresses:
                post = report.setdefault('post_signal_rejections', {})
                for reason in reasons:
                    post[reason] = post.get(reason, 0) + 1
            if coin:
                training_bridge.observe(coin,STATE.live_flow(coin.get('address'),pair_address=coin.get('pairAddress')),
                    reasons=reasons,now=now_ms())
        session_at_check = STATE.demo_session_id
        with STATE.lock:
            trade_controls = normalize_engine_settings(None, STATE.engine_settings) if STRATEGY_PROFILE == OCT4_FIXED_PROFILE else {
                'trade_notional_usd': TRADE_NOTIONAL_USD, 'stop_loss_pct': STOP_LOSS_PCT,
                'take_profit_pct': TAKE_PROFIT_PCT, 'version': None,
            }
        configured_notional = num(trade_controls.get('trade_notional_usd'), TRADE_NOTIONAL_USD)
        configured_stop = num(trade_controls.get('stop_loss_pct'), STOP_LOSS_PCT)
        configured_tp = num(trade_controls.get('take_profit_pct'), TAKE_PROFIT_PCT)
        if STATE.pending_audit:
            with STATE.lock: STATE.save()
            if STATE.pending_audit:
                reject(report, ['audit_pending'])
                return
        STATE.refresh_risk_day()
        if MAX_DAILY_LOSS_USD > 0 and STATE.risk_day_pnl() <= -MAX_DAILY_LOSS_USD:
            reject(report, ['daily_limit'])
            return
        # A route that is briefly missing blocks new exposure. One that stayed
        # missing through every retry is written off as reserved capital instead
        # of freezing the engine, when the profile says so.
        if any(p.get('valuation_status') == 'unavailable'
               and (UNSELLABLE_BLOCKS_ENTRIES or p.get('exit_state') != 'UNSELLABLE') for p in STATE.positions):
            reject(report,['liquidation_unavailable'])
            return
        if MAX_DRAWDOWN_PCT > 0 and (1-STATE.equity_usd()/max(STATE.equity_peak_usd,1))*100 >= MAX_DRAWDOWN_PCT:
            reject(report,['drawdown_limit'])
            return
        if slots_in_use(STATE.positions) >= MAX_POSITIONS:
            reject(report, ['position_open'])
            return
        if STATE.available_balance_usd() < min(configured_notional, 10.0):
            reject(report, ['balance'])
            return
        open_addresses = {p.get('address') for p in STATE.positions}
        now = now_ms()
        # Outcome-aware cooldown: keep sampling many different coins, but avoid
        # immediate revenge loops on the same mint.
        fixed_cooldown = is_adaptive() and not is_learner()
        recent = oct4.cooldown_addresses(STATE.history, now, LOSS_REENTRY_SECONDS) if fixed_cooldown else set()
        learned = learning_table(STATE.history, now) if is_learner() else None
        for trade in ([] if fixed_cooldown else STATE.history):
            recent_address = trade.get('address')
            closed_at = int(trade.get('closed_at') or 0)
            if not recent_address or closed_at <= 0:
                continue
            cooldown = LOSS_REENTRY_SECONDS if num(trade.get('pnl_pct')) <= 0 else WIN_REENTRY_SECONDS
            if now - closed_at < cooldown * 1000:
                recent.add(recent_address)
        for coin in feed:
            if slots_in_use(STATE.positions) >= MAX_POSITIONS:
                break
            address = coin.get('address')
            if not address or address in open_addresses or address in recent:
                reject(report, ['cooldown'], coin)
                continue
            report['evaluated'] += 1
            score = num(coin.get('score'))
            liquidity = num(coin.get('liquidityUsd'))
            age = num(coin.get('ageMinutes'), 999999)
            change_m5 = num((coin.get('priceChange') or {}).get('m5'))
            tx_m5 = (coin.get('txns') or {}).get('m5') or {}
            buys_m5 = num(tx_m5.get('buys'))
            sells_m5 = num(tx_m5.get('sells'))
            buy_sell_ratio = buys_m5 / max(sells_m5, 1.0)
            market_cap = num(coin.get('marketCap') or coin.get('fdv'))
            liquidity_mc_ratio = liquidity / max(market_cap, 1.0)

            tier_seen: dict[str, str] = {}
            if is_learner():
                flow_seconds = int(learner.CONFIG['entry_flow_window_seconds'])
                def signal_check(c, f, ctx):
                    tier, why = learner.entry_tier(c, f, ctx, now=now_ms())
                    if why:
                        return why
                    # A candidate sized as CORE must still be CORE when it is committed.
                    if tier_seen.get('tier') == 'CORE' and tier != 'CORE':
                        return ['gold_signal']
                    tier_seen.setdefault('tier', tier)
                    return []
            elif is_adaptive():
                flow_seconds = int(oct4.CONFIG['entry_flow_window_seconds'])
                def signal_check(c, f, ctx):
                    return oct4.signal_rejections(c, f, ctx, now=now_ms())
            else:
                flow_seconds = 10 if age <= 15 else 20 if age <= 45 else 30
                def signal_check(c, f, ctx):
                    return entry_policy.signal_rejections(
                        c, f, ctx, min_score=EFFECTIVE_ENTRY_THRESHOLDS.min_score,
                        min_liquidity=EFFECTIVE_ENTRY_THRESHOLDS.min_liquidity,
                        min_conviction=EFFECTIVE_ENTRY_THRESHOLDS.min_conviction, now=now_ms())
            flow = STATE.live_flow(address, flow_seconds, str(coin.get('pairAddress') or ''))
            context = self.market_context(coin)
            rejected = signal_check(coin, flow, context)
            if rejected:
                reject(report, rejected, coin, {
                    'score': score, 'liquidity_usd': liquidity, 'conviction': context.get('conviction'),
                    'flow_trades': flow.get('trades'), 'flow_ratio': flow.get('buy_sell_usd_ratio'),
                } if is_adaptive() else None)
                continue
            report['signal_passed'] += 1
            post_signal_addresses.add(str(address))
            entry_mode = (tier_seen.get('tier') if is_learner() else oct4.ENTRY_MODE if is_adaptive()
                          else order_flow.entry_mode(coin, flow, context, EFFECTIVE_ENTRY_THRESHOLDS))
            if not entry_mode:
                reject(report, ['gold_signal'], coin)
                continue
            # Start independent price and rug checks together. Both helpers are
            # cached/asynchronous; running them concurrently avoids serial provider
            # latency without weakening known-risk vetoes.
            validation=price_integrity.check(coin)
            safety=rug_guard.check(coin)
            training_bridge.observe(coin,flow,safety=safety,validation=validation,context=context,now=now_ms())
            price_review=(
                validation.get('status')=='review'
                and validation.get('reason') in {
                    'price_source_disagreement_needs_jupiter',
                    'price_unavailable_needs_jupiter',
                    'price_crosscheck_pending_needs_jupiter',
                }
            )
            if validation.get('status')!='pass' and not price_review:
                reject(report,[validation.get('reason') or 'price_unavailable'],coin,validation)
                continue
            if safety.get('status') != 'pass' or safety.get('provisional_early'):
                reject(report, safety.get('reasons') or ['risk_check_pending'], coin)
                continue
            report['safety_passed'] += 1
            if report['quoted'] >= MAX_QUOTED_CANDIDATES:
                reject(report, ['quote_budget'], coin)
                continue
            strategy_id = SIGNAL_STRATEGY
            learn_features = None
            if is_learner():
                # Size follows what similar closed trades actually returned; a context
                # that keeps losing is skipped until that evidence has aged out.
                learn_features = learner.features(coin, flow, context, entry_mode)
                verdict = learner.assess(learned, learn_features)
                if verdict['avoid']:
                    reject(report, ['learned_avoid'], coin, {'bucket': verdict['avoid'], 'edge_pct': verdict['edge_pct']})
                    continue
                learning = {'sample': verdict['sample'], 'wins': 0, 'win_rate': 0.0,
                            'avg_pnl_pct': verdict['edge_pct'], 'profit_factor': None,
                            'recent_losses': learned['loss_streak'], 'size_multiplier': verdict['size_multiplier'],
                            'bonus': round((verdict['size_multiplier'] - 1.0) * 100, 1),
                            'evidence': verdict['evidence'], 'loss_streak_brake': verdict['loss_streak_brake']}
            elif is_adaptive():
                # Fixed historical size: no journal-driven resizing in this strategy.
                learning = {'sample': 0, 'wins': 0, 'win_rate': 0.0, 'avg_pnl_pct': 0.0, 'profit_factor': None,
                            'recent_losses': 0, 'size_multiplier': 1.0, 'bonus': 0.0}
            else:
                # Learn from mistakes without killing trade frequency: the main journal
                # adapts only scout size. Threshold/exit experiments remain isolated.
                learning = adaptive_scout_profile(entry_mode)
            recovery = False
            price = num(coin.get('priceUsd'))
            if price <= 0:
                continue
            positive = [s['title'] for s in coin.get('signals', []) if s.get('kind') == 'positive'][:4]
            risks = [s['title'] for s in coin.get('signals', []) if s.get('kind') == 'risk'][:4]
            exposure_available = max(0,STATE.equity_usd()*MAX_TOTAL_EXPOSURE_PCT/100-STATE.reserved_usd())
            available_before = min(STATE.available_balance_usd(),exposure_available)
            sol_usd=num(safety.get('metrics',{}).get('sol_usd')) or sol_usd_from_coin(coin) or sol_usd_market_price()
            if sol_usd<=0:
                reject(report,['network_price_unknown'],coin); continue
            pre_network_fee = max(.03, NETWORK_FEE_SOL * sol_usd)
            seen_before=any(t.get('address')==address for t in STATE.history)
            rent_lamports=num(safety.get('metrics',{}).get('token_account_rent_lamports'))
            if not seen_before and rent_lamports<=0:
                reject(report,['risk_data_unavailable'],coin); continue
            entry_rent=0.0 if seen_before else rent_lamports/1e9*sol_usd
            # Fit cash, full-loss exposure and the configured daily allowance.
            fixed_cost_budget=2*pre_network_fee+entry_rent
            # Reserve each open position's planned stop budget separately from
            # the full-loss exposure used by the capital and position limits.
            open_planned_risk=sum(num(p.get('planned_risk_usd')) for p in STATE.positions)
            if is_learner():
                sizing = learner.SIZING
                base_notional = max(sizing['min_notional_usd'], min(
                    sizing['max_notional_usd'], learner.base_notional(coin, entry_mode) * learning['size_multiplier']))
            else:
                base_notional = configured_notional if STRATEGY_PROFILE == OCT4_FIXED_PROFILE else (TRADE_NOTIONAL_USD if is_adaptive() else early_requested_notional(coin, learning))
            requested_notional = (base_notional if STRATEGY_PROFILE == OCT4_FIXED_PROFILE
                                  else min(base_notional,MAX_POSITION_RISK_USD-fixed_cost_budget))
            # With budget sizing off the daily limit stays a hard gate above
            # and never shrinks the position.
            sizing_day_limit = MAX_DAILY_LOSS_USD if DAILY_BUDGET_SIZING else 0.0
            notional = runtime.plan_notional(
                requested_notional,available_before,sizing_day_limit,
                STATE.risk_day_pnl()-open_planned_risk,
                configured_stop,STOP_EXECUTION_BUFFER_PCT,fixed_cost_budget,
            )
            if STRATEGY_PROFILE == OCT4_FIXED_PROFILE and notional + 1e-9 < configured_notional:
                # User-fixed sizing never silently shrinks the requested trade.
                # If the selected amount plus costs does not fit, skip it.
                reject(report,['balance'],coin); return
            if notional < 10:
                reject(report,['risk_budget_unavailable' if DAILY_BUDGET_SIZING else 'balance'],coin); return
            if STRATEGY_PROFILE == OCT4_FIXED_PROFILE:
                notional = configured_notional
            report['quoted'] += 1
            dex_id = str(coin.get('dexId') or '').lower()
            if dex_id == 'pumpswap':
                prepared = pumpswap_stop.prepare_entry(
                    coin, notional, sol_usd,
                    buffer_bps=paper_quotes.BUFFER_BPS,
                    slippage_bps=paper_quotes.SLIPPAGE_BPS,
                )
            else:
                prepared = paper_quotes.prepare_entry(
                    address, str(coin.get('pairAddress') or ''), notional
                )
            if not prepared:
                reject(report,['quote_inconsistent'],coin)
                continue
            report['quote_returned'] += 1
            live_quote,initial_exit=prepared
            entry_network_fee=pre_network_fee
            expected_token_raw=int(live_quote['token_raw_amount'])
            immediate_exit_net = max(0.0, num(initial_exit.get('expected_usdc')) - entry_network_fee)
            worst_case_exit_net = max(0.0, num(initial_exit.get('floor_usdc')) - entry_network_fee)
            immediate_roundtrip_pct = (
                (immediate_exit_net - notional - entry_network_fee - entry_rent) / max(notional, 1e-18)
            ) * 100.0
            worst_case_roundtrip_pct = (
                (worst_case_exit_net - notional - entry_network_fee - entry_rent) / max(notional, 1e-18)
            ) * 100.0
            impact_pct = num(live_quote.get('price_impact_pct'))
            quote_rejected = entry_policy.quote_rejections(
                live_quote, immediate_roundtrip_pct, worst_case_roundtrip_pct,
                max_impact=STRICT_MAX_ENTRY_IMPACT_PCT,
                max_cost=STRICT_MAX_ROUNDTRIP_COST_PCT,
                max_conservative_cost=STRICT_MAX_WORST_CASE_COST_PCT, now=now_ms(),
            )
            if quote_rejected:
                reject(report, quote_rejected, coin, {
                    'expected_roundtrip_pct': round(immediate_roundtrip_pct, 4),
                    'conservative_roundtrip_pct': round(worst_case_roundtrip_pct, 4),
                    'entry_impact_pct': round(impact_pct, 4),
                })
                continue
            report['quote_passed'] += 1
            stop_signal_trigger_pct = None
            quote_slippage_pct = paper_quotes.BUFFER_BPS / 100.0
            quote_network_fee = entry_network_fee
            decimals=int(safety['metrics']['decimals'])
            quantity=expected_token_raw/(10**decimals)
            quote_fill_price=notional/max(quantity,1e-18)
            if price_review:
                validation=price_integrity.jupiter_tiebreak(validation,quote_fill_price)
                if validation.get('status')!='pass':
                    reject(
                        report,[validation.get('reason') or 'price_tiebreak_failed'],coin,validation
                    )
                    continue
            entry_quote = {
                'fill_price': quote_fill_price,
                'quantity': quantity,
                'capital_committed_usd': notional + quote_network_fee + entry_rent,
                'dex_fee_bps': 0.0,
                'dex_fee_usd': 0.0,
                'network_fee_usd': quote_network_fee,
                'impact_pct': impact_pct,
                'slippage_pct': quote_slippage_pct,
                'latency_pct': 0.0,
            }
            if quantity <= 0:
                continue
            training_bridge.observe(coin,flow,safety=safety,validation=validation,
                quotes={'entry':dict(live_quote,entry_network_fee_usd=entry_network_fee,
                                     entry_account_reserve_usd=entry_rent),
                        'exit':dict(initial_exit,exit_network_fee_usd=entry_network_fee)},
                context=context,now=now_ms())
            with STATE.lock:
                if STATE.demo_session_id!=session_at_check or not STATE.running or slots_in_use(STATE.positions)>=MAX_POSITIONS:
                    return
                current_coin = next((c for c in STATE.feed if c.get('address') == address and c.get('pairAddress') == coin.get('pairAddress')), coin)
                final_flow = STATE.live_flow(address, flow_seconds, str(coin.get('pairAddress') or ''))
                final_context = self.market_context(current_coin)
                final_rejections = signal_check(current_coin,final_flow,final_context)
                if final_rejections:
                    reject(report,final_rejections,coin)
                    continue
                if MAX_DAILY_LOSS_USD > 0 and STATE.risk_day_pnl()<=-MAX_DAILY_LOSS_USD:
                    reject(report,['daily_limit'],coin); return
                if STATE.available_balance_usd()<entry_quote['capital_committed_usd']:
                    reject(report,['balance'],coin); return
                live_open_risk=sum(num(p.get('planned_risk_usd')) for p in STATE.positions)
                current_exposure_available=max(0,STATE.equity_usd()*MAX_TOTAL_EXPOSURE_PCT/100-STATE.reserved_usd())
                permitted = runtime.plan_notional(
                    requested_notional,min(STATE.available_balance_usd(),current_exposure_available),sizing_day_limit,
                    STATE.risk_day_pnl()-live_open_risk,
                    configured_stop,STOP_EXECUTION_BUFFER_PCT,fixed_cost_budget,
                )
                if STRATEGY_PROFILE == OCT4_FIXED_PROFILE and permitted + 1e-9 < configured_notional:
                    reject(report,['balance'],coin); return
                if notional>permitted:
                    reject(report,['risk_budget_unavailable'],coin); return
                if not 0 <= now_ms()-int(live_quote['quoted_at']) <= entry_policy.MAX_ENTRY_QUOTE_AGE_MS:
                    reject(report,['quote_age'],coin); return
                if not paper_quotes.signal_fresh_at_commit(num(current_coin.get('updatedAt')),live_quote,now=now_ms()):
                    reject(report,['stale_signal'],coin)
                    continue
                next_trade_no = STATE.trade_seq + 1
                # ORDER_FLOW_ADAPTIVE records the context re-checked at commit.
                entry_context = final_context if is_adaptive() else context
                position = {
                    'id': f'{STATE.demo_session_id}:{address}:{next_trade_no}', 'address': address,
                    'pairAddress': coin.get('pairAddress'), 'name': coin.get('name'),
                    'symbol': coin.get('symbol'), 'imageUrl': coin.get('imageUrl'),
                    'entry_price': price, 'market_entry_price': price,
                    'execution_entry_price': round(entry_quote['fill_price'], 12),
                    'current_price': price, 'peak_price': price,
                    'trade_no': next_trade_no, 'session_id': STATE.demo_session_id, 'strategy_id': strategy_id,
                    'entry_mode': entry_mode,
                    'learn_features': learn_features, 'learning_evidence': learning.get('evidence'),
                    'learning_loss_streak_brake': learning.get('loss_streak_brake'),
                    'provisional_early_safety': bool(safety.get('provisional_early')),
                    'strategy_profile': STRATEGY_PROFILE, 'strategy_version': STRATEGY_VERSION,
                    'engine_settings_version': trade_controls.get('version'),
                    'learning_mode': LEARNING_MODE, 'entry_flow': final_flow,
                    'entry_flow_window_seconds': flow_seconds, 'stop_loss_pct': configured_stop,
                    'entry_context': entry_context, 'entry_conviction': entry_context.get('conviction'),
                    'entry_hold_mode': entry_context.get('mode'), 'learning_sample': learning['sample'],
                    'learning_win_rate': learning['win_rate'], 'learning_profit_factor': learning['profit_factor'],
                    'learning_recent_losses': learning['recent_losses'], 'learning_bonus': learning['bonus'],
                    'learning_avg_pnl_pct': learning.get('avg_pnl_pct'),
                    'learning_size_multiplier': learning.get('size_multiplier'),
                    'requested_notional_usd': round(requested_notional, 8),
                    'notional_usd': round(notional, 8),
                    'size_limited_by_daily_budget': notional<min(configured_notional if STRATEGY_PROFILE == OCT4_FIXED_PROFILE else TRADE_NOTIONAL_USD,available_before-fixed_cost_budget),
                    'planned_risk_usd': notional*(configured_stop+STOP_EXECUTION_BUFFER_PCT)/100+fixed_cost_budget,
                    'conservative_risk_usd': notional+quote_network_fee+entry_rent,
                    'original_notional_usd': notional,
                    'capital_committed_usd': round(entry_quote['capital_committed_usd'], 8),
                    'quantity': quantity, 'score': coin.get('score'),
                    'current_score': coin.get('score'), 'opened_at': now_ms(),
                    'signal_observed_at': num(current_coin.get('updatedAt')),
                    'signal_age_at_entry_ms': now_ms()-num(current_coin.get('updatedAt')),
                    'simulated_fill_at': live_quote.get('simulated_fill_at',live_quote.get('quoted_at')),
                    'execution_queue_ms': live_quote.get('queue_ms'), 'execution_http_ms': live_quote.get('http_ms'),
                    'updated_at': now_ms(), 'signal_pnl_pct': 0,
                    'pnl_pct': immediate_roundtrip_pct, 'pnl_usd': immediate_roundtrip_pct*notional/100,
                    'execution_mode': live_quote.get('execution_source') or 'JUPITER_QUOTE_V2',
                    'jupiter_usdc_in_raw': int(live_quote.get('input_usdc_raw') or 0),
                    'jupiter_token_raw_expected': int(live_quote.get('token_raw_expected') or 0),
                    'jupiter_token_raw_amount': expected_token_raw,
                    'jupiter_entry_route': live_quote.get('route') or [],
                    'jupiter_entry_quote_at': int(live_quote.get('quoted_at') or now_ms()),
                    'jupiter_entry_price_impact_pct': impact_pct,
                    'jupiter_slippage_bps': int(live_quote.get('slippage_bps') or paper_quotes.SLIPPAGE_BPS),
                    'entry_roundtrip_pnl_pct': round(immediate_roundtrip_pct, 4),
                    'entry_policy_version': ENTRY_POLICY_VERSION,
                    'exit_policy': EXIT_POLICY, 'exit_policy_version': EXIT_POLICY_VERSION,
                    'effective_config_hash': effective_config_hash(),
                    'effective_entry_thresholds': effective_entry_thresholds(),
                    'signal_source_commit': SIGNAL_SOURCE,
                    'execution_verification_version': 'QUOTE_EVIDENCE_V9',
                    'entry_quote': live_quote.get('raw_quote'),
                    'preflight_buy_quote': live_quote.get('preflight_buy_quote'),
                    'preflight_sell_quote': live_quote.get('preflight_sell_quote'),
                    'price_crosscheck': validation,
                    'preflight_is_cost_estimate_not_same_time_fill': True,
                    'entry_worst_case_roundtrip_pnl_pct': round(worst_case_roundtrip_pct, 4),
                    'stop_signal_trigger_pct': None,
                    'hard_stop_net_pct': None, 'planned_stop_net_pct': -configured_stop,
                    'entry_dex_fee_bps': round(entry_quote['dex_fee_bps'], 4),
                    'entry_dex_fee_usd': round(entry_quote['dex_fee_usd'], 8),
                    'entry_network_fee_usd': round(entry_quote['network_fee_usd'], 8),
                    'entry_account_reserve_usd': entry_rent, 'token_decimals': decimals,
                    'risk_check': safety,
                    # The recorded exit policy is authoritative for this position.
                    'take_profit_net_pct': configured_tp if EXIT_POLICY in ('fixed', 'fixed_targets') else entry_context.get('target_pct'),
                    'cost_assumptions': 'Jupiter AMM fees included; 10bps/leg buffer, network budget, account rent reserve',
                    'entry_price_impact_pct': round(entry_quote['impact_pct'], 6),
                    'entry_slippage_pct': round(entry_quote['slippage_pct'] + entry_quote['latency_pct'], 6),
                    'why_entry': positive, 'risks_at_entry': risks,
                    'balance_at_entry': round(STATE.demo_balance_usd, 8),
                    'available_before_entry': round(available_before, 8),
                    'available_after_entry': round(max(0.0, available_before - entry_quote['capital_committed_usd']), 8),
                    'entry_liquidity_usd': coin.get('liquidityUsd'),
                    'entry_volume_h1': (coin.get('volume') or {}).get('h1'),
                    'entry_market_cap': coin.get('marketCap') or coin.get('fdv'),
                    'entry_change_m5': (coin.get('priceChange') or {}).get('m5'),
                    'entry_buy_sell_ratio': round(buy_sell_ratio, 4),
                    'entry_liquidity_mc_ratio': round(liquidity_mc_ratio, 4),
                    'entry_scan_count': STATE.scan_count, 'dex_url': coin.get('dexUrl'),
                    'coin_snapshot': coin,
                }
                STATE.commit('ENTRY', position, positions=STATE.positions+[position], trade_seq=next_trade_no)
                report['opened'] += 1
                open_addresses.add(address)
                STATE.event(
                    f"PAPER ENTRY #{position['trade_no']} ${coin.get('symbol')} market ${price:.10g} "
                    f"→ fill ${entry_quote['fill_price']:.10g} · ${notional:.2f} · fee {entry_quote['dex_fee_bps'] / 100:.3f}% "
                    f"· impact {entry_quote['impact_pct']:.2f}% · {strategy_id} · NEO {coin.get('score'):.0f}/100"
                )

    def scan_once(self) -> None:
        if not self.scan_lock.acquire(blocking=False):
            return
        try:
            self.ensure_universe_seeded()
            addresses, metadata = self.discovery.get()
            early_pairs = gecko_new_pumpswap_pairs()
            for early_pair in early_pairs:
                address = str((early_pair.get('baseToken') or {}).get('address') or '')
                if not address:
                    continue
                if address not in addresses:
                    addresses.append(address)
                info = metadata.setdefault(address, {
                    'sources': [], 'icon': '', 'header': '', 'description': '',
                    'links': [], 'boost_amount': 0,
                })
                if 'gecko-new-pools' not in info['sources']:
                    info['sources'].append('gecko-new-pools')
            # Persist every genuinely observed token and rotate the larger universe.
            # Only addresses that are NEW since the previous discovery snapshot are
            # priority. Treating the whole provider snapshot as fresh on every scan
            # can starve the rotation when discovery itself is close to batch size.
            # Brand-new Gecko pools are always urgent and remain at the front.
            early_addresses = [
                str((pair.get('baseToken') or {}).get('address') or '')
                for pair in early_pairs
            ]
            discovered_snapshot = list(dict.fromkeys(addresses))
            priority_addresses = discovery_universe.fresh_priority(
                discovered_snapshot, self.last_discovery_snapshot, early_addresses
            )
            self.last_discovery_snapshot = set(discovered_snapshot)
            newly_added = self.universe.observe(discovered_snapshot, metadata)
            self.new_universe_since_start += newly_added
            addresses, universe_meta = self.universe.next_batch(
                priority=priority_addresses, limit=DISCOVERY_SCAN_BATCH
            )
            # Keep the small set of pools currently being verified by live_tape in
            # every market scan. Otherwise the 240-address rotation can move on
            # before the recorder completes its transaction bodies, leaving every
            # evaluated candidate with zero verified flow despite healthy tape data.
            tape_pins = active_tape_pins(read_live_tape())
            if tape_pins:
                addresses = list(dict.fromkeys([*tape_pins, *addresses]))[:DISCOVERY_SCAN_BATCH]
                for address in tape_pins:
                    info = metadata.setdefault(address, {
                        'sources': [], 'icon': '', 'header': '', 'description': '',
                        'links': [], 'boost_amount': 0,
                    })
                    sources = list(info.get('sources') or [])
                    if 'live-tape-pinned' not in sources:
                        sources.append('live-tape-pinned')
                    info['sources'] = sources
            universe_stats = self.universe.stats()
            priority_selected = len(set(addresses) & set(priority_addresses))
            discovery_stats = {
                **universe_stats,
                'provider_snapshot_size': len(discovered_snapshot),
                'selected_last_scan': len(addresses),
                'priority_last_scan': priority_selected,
                'rotation_last_scan': max(0, len(addresses) - priority_selected),
                'new_universe_last_scan': newly_added,
                'new_universe_since_start': self.new_universe_since_start,
                'tape_pinned_last_scan': len(tape_pins),
            }
            for address, info in universe_meta.items():
                if address not in metadata:
                    metadata[address] = info
                else:
                    old_sources = list(metadata[address].get('sources') or [])
                    for source in info.get('sources') or []:
                        if source not in old_sources:
                            old_sources.append(source)
                    metadata[address]['sources'] = old_sources
            for position in STATE.positions:
                address = position.get('address')
                if address and address not in addresses:
                    addresses.append(address)
                    metadata[address] = metadata.get(address) or {
                        'sources': ['open-position'], 'icon': position.get('imageUrl') or '',
                        'header': '', 'description': '', 'links': [], 'boost_amount': 0,
                    }
            if not addresses:
                with STATE.lock:
                    STATE.status = 'discovering'
                    STATE.message = 'Проверява пазарните източници.'
                return
            scan_sequence = STATE.scan_count + 1
            progress_done = 0
            discovery_stats.update({
                'scan_sequence': scan_sequence,
                'refresh_completed': 0,
                'refresh_total': len(addresses),
                'scanned_address_slots_since_start': self.scanned_address_slots_since_start,
            })
            with STATE.lock:
                STATE.discovery_stats = dict(discovery_stats)

            def market_refresh_progress(done: int, total: int) -> None:
                nonlocal progress_done
                delta = max(0, int(done) - progress_done)
                progress_done = int(done)
                self.scanned_address_slots_since_start += delta
                with STATE.lock:
                    STATE.scanned_address_slots_total = self.scanned_address_slots_since_start
                    live_stats = dict(STATE.discovery_stats or discovery_stats)
                    if int(live_stats.get('scan_sequence') or 0) != scan_sequence:
                        return
                    live_stats.update({
                        'refresh_completed': int(done),
                        'refresh_total': int(total),
                        'scanned_address_slots_since_start': self.scanned_address_slots_since_start,
                    })
                    STATE.discovery_stats = live_stats

            dex_pairs = fetch_pairs(addresses, progress=market_refresh_progress)
            discovery_stats.update({
                'scan_sequence': scan_sequence,
                'refresh_completed': progress_done,
                'refresh_total': len(addresses),
                'scanned_address_slots_since_start': self.scanned_address_slots_since_start,
            })
            pairs = list(early_pairs) + dex_pairs
            chosen = best_pairs(pairs)
            prefer_exact_tape_pairs(chosen, pairs, tape_pins)

            # For the first 15 minutes, preserve the newly-created exact PumpSwap
            # pool instead of silently switching to an older/higher-liquidity pair.
            newest_early: dict[str, dict[str, Any]] = {}
            for early_pair in early_pairs:
                address = str((early_pair.get('baseToken') or {}).get('address') or '')
                created = int(early_pair.get('pairCreatedAt') or 0)
                if not address or not created or now_ms() - created > 15 * 60 * 1000:
                    continue
                previous = newest_early.get(address)
                if previous is None or created > int(previous.get('pairCreatedAt') or 0):
                    newest_early[address] = early_pair
            chosen.update(newest_early)

            feed = []
            for address in addresses:
                pair = chosen.get(address)
                if not pair:
                    continue
                coin = make_coin(address, pair, metadata.get(address, {}))
                if coin['priceUsd'] > 0:
                    feed.append(coin)
            feed.sort(key=lambda c: (num(c.get('score')), num((c.get('volume') or {}).get('h1'))), reverse=True)
            feed = feed[:MAX_FEED]
            by_address = {c['address']: c for c in feed}
            for position in STATE.positions:
                address = position.get('address')
                if not address:
                    continue
                pair = exact_position_pair(position, pairs)
                if not pair:
                    by_address.pop(address, None)
                    continue
                snap = position.get('coin_snapshot') or {}
                meta = {
                    'sources': ['open-position-pinned-pair'],
                    'icon': snap.get('imageUrl') or '',
                    'header': '',
                    'description': '',
                    'links': [],
                    'boost_amount': 0,
                }
                by_address[address] = make_coin(address, pair, meta)
            with STATE.lock:
                for position in STATE.positions:
                    held = by_address.get(position.get('address'))
                    if held and held.get('pairAddress') == position.get('pairAddress'):
                        STATE.position_market[f"{position.get('address')}:{position.get('pairAddress')}"] = dict(held)
                STATE.feed = feed
                STATE.last_scan_at = now_ms()
                STATE.scan_count += 1
                STATE.status = 'monitoring'
                STATE.source_status = {'dexscreener': 'online'}
                STATE.discovery_stats = discovery_stats
                self.update_price_history(feed)
                setups = sum(1 for c in feed if c.get('posture') == 'SETUP')
                STATE.message = STATE.entry_diagnostics.get('message') or f'Проверени {len(feed)} token-а; отворени позиции: {len(STATE.positions)}.'
                STATE.save()
            # Prewarm provider checks before the short EARLY flow window fires.
            if training_bridge.accepting():
                for coin in feed:
                    training_bridge.observe(coin,STATE.live_flow(coin['address'],pair_address=coin.get('pairAddress')),
                        context=self.market_context(coin,{}),now=now_ms())
            self.prewarm_entry_checks(feed)
            # Entry preparation and quotes must not hold the account/UI lock.
            self.maybe_open(feed)
        except Exception as exc:
            with STATE.lock:
                STATE.status = 'error'
                STATE.source_status = {'dexscreener': 'error'}
                STATE.event(f'Market monitor error: {exc}')
                STATE.save()
        finally:
            self.scan_lock.release()

    def run(self) -> None:
        with STATE.lock:
            STATE.status = 'starting'
            STATE.event('NEO public market monitor started.')
        while not self.stop_event.is_set():
            started = time.monotonic()
            self.scan_once()
            # SCAN_SECONDS is a target cadence, not an extra sleep added after
            # network work. If a scan itself takes longer, start the next one
            # immediately instead of compounding the delay.
            remaining = max(0.0, SCAN_SECONDS - (time.monotonic() - started))
            self.stop_event.wait(remaining)

    def stop(self) -> None:
        self.stop_event.set()
        self.discovery.stop()


MONITOR = Monitor()

class ApiHandler(BaseHTTPRequestHandler):
    server_version = 'NEOMarketMonitor/2.0'

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            self.send_json({'ok': True, 'status': STATE.status, 'running': STATE.running, 'feed_count': len(STATE.feed)})
            return
        if parsed.path == '/state':
            self.send_json(STATE.snapshot())
            return
        if parsed.path == '/settings':
            self.send_json({'settings': STATE.engine_settings, 'enabled': STRATEGY_PROFILE == OCT4_FIXED_PROFILE,
                            'limits': USER_ENGINE_SETTINGS_LIMITS})
            return
        if parsed.path == '/token':
            address = (parse_qs(parsed.query).get('address') or [''])[0]
            result = STATE.token_snapshot(address)
            self.send_json(result if result else {'error': 'token_not_found'}, 200 if result else 404)
            return
        if parsed.path == '/lab-book':
            self.send_json(read_lab_book((parse_qs(parsed.query).get('id') or [''])[0]))
            return
        if parsed.path == '/hype-radar':
            self.send_json(read_hype_radar())
            return
        if parsed.path == '/coin-wallets':
            query = parse_qs(parsed.query)
            self.send_json(read_coin_wallets((query.get('address') or [''])[0], (query.get('pair') or [''])[0]))
            return
        if parsed.path == '/coin-flow':
            query = parse_qs(parsed.query)
            self.send_json(read_coin_flow((query.get('address') or [''])[0], (query.get('pair') or [''])[0]))
            return
        self.send_json({'error': 'not_found'}, 404)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return {}
        if length > 16_384:
            raise ValueError('request body too large')
        payload = json.loads(self.rfile.read(length).decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('request body must be an object')
        return payload

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == '/control/settings':
            if STRATEGY_PROFILE != OCT4_FIXED_PROFILE:
                self.send_json({'error': 'settings_not_enabled_for_profile'}, 409)
                return
            try:
                payload = self.read_json_body()
                with STATE.lock:
                    updated = normalize_engine_settings(payload, STATE.engine_settings)
                    updated['updated_at'] = now_ms()
                    STATE.engine_settings = updated
                    STATE.event(f"PAPER controls updated: ${updated['trade_notional_usd']:.2f} / SL {updated['stop_loss_pct']:.2f}% / TP {updated['take_profit_pct']:.2f}%")
                    STATE.save()
                self.send_json(STATE.snapshot())
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({'error': 'invalid_settings', 'message': str(exc)}, 400)
            return
        if path == '/control/start':
            with STATE.lock:
                STATE.running = True
                STATE.status = 'starting'
                STATE.event('Monitoring enabled.')
                STATE.save()
            threading.Thread(target=MONITOR.scan_once, daemon=True).start()
            self.send_json(STATE.snapshot())
            return
        if path == '/control/stop':
            with STATE.lock:
                STATE.running = False
                STATE.status = 'paused'
                STATE.event('Monitoring paused.')
                STATE.save()
            self.send_json(STATE.snapshot())
            return
        if path == '/control/rescan':
            threading.Thread(target=MONITOR.scan_once, daemon=True).start()
            self.send_json({'ok': True})
            return
        if path == '/control/reset':
            try:
                with MONITOR.position_lock, MONITOR.entry_lock:
                    STATE.reset_with_archive()
                    training_bridge.request_reset()
                self.send_json(STATE.snapshot())
            except Exception as exc:
                self.send_json({'error': 'reset_archive_or_persist_failed', 'type': type(exc).__name__}, 500)
            return
        self.send_json({'error': 'not_found'}, 404)


def main() -> None:
    mode = os.getenv('NEO_ENGINE_MODE', 'PAPER').upper()
    if mode not in {'PAPER', 'REPLAY', 'SHADOW'}:
        raise ValueError('Only PAPER/REPLAY/SHADOW modes are supported')
    STATE.load()
    MONITOR.scanned_address_slots_since_start = max(
        MONITOR.scanned_address_slots_since_start, STATE.scanned_address_slots_total
    )
    STATE.save()
    training_bridge.start(STATE_PATH.parent / 'training')
    thread = threading.Thread(target=MONITOR.run, name='neo-market-monitor', daemon=True)
    thread.start()
    guard = threading.Thread(target=MONITOR.run_position_guard, name='neo-position-guard', daemon=True)
    guard.start()
    server = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    print(f'NEO market monitor API listening on http://{HOST}:{PORT}', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        MONITOR.stop()
        training_bridge.stop()
        server.shutdown()


if __name__ == '__main__':
    main()

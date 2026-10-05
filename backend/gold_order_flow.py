"""High-frequency EARLY Order Flow entry predicate for PAPER trading.

User-directed mode: enter earlier and collect many more samples. Rug/price/
execution vetoes remain outside this module and are still mandatory.
"""
SOURCE_COMMIT='EARLY_ORDER_FLOW_2026_10_05'
CORE_AST_SHA256='USER_UNLOCKED_FOR_EARLY_ORDER_FLOW'

def _n(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def entry_mode(coin, flow, context):
    score=_n(coin.get('score'))
    liquidity=_n(coin.get('liquidityUsd'))
    change_m5=_n((coin.get('priceChange') or {}).get('m5'))
    age=_n(coin.get('ageMinutes'),999999)
    conviction=_n(context.get('conviction'))
    trades=_n(flow.get('trades'))
    ratio=_n(flow.get('buy_sell_usd_ratio'))
    buy_usd=_n(flow.get('buy_usd'))
    sell_usd=_n(flow.get('sell_usd'))
    unique_wallets=_n(flow.get('unique_wallets'))
    max_sell=_n(flow.get('max_sell_usd'))

    if liquidity < 4000 or unique_wallets < 1:
        return None
    if max_sell >= max(900.0, buy_usd * 1.25):
        return None

    # First real buying impulse. Small scouts are sized later by liquidity and
    # learned performance, so this gate can deliberately fire early.
    ultra_early=(
        age <= 45 and score >= 58 and -8 <= change_m5 <= 25
        and trades >= 1 and ratio >= 1.05 and buy_usd >= 8
        and buy_usd >= sell_usd * 1.02 and conviction >= 30
    )
    if ultra_early:
        return 'ULTRA_EARLY'

    early=(
        age <= 180 and score >= 70 and liquidity >= 5000
        and -8 <= change_m5 <= 30 and trades >= 2 and ratio >= 1.10
        and buy_usd >= 15 and buy_usd >= sell_usd * 1.05
        and conviction >= 45
    )
    if early:
        return 'EARLY'

    # Still allow a strong flow setup if discovery saw it later.
    momentum=(
        score >= 78 and liquidity >= 7500 and -5 <= change_m5 <= 30
        and trades >= 3 and ratio >= 1.20 and buy_usd >= 25
        and conviction >= 55
    )
    if momentum:
        return 'MOMENTUM'

    return None

def qualifies(coin,flow,context):
    try:
        return entry_mode(coin,flow,context) is not None
    except (KeyError,TypeError,ValueError):
        return False

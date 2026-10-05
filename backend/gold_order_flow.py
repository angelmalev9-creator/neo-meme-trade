"""Original Order Flow entry predicate from the immutable user-approved GOLD tag.
Safety/execution vetoes and the user's later net 3/10 exit overlay are separate.
No legacy stop clamping or automatic change of pool is restored.
"""
SOURCE_COMMIT='44a7a09b019f068a97c2165068a556cadcc6bfc4'
CORE_AST_SHA256='e5ceccbfdf5707868f9b9ec81b24745b280acdcac9a6e20189db620223a47ccc'

def qualifies(coin,flow,context):
    score=float(coin.get('score') or 0)
    liquidity=float(coin.get('liquidityUsd') or 0)
    change_m5=float((coin.get('priceChange') or {}).get('m5') or 0)
    try:
        core=score >= 85 and liquidity >= 15000 and (-5 <= change_m5 <= 25) and (flow['trades'] >= 3) and (flow['buy_sell_usd_ratio'] >= 1.3) and (flow['unique_wallets'] >= 1) and (flow['max_sell_usd'] < max(750.0, flow['buy_usd'] * 0.8))
        return bool(core and float(context.get('conviction') or 0)>=75)
    except (KeyError,TypeError,ValueError): return False

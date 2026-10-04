"""Main-engine-only quote accounting. No transaction building, signing or sending.
Preserves shared quote client's API throttling. Rejects stale/malformed replies.
OutAmount already includes AMM fees; explicit 10bps/leg execution cost is an
assumption, not an observed fill. Minimum slippage output is a separate bound.
"""
import math
import threading
import time
from decimal import Decimal
import paper_execution_quotes as provider

SLIPPAGE_BPS=provider.SLIPPAGE_BPS
USDC=provider.USDC_MINT
MAX_AGE_MS=8_000
MARK_TTL_MS=1_500
BUFFER_BPS=10
_CACHE={}
_LOCK=threading.Lock()

def stamp(): return int(time.time()*1000)
def _int(v):
    if isinstance(v,bool): raise ValueError('boolean amount')
    if isinstance(v,int): return v
    if not isinstance(v,str) or not v.isdigit(): raise ValueError('integer amount required')
    return int(v)

def valid(data,input_mint,output_mint,amount,start):
    try:
        if not data or data.get('inputMint')!=input_mint or data.get('outputMint')!=output_mint: return False
        if _int(data.get('inAmount'))!=int(amount) or _int(data.get('outAmount'))<=0: return False
        floor=_int(data.get('otherAmountThreshold'))
        if not 0<floor<=_int(data['outAmount']): return False
        if data.get('swapMode')!='ExactIn' or not data.get('routePlan'): return False
        if not 0<=stamp()-start<=MAX_AGE_MS: return False
        if not math.isfinite(float(data['priceImpactPct'])): return False
        return True
    except (ValueError,TypeError,KeyError): return False

def same_token_pool(data,mint,pair):
    legs=[leg.get('swapInfo',{}) for leg in data.get('routePlan',[]) if mint in
          (leg.get('swapInfo',{}).get('inputMint'),leg.get('swapInfo',{}).get('outputMint'))]
    return bool(pair) and bool(legs) and all(x.get('ammKey')==pair for x in legs)

def _request(a,b,amount,pair=None,token=None):
    began=stamp(); q=provider.quote(a,b,int(amount))
    if not valid(q,a,b,amount,began): return None
    if pair and not same_token_pool(q,token,pair): return None
    q['_observed_at']=began
    return q

def entry_quote(token_mint,pair_address,notional_usd):
    raw=int(Decimal(str(notional_usd))*1_000_000)
    d=_request(USDC,token_mint,raw,pair_address,token_mint)
    if not d: return None
    expected=_int(d['outAmount']); assumed=expected*(10000-BUFFER_BPS)//10000
    return {'input_usdc_raw':raw,'token_raw_expected':expected,'token_raw_amount':assumed,
            'token_raw_floor':_int(d['otherAmountThreshold']),
            'price_impact_pct':float(d['priceImpactPct'])*100,
            'slippage_bps':int(d.get('slippageBps',SLIPPAGE_BPS)),
            'route':provider._compact_route(d),'quoted_at':d['_observed_at'],
            'context_slot':d.get('contextSlot'),'assumed_buffer_bps':BUFFER_BPS}

def exit_quote(token_mint,token_raw_amount,pair_address=None):
    raw=int(token_raw_amount)
    d=_request(token_mint,USDC,raw,pair_address,token_mint)
    if not d: return None
    expected=_int(d['outAmount'])/1_000_000
    assumed=expected*(1-BUFFER_BPS/10000)
    return {'expected_usdc':assumed,'provider_expected_usdc':expected,
            'floor_usdc':_int(d['otherAmountThreshold'])/1_000_000,
            'price_impact_pct':float(d['priceImpactPct'])*100,
            'slippage_bps':int(d.get('slippageBps',SLIPPAGE_BPS)),
            'route':provider._compact_route(d),'quoted_at':d['_observed_at'],
            'context_slot':d.get('contextSlot'),'token_input_raw':raw,
            'assumed_buffer_bps':BUFFER_BPS}

def position_mark(position,coin,network_fee_usd,force=False):
    mint=position.get('address'); pair=position.get('pairAddress')
    # Use the actually recorded paper amount, never silently give old positions
    # extra tokens by replacing a floor/assumed amount with expected output.
    raw=int(position.get('jupiter_token_raw_amount') or 0)
    if not raw: return None
    key=(mint,pair,raw)
    with _LOCK: cached=_CACHE.get(key)
    if not force and cached and 0<=stamp()-cached['quoted_at']<=MARK_TTL_MS:
        return dict(cached,from_cache=True)
    fresh=exit_quote(mint,raw,pair)
    if not fresh: return None
    gross=fresh['expected_usdc']; fee=max(0,float(network_fee_usd))
    qty=float(position.get('quantity') or 0)
    result={'execution_source':'JUPITER_QUOTE_EXPECTED_WITH_BUFFER_V5','market_price':float(coin.get('priceUsd') or 0),
            'fill_price':gross/qty if qty>0 else 0,'market_value_usd':fresh['provider_expected_usdc'],
            'gross_proceeds_usd':gross,'dex_fee_usd':0.0,'network_fee_usd':fee,
            'net_proceeds_usd':max(0,gross-fee),'impact_pct':fresh['price_impact_pct'],
            'slippage_pct':BUFFER_BPS/100,'latency_pct':0.0,
            'slippage_tolerance_pct':fresh['slippage_bps']/100,
            'quoted_at':fresh['quoted_at'],'route':fresh['route'],'context_slot':fresh['context_slot'],
            'token_input_raw':raw,'from_cache':False,'fees_included_in_quote':True,
            'execution_buffer_estimated':True}
    with _LOCK:
        for k,v in list(_CACHE.items()):
            if stamp()-v['quoted_at']>MAX_AGE_MS: _CACHE.pop(k,None)
        _CACHE[key]=dict(result)
    return result

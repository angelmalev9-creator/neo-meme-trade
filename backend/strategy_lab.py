#!/usr/bin/env python3
import json, math, os, time
from pathlib import Path
from typing import Any, Callable
import requests

API_URL=os.getenv('NEO_LOCAL_API','http://127.0.0.1:8788/state')
DEX='https://api.dexscreener.com'
STATE_PATH=Path(os.getenv('NEO_STRATEGY_LAB_PATH','/var/lib/neo-market/strategy_lab.json'))
START_BALANCE=float(os.getenv('NEO_LAB_START_BALANCE','500'))
TRADE_NOTIONAL=float(os.getenv('NEO_LAB_TRADE_NOTIONAL','150'))
POLL_SECONDS=float(os.getenv('NEO_LAB_POLL_SECONDS','2'))
ENTRY_REFRESH_SECONDS=float(os.getenv('NEO_LAB_ENTRY_REFRESH_SECONDS','2'))
STOP_LOSS=4.0
TAKE_PROFIT=18.0
TRAILING=4.0
MAX_HOLD_MIN=7.0
REENTRY_COOLDOWN_MIN=20.0
SESSION=requests.Session()
SESSION.headers.update({'user-agent':'NEO-Strategy-Lab/1.0','accept':'application/json'})

def now_ms(): return int(time.time()*1000)
def num(v,d=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else d
    except Exception: return d

def load_json(path,default):
    try: return json.loads(path.read_text())
    except Exception: return default

def atomic_write(data):
    STATE_PATH.parent.mkdir(parents=True,exist_ok=True)
    tmp=STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False))
    tmp.replace(STATE_PATH)

def flow_map():
    tape=load_json(Path('/var/lib/neo-market/live_tape.json'),{})
    cutoff=now_ms()-60_000
    out={}
    for e in tape.get('events',[]):
        if int(e.get('ts',0))<cutoff: continue
        a=e.get('address')
        if not a: continue
        f=out.setdefault(a,{'trades':0,'buys':0,'sells':0,'buy_usd':0.0,'sell_usd':0.0,'wallets':set(),'max_sell':0.0})
        usd=num(e.get('usd_amount')); f['trades']+=1
        if e.get('wallet'): f['wallets'].add(e['wallet'])
        if e.get('direction')=='BUY': f['buys']+=1; f['buy_usd']+=usd
        else: f['sells']+=1; f['sell_usd']+=usd; f['max_sell']=max(f['max_sell'],usd)
    for f in out.values():
        f['unique_wallets']=len(f.pop('wallets')); f['ratio']=f['buy_usd']/max(f['sell_usd'],1)
    return out
def enrich(c,flows):
    tx=(c.get('txns') or {}).get('m5') or {}
    b=num(tx.get('buys')); s=num(tx.get('sells'))
    liq=num(c.get('liquidityUsd')); mc=num(c.get('marketCap') or c.get('fdv'))
    pc=c.get('priceChange') or {}
    vol1h=num((c.get('volume') or {}).get('h1'))
    return {
      'score':num(c.get('score')),'liq':liq,'m5':num(pc.get('m5')),'h1':num(pc.get('h1')),
      'bs':b/max(s,1),'lmc':liq/max(mc,1),'age':num(c.get('ageMinutes'),999999),
      'vol1h':vol1h,'vol_liq':vol1h/max(liq,1),
      'flow':flows.get(c.get('address'),{'trades':0,'buys':0,'sells':0,'buy_usd':0,'sell_usd':0,'unique_wallets':0,'ratio':0,'max_sell':0})
    }

STRATEGIES=[
 {'id':'ULTRA_PRECISION','name':'Ultra Precision','rule':lambda f: f['score']>=98 and f['liq']>=25000 and 3<=f['m5']<=18 and 1.1<=f['bs']<=3.0 and f['lmc']>=.15 and 8<=f['age']<=180},
 {'id':'PRECISION','name':'Precision','rule':lambda f: f['score']>=95 and f['liq']>=20000 and 2<=f['m5']<=22 and 1.0<=f['bs']<=3.2 and f['lmc']>=.12 and 5<=f['age']<=240},
 {'id':'MOMENTUM','name':'Momentum','rule':lambda f: f['score']>=90 and f['liq']>=15000 and 5<=f['m5']<=30 and f['bs']>=1.15 and f['lmc']>=.08 and 3<=f['age']<=300},
 {'id':'BREAKOUT','name':'Breakout','rule':lambda f: f['score']>=90 and f['liq']>=20000 and 15<f['m5']<=55 and f['bs']>=1.4 and f['lmc']>=.08 and 3<=f['age']<=300},
 {'id':'LIQUIDITY','name':'Liquidity First','rule':lambda f: f['score']>=85 and f['liq']>=40000 and -2<=f['m5']<=20 and f['bs']>=.9 and f['lmc']>=.12 and 5<=f['age']<=720},
 {'id':'ORDER_FLOW','name':'Order Flow','rule':lambda f: f['score']>=85 and f['liq']>=15000 and -5<=f['m5']<=25 and f['flow']['trades']>=3 and f['flow']['ratio']>=1.3 and f['flow']['unique_wallets']>=1 and f['flow']['max_sell']<max(750,f['flow']['buy_usd']*.8)},
 {'id':'EARLY','name':'Early Runner','rule':lambda f: f['score']>=90 and f['liq']>=15000 and 1<=f['m5']<=20 and f['bs']>=1.05 and f['lmc']>=.10 and 2<=f['age']<=60},
 {'id':'TREND','name':'Balanced Trend','rule':lambda f: f['score']>=88 and f['liq']>=20000 and 0<=f['m5']<=15 and 0<=f['h1']<=150 and .9<=f['bs']<=3.0 and f['lmc']>=.10 and 15<=f['age']<=480},
 {'id':'SCALPER','name':'Fast Scalper','rule':lambda f: f['score']>=85 and f['liq']>=15000 and -3<=f['m5']<=12 and f['bs']>=1.05 and f['lmc']>=.08 and 2<=f['age']<=180},
 {'id':'VOLUME_SURGE','name':'Volume Surge','rule':lambda f: f['score']>=88 and f['liq']>=15000 and 2<=f['m5']<=28 and f['vol_liq']>=.35 and f['bs']>=1.1 and f['lmc']>=.08 and 3<=f['age']<=360},
 {'id':'REVERSAL','name':'Reversal Catch','rule':lambda f: f['score']>=85 and f['liq']>=20000 and -10<=f['m5']<=3 and f['h1']>-25 and f['bs']>=1.15 and f['lmc']>=.10 and 10<=f['age']<=480},
 {'id':'FLOW_MOMENTUM','name':'Flow Momentum','rule':lambda f: f['score']>=85 and f['liq']>=15000 and 0<=f['m5']<=30 and f['flow']['trades']>=3 and f['flow']['ratio']>=1.8 and f['flow']['buy_usd']>=150},
 {'id':'FLOW_MOMENTUM_SCALE_OUT','name':'Flow Momentum Scale-Out','rule':lambda f: f['score']>=85 and f['liq']>=15000 and 0<=f['m5']<=30 and f['flow']['trades']>=3 and f['flow']['ratio']>=1.8 and f['flow']['buy_usd']>=150},
]
def empty_book(s):
    return {'id':s['id'],'name':s['name'],'starting_balance':START_BALANCE,'balance':START_BALANCE,
            'position':None,'history':[],'trade_seq':0,'last_entry_by_address':{},'created_at':now_ms()}

def load_state():
    raw=load_json(STATE_PATH,{})
    books={}
    for s in STRATEGIES:
        b=(raw.get('books') or {}).get(s['id']) or empty_book(s)
        b['id']=s['id']; b['name']=s['name']; books[s['id']]=b
    return {'started_at':raw.get('started_at') or now_ms(),'updated_at':now_ms(),'status':'starting','books':books}

STATE=load_state()

def dex_prices(addresses):
    if not addresses: return {}
    out={}
    for i in range(0,len(addresses),30):
        batch=addresses[i:i+30]
        r=SESSION.get(DEX+'/tokens/v1/solana/'+','.join(batch),timeout=10)
        r.raise_for_status()
        best={}
        for p in r.json() if isinstance(r.json(),list) else []:
            a=(p.get('baseToken') or {}).get('address')
            if not a: continue
            l=num((p.get('liquidity') or {}).get('usd'))
            if a not in best or l>num((best[a].get('liquidity') or {}).get('usd')): best[a]=p
        for a,p in best.items(): out[a]=num(p.get('priceUsd'))
    return out

def close_position(book,pos,price,reason):
    entry=num(pos.get('entry_price')); qty=num(pos.get('quantity'))
    final_pnl=qty*(price-entry)
    partial_pnl=num(pos.get('partial_realized_pnl'))
    total_pnl=partial_pnl+final_pnl
    original_notional=num(pos.get('notional_usd'))
    pct=total_pnl/max(original_notional,1e-18)*100
    book['balance']=round(num(book['balance'])+final_pnl,8)
    trade={**pos,'exit_price':price,'closed_at':now_ms(),'exit_reason':reason,
           'final_leg_pnl_usd':round(final_pnl,4),'pnl_usd':round(total_pnl,4),
           'pnl_pct':round(pct,3),'balance_after':round(book['balance'],4)}
    book['history'].insert(0,trade); book['history']=book['history'][:300]; book['position']=None

def realize_partial(book,pos,price,fraction,label):
    entry=num(pos.get('entry_price'))
    original_qty=num(pos.get('original_quantity'),num(pos.get('quantity')))
    remaining_qty=num(pos.get('quantity'))
    sell_qty=min(remaining_qty,original_qty*fraction)
    if sell_qty<=0: return 0.0
    pnl=sell_qty*(price-entry)
    book['balance']=round(num(book['balance'])+pnl,8)
    pos['quantity']=max(0.0,remaining_qty-sell_qty)
    pos['partial_realized_pnl']=round(num(pos.get('partial_realized_pnl'))+pnl,8)
    exits=pos.setdefault('partial_exits',[])
    exits.append({'stage':label,'ts':now_ms(),'price':price,'quantity':sell_qty,
                  'fraction_of_original':fraction,'pnl_usd':round(pnl,4),
                  'move_pct':round((price-entry)/max(entry,1e-18)*100,3)})
    return pnl

def update_positions(flows):
    addresses=[b['position']['address'] for b in STATE['books'].values() if b.get('position')]
    prices=dex_prices(list(dict.fromkeys(addresses))) if addresses else {}
    for book in STATE['books'].values():
        pos=book.get('position')
        if not pos: continue
        price=prices.get(pos['address'])
        if not price: continue
        entry=num(pos['entry_price']); peak=max(num(pos.get('peak_price'),entry),price)
        pct=(price-entry)/entry*100; hold=(now_ms()-int(pos['opened_at']))/60000
        f=flows.get(pos['address'],{})
        reason=None

        if book.get('id')=='FLOW_MOMENTUM_SCALE_OUT':
            # Same FLOW_MOMENTUM entry logic; only exit management differs.
            # Lock 80% progressively and let the final 20% run.
            stages=((5.0,.20,'LOCK_5'),(10.0,.20,'LOCK_10'),(18.0,.20,'LOCK_18'),(30.0,.20,'LOCK_30'))
            completed={x.get('stage') for x in (pos.get('partial_exits') or [])}
            for threshold,fraction,label in stages:
                if pct>=threshold and label not in completed:
                    realize_partial(book,pos,price,fraction,label)
                    completed.add(label)

            if pct<=-STOP_LOSS:
                reason='STOP_LOSS'
            elif f.get('trades',0)>=4 and f.get('sells',0)>=3 and num(f.get('sell_usd'))>=max(250,num(f.get('buy_usd'))*2.5) and pct<3:
                reason='ORDERFLOW_EXIT'
            elif pos.get('partial_exits') and price<=peak*(1-5.0/100):
                reason='SCALE_OUT_PEAK_TRAIL'
            elif hold>=MAX_HOLD_MIN:
                reason='MAX_HOLD'
        else:
            if f.get('trades',0)>=4 and f.get('sells',0)>=3 and num(f.get('sell_usd'))>=max(250,num(f.get('buy_usd'))*2.5) and pct<3: reason='ORDERFLOW_EXIT'
            elif pct<=-STOP_LOSS: reason='STOP_LOSS'
            elif pct>=TAKE_PROFIT: reason='TAKE_PROFIT'
            elif peak>=entry*1.10 and pct<4: reason='PROFIT_PROTECT'
            elif peak>=entry*1.06 and price<=peak*(1-TRAILING/100): reason='TRAILING_STOP'
            elif hold>=MAX_HOLD_MIN: reason='MAX_HOLD'

        remaining_qty=num(pos.get('quantity'))
        open_pnl=remaining_qty*(price-entry)
        total_live_pnl=num(pos.get('partial_realized_pnl'))+open_pnl
        total_live_pct=total_live_pnl/max(num(pos.get('notional_usd')),1e-18)*100
        pos.update({'current_price':price,'peak_price':peak,'pnl_pct':round(total_live_pct,3),
                    'partial_realized_pnl':round(num(pos.get('partial_realized_pnl')),4),
                    'remaining_fraction':round(remaining_qty/max(num(pos.get('original_quantity'),remaining_qty),1e-18),4),
                    'updated_at':now_ms()})
        if reason: close_position(book,pos,price,reason)
def maybe_open(feed,flows):
    now=now_ms()
    for s in STRATEGIES:
        book=STATE['books'][s['id']]
        if book.get('position') or num(book.get('balance'))<10: continue
        for c in feed:
            a=c.get('address')
            if not a: continue
            last=int((book.get('last_entry_by_address') or {}).get(a,0))
            if now-last<REENTRY_COOLDOWN_MIN*60*1000: continue
            f=enrich(c,flows)
            try: ok=bool(s['rule'](f))
            except Exception: ok=False
            if not ok: continue
            price=num(c.get('priceUsd'))
            if price<=0: continue
            notional=min(TRADE_NOTIONAL,num(book['balance']))
            book['trade_seq']=int(book.get('trade_seq',0))+1
            qty=notional/price
            pos={'trade_no':book['trade_seq'],'strategy_id':s['id'],'symbol':c.get('symbol'),'name':c.get('name'),
                 'address':a,'pairAddress':c.get('pairAddress'),'entry_price':price,'current_price':price,'peak_price':price,
                 'quantity':qty,'original_quantity':qty,'notional_usd':notional,'opened_at':now,'updated_at':now,
                 'score':c.get('score'),'entry_features':f,'partial_realized_pnl':0.0,'partial_exits':[]}
            book['position']=pos
            book.setdefault('last_entry_by_address',{})[a]=now
            break

def stats(book):
    h=book.get('history') or []; wins=[t for t in h if num(t.get('pnl_usd'))>0]
    gp=sum(max(0,num(t.get('pnl_usd'))) for t in h); gl=-sum(min(0,num(t.get('pnl_usd'))) for t in h)
    unreal=0.0
    p=book.get('position')
    if p: unreal=num(p.get('quantity'))*(num(p.get('current_price'))-num(p.get('entry_price')))
    equity=num(book.get('balance'))+unreal
    partial_count=sum(len(t.get('partial_exits') or []) for t in h)+len((p or {}).get('partial_exits') or [])
    locked_partial=sum(num(t.get('partial_realized_pnl')) for t in h)+num((p or {}).get('partial_realized_pnl'))
    return {'trades':len(h),'wins':len(wins),'losses':len(h)-len(wins),'win_rate':round(len(wins)/len(h)*100,1) if h else 0,
            'profit_factor':round(gp/gl,2) if gl>0 else (99.0 if gp>0 else 0.0),'realized_pnl':round(num(book.get('balance'))-START_BALANCE,2),
            'equity':round(equity,2),'return_pct':round((equity-START_BALANCE)/START_BALANCE*100,2),'open':bool(p),
            'partial_exits':partial_count,'partial_locked_pnl':round(locked_partial,2)}

def persist(status='online',error=None):
    STATE['status']=status; STATE['updated_at']=now_ms()
    STATE['stats']={k:stats(v) for k,v in STATE['books'].items()}
    if error: STATE['error']=str(error)[:200]
    else: STATE.pop('error',None)
    atomic_write(STATE)

def main():
    last_entry=0
    while True:
        started=time.time()
        try:
            flows=flow_map()
            update_positions(flows)
            if time.time()-last_entry>=ENTRY_REFRESH_SECONDS:
                r=SESSION.get(API_URL,timeout=5); r.raise_for_status()
                maybe_open(r.json().get('feed') or [],flows); last_entry=time.time()
            persist('online')
        except Exception as e:
            persist('degraded',e)
        time.sleep(max(.25,POLL_SECONDS-(time.time()-started)))

if __name__=='__main__': main()

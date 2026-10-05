#!/usr/bin/env python3
import json, os, time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
import requests

API_URL = os.getenv('NEO_LOCAL_API', 'http://127.0.0.1:8788/state')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://rpc.solanatracker.io/public')
OUT = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
MAX_TRACKED = int(os.getenv('NEO_TAPE_MAX_PAIRS', '60'))
MAX_EVENTS = int(os.getenv('NEO_TAPE_MAX_EVENTS', '1600'))
POLL_SECONDS = float(os.getenv('NEO_TAPE_POLL_SECONDS', '1.0'))
SESSION = requests.Session()
SESSION.headers.update({'content-type':'application/json','user-agent':'NEO-LiveTape/2.0'})
EVENTS = deque(maxlen=MAX_EVENTS)
SEEN = set()
INITIALIZED_PAIRS = set()
WALLET_HITS = defaultdict(int)
STATUS = {'status':'starting','tracked_pairs':0,'updated_at':0,
          'source':'solana-mainnet-http-live','poll_seconds':POLL_SECONDS}

def now_ms():
    return int(time.time()*1000)

def feed_snapshot():
    r=SESSION.get(API_URL,timeout=5)
    r.raise_for_status()
    coins=list(r.json().get('feed',[]) or [])
    def priority(c):
        try: age=float(c.get('ageMinutes') if c.get('ageMinutes') is not None else 999999)
        except: age=999999
        tx=(c.get('txns') or {}).get('m5') or {}
        activity=float(tx.get('buys') or 0)+float(tx.get('sells') or 0)
        score=float(c.get('score') or 0)
        return (1 if age<=180 else 0, activity, score)
    coins.sort(key=priority,reverse=True)
    rows=[]
    for c in coins[:MAX_TRACKED]:
        pair=c.get('pairAddress'); mint=c.get('address')
        if pair and mint:
            rows.append({'pair':pair,'address':mint,'symbol':c.get('symbol') or '?',
                         'price':float(c.get('priceUsd') or 0),'score':float(c.get('score') or 0)})
    return rows

def rpc_batch(calls):
    if not calls: return []
    payload=[{'jsonrpc':'2.0','id':i+1,'method':m,'params':p} for i,(m,p) in enumerate(calls)]
    r=SESSION.post(RPC_URL,json=payload,timeout=15)
    r.raise_for_status()
    data=r.json()
    if not isinstance(data,list):
        raise RuntimeError('RPC batch response is not a list')
    return sorted(data,key=lambda x:int(x.get('id',0)))
def atomic_write():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    payload={**STATUS,'events':list(EVENTS)}
    tmp=OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False))
    tmp.replace(OUT)

def key_of(item):
    return str(item.get('pubkey') if isinstance(item,dict) else item)

def amount_map(items,mint):
    out=defaultdict(float)
    for b in items or []:
        if b.get('mint')!=mint: continue
        owner=b.get('owner')
        if not owner: continue
        ui=b.get('uiTokenAmount') or {}
        try: amount=float(ui.get('uiAmountString') or ui.get('uiAmount') or 0)
        except: amount=0.0
        out[owner]+=amount
    return out

def parse_trade(tx,meta):
    m=(tx or {}).get('meta') or {}
    if m.get('err') is not None: return None
    mint=meta['address']
    pre=amount_map(m.get('preTokenBalances'),mint)
    post=amount_map(m.get('postTokenBalances'),mint)
    deltas={o:post.get(o,0)-pre.get(o,0) for o in set(pre)|set(post)}
    if not deltas: return None
    message=((tx or {}).get('transaction') or {}).get('message') or {}
    keys=message.get('accountKeys') or []
    signers=[key_of(k) for k in keys if isinstance(k,dict) and k.get('signer')]
    signer_deltas=[(o,d) for o,d in deltas.items() if o in signers and abs(d)>0]
    if not signer_deltas: return None
    wallet,delta=max(signer_deltas,key=lambda kv:abs(kv[1]))
    direction='BUY' if delta>0 else 'SELL'
    token_amount=abs(delta)
    usd=token_amount*float(meta.get('price') or 0)
    WALLET_HITS[wallet]+=1
    repeat=WALLET_HITS[wallet]
    if usd>=2500: note='WHALE '+direction
    elif usd>=750: note='LARGE '+direction
    elif repeat>=3: note='REPEAT WALLET'
    else: note=direction
    bt=(tx or {}).get('blockTime')
    return {'ts':int(bt*1000) if bt else now_ms(),'direction':direction,
            'token_amount':round(token_amount,6),'usd_amount':round(usd,2),
            'wallet':wallet,'note':note,'address':mint,'pairAddress':meta['pair'],
            'symbol':meta['symbol'],'price_estimate':float(meta.get('price') or 0)}
def poll_once():
    feed=feed_snapshot()
    STATUS['tracked_pairs']=len(feed)
    STATUS['updated_at']=now_ms()
    if not feed:
        STATUS['status']='degraded'; STATUS['error']='No pairs from NEO feed'; atomic_write(); return
    calls=[('getSignaturesForAddress',[m['pair'],{'limit':8,'commitment':'confirmed'}]) for m in feed]
    answers=rpc_batch(calls)
    new=[]
    for meta,answer in zip(feed,answers):
        rows=answer.get('result') or []
        pair=meta['pair']
        if pair not in INITIALIZED_PAIRS:
            for row in rows:
                sig=row.get('signature')
                if sig: SEEN.add(sig)
            INITIALIZED_PAIRS.add(pair)
            continue
        for row in reversed(rows):
            sig=row.get('signature')
            if not sig or sig in SEEN or row.get('err') is not None: continue
            SEEN.add(sig)
            new.append((sig,row.get('slot'),meta))
    if len(SEEN)>12000:
        # Retain recent event signatures and let active pairs re-prime naturally.
        keep={e.get('signature') for e in EVENTS if e.get('signature')}
        SEEN.clear(); SEEN.update(keep)
        INITIALIZED_PAIRS.clear()
    if new:
        new=new[-120:]
        tx_calls=[('getTransaction',[sig,{'encoding':'jsonParsed','commitment':'confirmed',
                   'maxSupportedTransactionVersion':0}]) for sig,_,_ in new]
        tx_answers=rpc_batch(tx_calls)
        for (sig,slot,meta),answer in zip(new,tx_answers):
            tx=answer.get('result')
            trade=parse_trade(tx,meta) if tx else None
            if not trade: continue
            trade['signature']=sig; trade['slot']=slot
            EVENTS.appendleft(trade)
    STATUS['status']='online'
    STATUS.pop('error',None)
    STATUS['updated_at']=now_ms()
    atomic_write()

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    while True:
        started=time.time()
        try:
            poll_once()
        except Exception as e:
            STATUS['status']='degraded'
            STATUS['error']=str(e)[:180]
            STATUS['updated_at']=now_ms()
            atomic_write()
        wait=max(0.25,POLL_SECONDS-(time.time()-started))
        time.sleep(wait)

if __name__=='__main__':
    main()

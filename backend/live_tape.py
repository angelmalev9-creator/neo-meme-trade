#!/usr/bin/env python3
import asyncio, json, os, time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
import requests
import websockets

API_URL = os.getenv('NEO_LOCAL_API', 'http://127.0.0.1:8788/state')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
WSS_URL = os.getenv('SOLANA_WSS_URL', 'wss://api.mainnet-beta.solana.com')
OUT = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
MAX_TRACKED = int(os.getenv('NEO_TAPE_MAX_PAIRS', '45'))
MAX_EVENTS = int(os.getenv('NEO_TAPE_MAX_EVENTS', '600'))
SESSION = requests.Session()
SESSION.headers.update({'content-type':'application/json','user-agent':'NEO-LiveTape/1.0'})
EVENTS = deque(maxlen=MAX_EVENTS)
SEEN = set()
WALLET_HITS = defaultdict(int)
STATUS = {'status':'starting','tracked_pairs':0,'updated_at':0,'source':'solana-mainnet-wss'}

def now_ms():
    return int(time.time()*1000)

def rpc(method: str, params: list[Any]):
    payload={'jsonrpc':'2.0','id':1,'method':method,'params':params}
    r=SESSION.post(RPC_URL,json=payload,timeout=10)
    r.raise_for_status()
    data=r.json()
    if data.get('error'):
        raise RuntimeError(str(data['error']))
    return data.get('result')

def feed_snapshot():
    r=SESSION.get(API_URL,timeout=5)
    r.raise_for_status()
    state=r.json()
    rows=[]
    for c in state.get('feed',[])[:MAX_TRACKED]:
        pair=c.get('pairAddress'); mint=c.get('address')
        if pair and mint:
            rows.append({'pair':pair,'address':mint,'symbol':c.get('symbol') or '?',
                         'price':float(c.get('priceUsd') or 0),'score':float(c.get('score') or 0)})
    return rows

def atomic_write():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    payload={**STATUS,'events':list(EVENTS)}
    tmp=OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False))
    tmp.replace(OUT)

def key_of(item):
    return str(item.get('pubkey') if isinstance(item,dict) else item)

def amount_map(items, mint):
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
def parse_trade(tx, meta):
    m=(tx or {}).get('meta') or {}
    if m.get('err') is not None: return None
    mint=meta['address']
    pre=amount_map(m.get('preTokenBalances'),mint)
    post=amount_map(m.get('postTokenBalances'),mint)
    owners=set(pre)|set(post)
    deltas={o:post.get(o,0)-pre.get(o,0) for o in owners}
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
    if usd>=2500:
        note='WHALE '+direction
    elif usd>=750:
        note='LARGE '+direction
    elif repeat>=3:
        note='REPEAT WALLET'
    else:
        note=direction
    return {'ts':now_ms(),'direction':direction,'token_amount':round(token_amount,6),
            'usd_amount':round(usd,2),'wallet':wallet,'note':note,
            'address':mint,'pairAddress':meta['pair'],'symbol':meta['symbol']}

async def fetch_and_record(signature, slot, meta, sem):
    if signature in SEEN: return
    SEEN.add(signature)
    if len(SEEN)>5000:
        SEEN.clear(); SEEN.add(signature)
    async with sem:
        tx=None
        for _ in range(3):
            try:
                tx=await asyncio.to_thread(rpc,'getTransaction',[signature,{'encoding':'jsonParsed',
                    'commitment':'confirmed','maxSupportedTransactionVersion':0}])
                if tx: break
            except Exception:
                pass
            await asyncio.sleep(.35)
        trade=parse_trade(tx,meta) if tx else None
        if not trade: return
        trade['signature']=signature
        trade['slot']=slot
        EVENTS.appendleft(trade)
        STATUS['updated_at']=now_ms()
        atomic_write()

async def run_stream():
    sem=asyncio.Semaphore(8)
    pending={}
    subs={}
    req_id=100
    watched={}
    async with websockets.connect(WSS_URL,ping_interval=20,ping_timeout=20,max_size=8_000_000) as ws:
        STATUS['status']='online'; atomic_write()
        last_refresh=0
        while True:
            if time.time()-last_refresh>10:
                try:
                    latest={r['pair']:r for r in await asyncio.to_thread(feed_snapshot)}
                    for sid,old in list(subs.items()):
                        if old['pair'] not in latest:
                            req_id+=1
                            await ws.send(json.dumps({'jsonrpc':'2.0','id':req_id,'method':'logsUnsubscribe','params':[sid]}))
                            subs.pop(sid,None)
                    existing={m['pair'] for m in subs.values()}|{m['pair'] for m in pending.values()}
                    for pair,meta in latest.items():
                        if pair in existing: continue
                        req_id+=1; pending[req_id]=meta
                        await ws.send(json.dumps({'jsonrpc':'2.0','id':req_id,'method':'logsSubscribe',
                            'params':[{'mentions':[pair]},{'commitment':'confirmed'}]}))
                    watched=latest
                    STATUS['tracked_pairs']=len(latest); STATUS['updated_at']=now_ms(); atomic_write()
                except Exception as e:
                    STATUS['status']='degraded'; STATUS['error']=str(e)[:180]; atomic_write()
                last_refresh=time.time()
            try:
                raw=await asyncio.wait_for(ws.recv(),timeout=1.0)
            except asyncio.TimeoutError:
                continue
            msg=json.loads(raw)
            if 'id' in msg and 'result' in msg and msg['id'] in pending:
                subs[int(msg['result'])]=pending.pop(msg['id'])
                continue
            if msg.get('method')!='logsNotification':
                continue
            params=msg.get('params') or {}
            sid=params.get('subscription')
            meta=subs.get(sid)
            value=((params.get('result') or {}).get('value') or {})
            if not meta or value.get('err') is not None:
                continue
            sig=value.get('signature')
            slot=((params.get('result') or {}).get('context') or {}).get('slot')
            if sig:
                asyncio.create_task(fetch_and_record(sig,slot,meta,sem))

async def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    while True:
        try:
            STATUS.pop('error',None)
            await run_stream()
        except Exception as e:
            STATUS['status']='reconnecting'; STATUS['error']=str(e)[:180]; STATUS['updated_at']=now_ms()
            atomic_write()
            await asyncio.sleep(3)

if __name__=='__main__':
    asyncio.run(main())

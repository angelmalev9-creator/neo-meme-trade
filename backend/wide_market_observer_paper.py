#!/usr/bin/env python3
"""Read-only broad-market PAPER observer; no trading or order capability.

Scans unverified Solana mint leads from an independent websocket feed against
real public market-data records, at a bounded rate. It never changes the GOLD
engine and does not consider a lead an analysed market until data is returned.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests
import wide_discovery

ROOT=Path(os.getenv('NEO_WIDE_ROOT','/var/lib/neo-market/users/42d3192d-f033-4061-85d6-2408c5e168e7'))
WATCH=ROOT/'wide_discovery.json'
REPORT=ROOT/'wide_market_report.json'
LEDGER=ROOT/'wide_market_daily_checked.json'
API='https://api.dexscreener.com'
CYCLE_SLEEP=1.1  # <=55 market API calls/minute, leaves budget for PAPER engine
TIMEOUT=(1.5,7)
PORT=int(os.getenv('NEO_WIDE_HTTP_PORT','18805'))


class Observer:
    def __init__(self):
        self.lock=threading.RLock()
        self.ledger=wide_discovery.DailyMarketLedger(LEDGER)
        self.seen_at:dict[str,int]={}
        self.checked_at:dict[str,int]={}
        self.validated:dict[str,dict[str,Any]]={}
        self.running=True
        self.requests_success=0
        self.requests_failed=0
        self.rate_limits=0
        self.scan_cycles=0
        self.last_request_at=0
        self.last_provider_ok_at=0
        self.last_provider_error=None
        self.backoff_until=0
        self.last_tokens=[]
        self.stream_stats={}
        self.batches_done=0
        self.started_at=int(time.time()*1000)
        self.http=requests.Session()
        self.http.headers.update({'User-Agent':'NEO-ReadOnly-Wide-Paper/1.0','Accept':'application/json'})

    def update_leads(self,now):
        rows,stats=wide_discovery.read_candidates(WATCH,now_ms=now,limit=5000,
                                                  validator=lambda x:bool(wide_discovery.BASE58.fullmatch(x)))
        self.last_tokens=rows
        self.stream_stats=stats
        for mint in rows:self.seen_at[mint]=now
        # Keep only recent observation state; don't accumulate unbounded addresses.
        expire=now-2*60*60*1000
        for key in list(self.seen_at):
            if self.seen_at[key]<expire:
                self.seen_at.pop(key,None)
                self.checked_at.pop(key,None)
                self.validated.pop(key,None)

    def select_batch(self,now):
        # Prefer never-checked mints first, then stale previously checked mints.
        fresh=[a for a in self.last_tokens if a not in self.checked_at]
        if fresh:return fresh[:30]
        old=[a for a in self.last_tokens if now-self.checked_at.get(a,0)>=15*60*1000]
        return old[:30]

    def fetch(self,addresses):
        if not addresses:return []
        url=API+'/tokens/v1/solana/'+','.join(addresses)
        response=self.http.get(url,timeout=TIMEOUT)
        if response.status_code==429:
            self.rate_limits+=1
            self.backoff_until=int(time.time()*1000)+60000
            raise RuntimeError('DEXSCREENER_RATE_LIMIT_429')
        response.raise_for_status()
        body=response.json()
        if not isinstance(body,list):raise ValueError('unexpected_market_response')
        return [x for x in body if isinstance(x,dict)]

    def evaluate(self,addresses,rows,stamp):
        requested=set(addresses)
        found=set()
        for row in rows:
            if str(row.get('chainId','')).lower()!='solana':continue
            address=str((row.get('baseToken') or {}).get('address') or '')
            if address not in requested:continue
            try:
                price=float(row.get('priceUsd') or 0)
                liq=float((row.get('liquidity') or {}).get('usd') or 0)
            except (TypeError,ValueError):continue
            if not (price>0 and liq>=0):continue
            found.add(address)
            self.validated[address]={
                'pair':str(row.get('pairAddress') or ''),
                'price_observed_at':stamp,
                'symbol':str((row.get('baseToken') or {}).get('symbol') or '')[:20],
                'has_liquidity':liq>0,
            }
        self.ledger.observe(requested=addresses,with_market=found,now=stamp)
        for mint in addresses:self.checked_at[mint]=stamp
        return len(found)

    def status(self):
        with self.lock:
            stamp=int(time.time()*1000)
            source_age=self.stream_stats.get('stream_age_seconds')
            stats=self.ledger.stats()
            market_valid_today=stats['markets_validated_unique_today']
            return {
              'mode':'READ_ONLY_PAPER_MARKET_OBSERVER','status':'online' if self.last_provider_ok_at and stamp-self.last_provider_ok_at<90000 else 'starting' if self.requests_success==0 else 'degraded',
              'updated_at':stamp,'started_at':self.started_at,'stream_status':self.stream_stats.get('status','unavailable'),
              'stream_observed_window':self.stream_stats.get('stream_observed_window',0),
              'stream_observed_since_start':self.stream_stats.get('stream_observed_total',0),
              'stream_listed':self.stream_stats.get('stream_listed',0),
              'stream_age_seconds':source_age,
              'daily_distinct_market_addresses_checked':stats['market_checked_unique_today'],
              'daily_distinct_markets_validated':market_valid_today,
              'report_day':stats['market_checked_day'],
              'requests_success':self.requests_success,'requests_failed':self.requests_failed,
              'rate_limits':self.rate_limits,'scan_cycles':self.scan_cycles,
              'last_market_response_at':self.last_provider_ok_at,
              'last_error':self.last_provider_error,'backoff_active':stamp<self.backoff_until,
              'read_only':True,
              'gold_engine_unchanged':True,
              'main_engine_batch_size':240,
              'note':'Live market observer checks unverified token addresses separately. No trades or orders.',
            }

    def write_status(self):
        p=REPORT; temp=p.with_name('.'+p.name+'.tmp')
        p.parent.mkdir(parents=True,exist_ok=True)
        temp.write_text(json.dumps(self.status(),ensure_ascii=False),encoding='utf8')
        temp.replace(p)

    def loop(self):
        while self.running:
            started=time.monotonic()
            stamp=int(time.time()*1000)
            try:
                with self.lock:
                    self.update_leads(stamp)
                    batch=self.select_batch(stamp)
                if batch and stamp>=self.backoff_until:
                    rows=self.fetch(batch)
                    with self.lock:
                        self.evaluate(batch,rows,int(time.time()*1000))
                        self.requests_success+=1;self.last_provider_ok_at=int(time.time()*1000)
                        self.last_provider_error=None;self.scan_cycles+=1
                elif not batch:
                    time.sleep(3)
            except Exception as exc:
                with self.lock:
                    self.requests_failed+=1
                    self.last_provider_error=str(exc)[:120]
                time.sleep(4)
            try:self.write_status()
            except OSError as exc:print('PAPER_WIDE_REPORT_ERROR',str(exc)[:120],flush=True)
            time.sleep(max(0,CYCLE_SLEEP-(time.monotonic()-started)))


HTML='''<!doctype html><html lang="bg"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEO · Пазарно наблюдение</title><style>
:root{color-scheme:dark}body{margin:0;background:#070d11;color:#e8f0f2;font:15px system-ui,Arial,sans-serif}main{max-width:1020px;margin:auto;padding:34px 18px}.tag{display:inline-block;background:#163c34;color:#9cecca;border:1px solid #296c55;padding:5px 10px;border-radius:22px;font-weight:800;font-size:11px}h1{font-size:clamp(26px,5vw,38px);letter-spacing:-1px;margin:16px 0 9px}p{line-height:1.6;color:#aabcc6}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:28px 0}.card{background:#111c22;border:1px solid #294047;border-radius:16px;padding:22px}.label{font-size:11px;color:#a9bec7;text-transform:uppercase;letter-spacing:.06em;font-weight:700}.value{font-size:31px;font-weight:800;margin:10px 0}.hint{font-size:12px;color:#8ca3ad}.sub{border-top:1px solid #24333a;padding:17px 0;font-size:13px;color:#aabcc6}.ok{color:#8de9ba}.warn{color:#ffcd89}a{color:#9cecca}small{font-size:12px;color:#738d97}</style></head>
<body><main><span class="tag">NEO · PAPER ONLY · READ-ONLY</span><h1>Широко наблюдение на Solana</h1><p>Показателите са реални и разделят откритите токени от тези, за които са получени пазарни данни. Това наблюдение не отваря сделки и не променя стратегията GOLD.</p><div class="grid" id="cards"><div class="card">Зарежда показатели...</div></div><div class="sub" id="detail">Проверка...</div><div class="sub">Основният GOLD engine продължава да използва собствените си 240 адреса на цикъл. Допълнителният скенер изследва хиляди токени независимо, без да променя условията за вход или изход. <a href="https://angelmalev9-creator.github.io/neo-meme-trade/">Върни се към NEO</a>.</div><small id="updated"></small></main><script>
const fmt=x=>(Number(x)||0).toLocaleString('bg-BG');const items=[['stream_observed_window','Наблюдавани токени','Различни токени в текущия поток'],['daily_distinct_market_addresses_checked','Проверени днес','Уникални адреси, проверени чрез пазарен API'],['daily_distinct_markets_validated','С потвърдени пазарни данни','Реално намерен пазар и цена'],['stream_listed','В активната листа','Кандидати за последваща проверка']];
async function update(){try{const r=await fetch('status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();document.getElementById('cards').innerHTML=items.map(([key,title,hint])=>'<div class="card"><div class="label">'+title+'</div><div class="value">'+fmt(s[key])+'</div><div class="hint">'+hint+'</div></div>').join('');document.getElementById('detail').innerHTML='<b class="'+(s.status==='online'?'ok':'warn')+'">'+s.status.toUpperCase()+'</b> · '+fmt(s.requests_success)+' успешни API пакета · '+fmt(s.rate_limits)+' ограничения (429) · поток '+s.stream_status+' · '+(s.last_error||'без грешки');document.getElementById('updated').textContent='Обновено: '+new Date(s.updated_at).toLocaleTimeString('bg-BG')+' · дневни стойности за '+s.report_day+' (София)';}catch(e){document.getElementById('detail').textContent='Неуспешна проверка: '+e.message;}}update();setInterval(update,10000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/','/index.html'):
            body=HTML.encode('utf8');ctype='text/html; charset=utf-8'
        elif self.path.startswith('/status'):
            body=json.dumps(OBSERVER.status(),ensure_ascii=False).encode('utf8')
            ctype='application/json; charset=utf-8'
        else:
            self.send_error(404);return
        self.send_response(200)
        self.send_header('Content-Type',ctype)
        self.send_header('Cache-Control','no-store')
        self.send_header('Access-Control-Allow-Origin','https://angelmalev9-creator.github.io')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def log_message(self,*a):return


OBSERVER=Observer()
def main():
    threading.Thread(target=OBSERVER.loop,daemon=True).start()
    print('NEO PAPER broad observer listening on localhost port',PORT,flush=True)
    try:ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
    except KeyboardInterrupt:pass


if __name__=='__main__':main()

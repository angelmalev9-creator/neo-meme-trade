"""Read-only quotes. Shared rate limit, short identical-request cache, exit priority.
Never builds/signs/submits transactions. Timestamp refers to HTTP reception,
not time waiting for the rate limiter. Legacy clients use the same lock/stamp.
"""
import fcntl,hashlib,json,os,threading,time
from pathlib import Path
import requests

ROOT=Path(os.getenv('NEO_JUPITER_LOCK_PATH','/var/lib/neo-market/jupiter_quote.lock')).parent
LOCK=Path(os.getenv('NEO_JUPITER_LOCK_PATH',str(ROOT/'jupiter_quote.lock')))
STAMP=Path(os.getenv('NEO_JUPITER_STAMP_PATH',str(ROOT/'jupiter_quote_last.txt')))
INTERVAL=float(os.getenv('NEO_JUPITER_KEYLESS_MIN_INTERVAL','2.10'))
KEY=os.getenv('JUPITER_API_KEY','').strip()
URL=os.getenv('NEO_JUPITER_QUOTE_URL','https://api.jup.ag/swap/v1/quote')
LOCAL=threading.local()

def now_ms():return int(time.time()*1000)
def _write(path,obj):
 tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
 tmp.write_text(json.dumps(obj));tmp.replace(path)

def _cached(path):
 try:
  d=json.loads(path.read_text())
  if 0<=now_ms()-int(d.get('_received_at',0))<=700:return dict(d,_cache_hit=True)
 except (OSError,ValueError,TypeError):pass
 return None

def quote(inp,out,amount,*,purpose='entry',slippage_bps=100):
 if type(amount) is not int or amount<=0:return None
 ROOT.mkdir(parents=True,exist_ok=True)
 cache=ROOT/'quote-response-cache';cache.mkdir(exist_ok=True)
 params=dict(inputMint=inp,outputMint=out,amount=str(amount),swapMode='ExactIn',
             slippageBps=int(slippage_bps),instructionVersion='V2',restrictIntermediateTokens='true')
 digest=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
 file=cache/(digest+'.json'); data=_cached(file)
 if data:return data
 priority=ROOT/'quote-exit-priority';priority.mkdir(exist_ok=True)
 marker=priority/(str(os.getpid())+'-'+str(threading.get_ident())+'.json')
 begin=now_ms(); is_exit=purpose=='exit'; deadline=time.monotonic()+(3.0 if is_exit else 4.5)
 if is_exit:_write(marker,{'requested_at':begin})
 try:
  while True:
   waiters=[]
   for p in priority.glob('*.json'):
    try:
     age=time.time()-p.stat().st_mtime
     if age>5:p.unlink(missing_ok=True)
     else:waiters.append((p.stat().st_mtime,p.name))
    except OSError:pass
   waiters.sort()
   other_priority=bool(waiters) and (not is_exit or waiters[0][1]!=marker.name)
   if not other_priority:
    with LOCK.open('a+') as h:
     acquired=False
     try:
      fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB); acquired=True
      data=_cached(file)
      if data:return data
      try:last=float(STAMP.read_text())
      except (OSError,ValueError):last=0
      # Keep the existing shared quota; never invent a faster paid limit.
      if time.time()-last>=INTERVAL:
       http=getattr(LOCAL,'http',None)
       if http is None:
        http=requests.Session();http.headers['User-Agent']='NEO-Paper-Quotes-Audited/1.0';LOCAL.http=http
       sent=now_ms()
       try:
        r=http.get(URL,params=params,headers={'x-api-key':KEY} if KEY else {},timeout=(1.0,2.5))
        received=now_ms();r.raise_for_status();data=r.json()
        if not isinstance(data,dict):return None
        data.update(_requested_at=begin,_sent_at=sent,_received_at=received,
                    _quotedAtMs=received,_queue_ms=sent-begin,_http_ms=received-sent,
                    _cache_hit=False)
        _write(file,data)
        # Bounded cache, no historical account data or credentials are stored here.
        if len(list(cache.glob('*.json')))>500:
         for old in cache.glob('*.json'):
          try:
           if time.time()-old.stat().st_mtime>30:old.unlink(missing_ok=True)
          except OSError:pass
        return data
       except (requests.RequestException,ValueError,TypeError):return None
       finally:STAMP.write_text(str(time.time()))
     except BlockingIOError:pass
     finally:
      if acquired:fcntl.flock(h,fcntl.LOCK_UN)
   if time.monotonic()>=deadline:return None
   time.sleep(.04)
 finally:
  if is_exit:marker.unlink(missing_ok=True)

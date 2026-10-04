"""Read-only, cached pre-entry checks for the paper engine. No swap submission.
No claim of complete rug detection: holder/LP findings are third-party reports;
full funding graphs and wallet-age analysis are not fabricated when unavailable.
"""
import concurrent.futures as cf
import fcntl
import json
import math
import os
import re
import threading
import time
from pathlib import Path
import requests

VERSION='RUG_GUARD_V1'
RPC=os.getenv('NEO_RISK_RPC_URL','https://api.mainnet-beta.solana.com')
ROOT=Path(os.getenv('NEO_RISK_CACHE_DIR','/var/lib/neo-market/engine-risk-cache'))
TOKEN_PROGRAMS={'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA','TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'}
ADDR=re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
TTL_MS=60_000
_POOL=cf.ThreadPoolExecutor(max_workers=2,thread_name_prefix='engine-risk')
_LOCK=threading.Lock()
_PENDING={}

def now_ms(): return int(time.time()*1000)
def num(x,default=math.nan):
    try:
        v=float(x); return v if math.isfinite(v) else default
    except (TypeError,ValueError,OverflowError): return default

def assess(mint,pair,account,report):
    reasons=[]; metrics={}
    try:
        if account.get('owner') not in TOKEN_PROGRAMS: reasons.append('unsupported_token_program')
        parsed=(account.get('data') or {}).get('parsed') or {}
        info=parsed.get('info') or {}
        if parsed.get('type')!='mint' or info.get('isInitialized') is not True or int(info.get('supply',0))<=0:
            reasons.append('invalid_mint')
        if 'mintAuthority' not in info or info['mintAuthority'] is not None: reasons.append('mint_authority')
        if 'freezeAuthority' not in info or info['freezeAuthority'] is not None: reasons.append('freeze_authority')
        decimals=info.get('decimals')
        if type(decimals) is not int or not 0<=decimals<=18: reasons.append('mint_decimals')
        metrics['decimals']=decimals
        # Only metadata extensions are supported by this spot-paper strategy.
        # Transfer hooks/fees, permanent delegates, frozen/pausable states and
        # unknown extensions require bespoke handling, so they cannot pass silently.
        extensions=info.get('extensions',[])
        if not isinstance(extensions,list): reasons.append('token_extensions_unknown')
        else:
            names=[str(e.get('extension','')) for e in extensions if isinstance(e,dict)]
            metrics['extensions']=names
            if len(names)!=len(extensions) or any(x not in {'metadataPointer','tokenMetadata'} for x in names):
                reasons.append('unsupported_token_extension')
        if report.get('mint')!=mint or report.get('rugged') is not False: reasons.append('rug_report_invalid_or_rugged')
        risks=report.get('risks')
        if not isinstance(risks,list): reasons.append('rug_report_incomplete')
        else:
            danger=[str(r.get('name',''))[:120] for r in risks if str(r.get('level','')).lower() in {'danger','critical'}]
            metrics['critical_risks']=danger
            if danger: reasons.append('rugcheck_critical')
        markets=report.get('markets') or []
        selected=next((m for m in markets if m.get('pubkey')==pair and mint in (m.get('mintA'),m.get('mintB'))),None)
        if not selected: reasons.append('pool_not_verified')
        else:
            pct=num((selected.get('lp') or {}).get('lpLockedPct'))
            metrics['reported_lp_locked_pct']=pct if math.isfinite(pct) else None
            lp=selected.get('lp') or {}
            if lp.get('quoteMint')=='So11111111111111111111111111111111111111112': metrics['sol_usd']=num(lp.get('quotePrice'),0)
            elif lp.get('baseMint')=='So11111111111111111111111111111111111111112': metrics['sol_usd']=num(lp.get('basePrice'),0)
            if not math.isfinite(pct) or not 95<=pct<=100.0001: reasons.append('lp_control_risk')
        # Exclude only vault addresses explicitly identified by the risk report.
        vaults=set(); market_owners=set()
        for m in markets:
            market_owners.add(m.get('pubkey'))
            for field in ['liquidityA','liquidityB']:
                if m.get(field): vaults.add(m[field])
        holders=report.get('topHolders'); owners={}
        if not isinstance(holders,list) or not holders: reasons.append('holders_unverified')
        else:
            for h in holders:
                if h.get('address') in vaults: continue
                owner=h.get('owner')
                pct=num(h.get('pct'))
                if not owner or not math.isfinite(pct) or not 0<=pct<=100:
                    reasons.append('holders_unverified'); continue
                owners[owner]=owners.get(owner,0)+pct
            largest=sorted(owners.values(),reverse=True)
            top1=largest[0] if largest else 0; top10=sum(largest[:10])
            metrics.update(reported_top_owner_pct=top1,reported_top10_pct=top10)
            if top1>20 or top10>35: reasons.append('holder_concentration')
        if num(report.get('graphInsidersDetected'),0)>0: reasons.append('reported_linked_insiders')
        metrics['report_detected_at']=report.get('detectedAt')
    except (TypeError,ValueError,KeyError,AttributeError,OverflowError):
        reasons.append('risk_schema_unverified')
    return {'version':VERSION,'status':'blocked' if reasons else 'pass','mint':mint,'pair':pair,
            'checked_at':now_ms(),'reasons':sorted(set(reasons)),'metrics':metrics,
            'limitations':['third_party_holder_and_lp_report','no_complete_funding_graph','no_guarantee_against_rug']}

def _file(mint,pair): return ROOT/(mint+'-'+pair+'.json')
def _read(mint,pair):
    try:
        d=json.loads(_file(mint,pair).read_text())
        ttl=TTL_MS if d.get('status') in {'pass','blocked'} else 15_000
        if d.get('version')==VERSION and d.get('mint')==mint and d.get('pair')==pair and 0<=now_ms()-int(d.get('checked_at',0))<=ttl: return d
    except (OSError,ValueError,TypeError): pass
    return None

def _fetch(mint,pair):
    ROOT.mkdir(parents=True,exist_ok=True)
    with (ROOT/(mint+'-'+pair+'.lock')).open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        existing=_read(mint,pair)
        if existing: return existing
        try:
            r=requests.post(RPC,json={'jsonrpc':'2.0','id':1,'method':'getAccountInfo','params':[mint,{'encoding':'jsonParsed','commitment':'confirmed'}]},timeout=(3,8))
            r.raise_for_status(); body=r.json()
            if body.get('error'): raise ValueError('rpc_error')
            account=(body.get('result') or {}).get('value')
            if not isinstance(account,dict): raise ValueError('missing_mint')
            r=requests.get('https://api.rugcheck.xyz/v1/tokens/'+mint+'/report',timeout=(3,8))
            r.raise_for_status(); report=r.json()
            result=assess(mint,pair,account,report)
            if result['status']=='pass':
                rent=requests.post(RPC,json={'jsonrpc':'2.0','id':2,'method':'getMinimumBalanceForRentExemption','params':[182]},timeout=(3,8))
                rent.raise_for_status(); lamports=rent.json().get('result')
                if type(lamports) is not int or lamports<=0: raise ValueError('rent_unavailable')
                result['metrics']['token_account_rent_lamports']=lamports
        except (requests.RequestException,ValueError,TypeError) as exc:
            result={'version':VERSION,'status':'unavailable','mint':mint,'pair':pair,'checked_at':now_ms(),
                    'reasons':['risk_data_unavailable'],'error_type':type(exc).__name__}
        p=_file(mint,pair); tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(result,allow_nan=False));tmp.replace(p)
        return result

def check(coin):
    mint=str(coin.get('address') or '');pair=str(coin.get('pairAddress') or '')
    if not ADDR.fullmatch(mint) or not ADDR.fullmatch(pair):
        return {'status':'blocked','reasons':['invalid_mint_or_pair']}
    block_path=Path(os.getenv('NEO_ENGINE_BLOCKLIST_PATH','/var/lib/neo-market/token_blocklist.json'))
    if block_path.exists():
        try:
            blocked=json.loads(block_path.read_text())
            if isinstance(blocked,dict): blocked=blocked.get('tokens')
            if not isinstance(blocked,list): raise ValueError('blocklist schema')
            if mint in blocked: return {'status':'blocked','reasons':['token_blocklisted']}
        except (OSError,ValueError,TypeError):
            return {'status':'unavailable','reasons':['blocklist_unavailable']}
    cached=_read(mint,pair)
    if cached: return cached
    key=(mint,pair)
    with _LOCK:
        for k,f in list(_PENDING.items()):
            if f.done(): _PENDING.pop(k,None)
        if key not in _PENDING and len(_PENDING)<6: _PENDING[key]=_POOL.submit(_fetch,mint,pair)
    return {'status':'pending','reasons':['risk_check_pending'],'mint':mint,'pair':pair}

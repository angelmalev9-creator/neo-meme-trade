#!/usr/bin/env python3
"""Publish ONLY an explicitly limited, non-sensitive PAPER shadow summary.

No keys, addresses, wallet information, user identity, source filesystem paths,
or even closed position identifiers are included in the public response.
This is not an exchange account or a live profit API.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ALLOWED_VARIANTS = ('NET_TARGET_5','NET_TARGET_10')
VERSION='NEO_SHADOW_PUBLIC_PAPER_V1'
FIELDS=('completed','mark_wins','mark_losses','unresolved','open_unverified',
        'mark_net_model_pnl_usd','virtual_mark_balance_usd',
        'win_rate_among_resolvable_pct','mark_target_pct','mark_stop_pct')

def sanitize(data:dict, now_ms:int)->dict:
    if data.get('version')!='NEO_SELECTIVE_EARLY_SHADOW_PAPER_V1':
        raise ValueError('unexpected report version')
    if data.get('mode')!='FORWARD_ONLY_FROM_START':
        raise ValueError('not a forward-only PAPER session')
    generated_at=data.get('generated_at_utc')
    if not isinstance(generated_at,str):
        raise ValueError('missing report timestamp')
    source_time=datetime.fromisoformat(generated_at.replace('Z','+00:00'))
    if source_time.tzinfo is None:
        raise ValueError('report timestamp is timezone-naive')
    age_ms=max(0,now_ms-int(source_time.timestamp()*1000))
    variants=data.get('variants')
    if not isinstance(variants,dict) or any(key not in variants for key in ALLOWED_VARIANTS):
        raise ValueError('report variants unavailable')
    safe_variants={}
    for name in ALLOWED_VARIANTS:
        record=variants[name]
        if not isinstance(record,dict):
            raise ValueError('invalid scenario')
        safe_variants[name]={k:record.get(k) for k in FIELDS}
    return {
      'version':VERSION,
      'research':'NEO Selective Early shadow mark-to-mark PAPER',
      'mode':'FORWARD_ONLY_FROM_START',
      'as_of':generated_at,
      'status':'updated' if age_ms < 180_000 else 'stale',
      'source_age_seconds':round(age_ms/1000),
      'market_events_read':data.get('market_events_read',0),
      'started_ms':data.get('started_ms'),
      'simulated_start_usd':1000.0,
      'orders_created':0,
      'realized_money_usd':0,
      'executable_swap_quote_verified':False,
      'mark_results_are_not_cash_profit':True,
      'cross_scenario_results_not_additive':True,
      'variants':safe_variants,
      'disclaimer':'Indicative DEX mark-price simulation with approximate modeled costs, not confirmed executable quotes, fills or real profit. Unresolved exits are not counted as wins; no real orders were placed.',
    }

def publish(source:Path,target:Path):
    with source.open('r',encoding='utf8') as stream:
        input_data=json.load(stream)
    output=sanitize(input_data,int(time.time()*1000))
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix('.json.tmp')
    with tmp.open('w',encoding='utf8') as stream:
        json.dump(output,stream,ensure_ascii=False,allow_nan=False,indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(tmp,0o644)
    os.replace(tmp,target)
    return output

if __name__=='__main__':
    if len(sys.argv)!=3:
        raise SystemExit('Usage: neo_shadow_public_report.py SOURCE REPORT')
    publish(Path(sys.argv[1]),Path(sys.argv[2]))

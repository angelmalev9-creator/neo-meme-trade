"""Read-only PAPER Solana market discovery intake and honest unique-count ledger.

Stream mints are *unverified leads*. They always pass through the existing
DexScreener market-data layer before appearing as market candidates. No changes
to GOLD entry, safety, sizing, exit, quote or execution policies.
"""
from __future__ import annotations
import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Callable

SOFIA=ZoneInfo('Europe/Sofia')
BASE58 = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def read_candidates(path: Path | str, *, now_ms: int | None = None,
                    limit: int = 5000, validator: Callable[[str], bool] = lambda x: bool(x)):
    stamp = int(time.time()*1000) if now_ms is None else int(now_ms)
    info = {'status': 'unavailable', 'stream_observed_total': 0,
            'stream_observed_window': 0, 'stream_listed': 0, 'stream_age_seconds': None}
    try:
        raw = json.loads(Path(path).read_text(encoding='utf8'))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return [], info
    if not isinstance(raw, dict) or raw.get('schema_version') != 1:
        return [], info
    updated = int(raw.get('updated_at') or 0)
    age = stamp-updated
    info.update(stream_observed_total=int(raw.get('unique_mints_since_start') or 0),
                stream_observed_window=int(raw.get('unique_mints_in_window') or 0),
                stream_listed=int(raw.get('listed_mints') or 0),
                stream_age_seconds=round(age/1000,1))
    if not raw.get('connected') or not 0<=age<=60000:
        info['status']='stale'
        return [], info
    rows=raw.get('tokens')
    if not isinstance(rows,list):
        return [], info
    out=[];seen=set()
    for row in rows[:max(1,limit)]:
        if not isinstance(row,dict):
            continue
        address=str(row.get('address') or '').strip()
        if address not in seen and validator(address):
            out.append(address)
            seen.add(address)
    info.update(status='online',stream_listed=len(out))
    return out,info


class DailyMarketLedger:
    """Distinguishes checked *addresses* from addresses with observed markets."""
    def __init__(self, path: Path | str, *, clock_ms: Callable[[], int] | None = None,
                 interval_ms: int = 45000):
        self.path=Path(path);self.clock=clock_ms or (lambda: int(time.time()*1000))
        self.interval_ms=interval_ms
        self.day=''
        self.requested=set()
        self.with_market=set()
        self.last_persist_at=0
        self._load()

    def _day(self,now):
        return datetime.fromtimestamp(now/1000,SOFIA).strftime('%Y-%m-%d')

    def _load(self):
        try:
            raw=json.loads(self.path.read_text(encoding='utf8'))
            if raw.get('day')==self._day(self.clock()):
                self.day=raw['day']
                self.requested=set(raw.get('requested') or [])
                self.with_market=set(raw.get('with_market') or [])
        except (FileNotFoundError,OSError,ValueError,TypeError,AttributeError):
            pass

    def observe(self, *, requested=(), with_market=(), now=None):
        stamp=self.clock() if now is None else int(now)
        today=self._day(stamp)
        if today!=self.day:
            self.day=today;self.requested.clear();self.with_market.clear()
        self.requested.update(str(x) for x in requested if x)
        self.with_market.update(str(x) for x in with_market if x)
        if stamp-self.last_persist_at>=self.interval_ms:
            self.persist(); self.last_persist_at=stamp
        return self.stats()

    def stats(self):
        return {'market_checked_unique_today':len(self.requested),
                'markets_validated_unique_today':len(self.with_market),
                'market_checked_day':self.day}

    def persist(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        body={'day':self.day,'requested':sorted(self.requested),
              'with_market':sorted(self.with_market)}
        fd,tmp=tempfile.mkstemp(prefix='.'+self.path.name+'.',suffix='.tmp',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf8') as f:
                json.dump(body,f,ensure_ascii=False,separators=(',',':'))
                f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path)
        finally:
            try:os.unlink(tmp)
            except FileNotFoundError:pass

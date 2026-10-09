#!/usr/bin/env python3
"""Persistent evidence that a Solana mint has one or more DEX pools.

This registry is informational only: it never supplies prices and never authorizes
an engine entry.  It exists so a temporary DexScreener failure cannot erase the
fact that a pool is already known from another free source or verified on-chain.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1


class DexRegistry:
    def __init__(self, path: Path | str, *, max_pools: int = 50_000,
                 persist_interval_ms: int = 5_000) -> None:
        self.path = Path(path)
        self.max_pools = max(100, int(max_pools))
        self.persist_interval_ms = max(1000, int(persist_interval_ms))
        self.lock = threading.RLock()
        self.pools: dict[str, dict[str, Any]] = {}
        self.known_mints: set[str] = set()
        self.updated_at = 0
        self.last_persisted_at = 0
        self.dirty = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict) or int(raw.get('schema_version') or 0) != SCHEMA_VERSION:
            return
        for row in (raw.get('pools') or [])[-self.max_pools:]:
            if not isinstance(row, dict):
                continue
            pair, mint = str(row.get('pair') or '').strip(), str(row.get('mint') or '').strip()
            if not pair or not mint:
                continue
            self.pools[pair] = {
                'pair': pair, 'mint': mint, 'dex': str(row.get('dex') or 'unknown').lower(),
                'sources': list(dict.fromkeys(str(x) for x in (row.get('sources') or []) if x)),
                'first_seen': int(row.get('first_seen') or 0), 'last_seen': int(row.get('last_seen') or 0),
                'onchain_verified': bool(row.get('onchain_verified')),
            }
        self.known_mints = {str(row['mint']) for row in self.pools.values() if row.get('mint')}
        self.updated_at = int(raw.get('updated_at') or 0)
        self.last_persisted_at = self.updated_at

    def _payload(self) -> dict[str, Any]:
        rows = sorted(self.pools.values(), key=lambda row: int(row.get('last_seen') or 0))
        return {'schema_version': SCHEMA_VERSION, 'updated_at': self.updated_at,
                'pools': rows[-self.max_pools:]}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(self._payload(), handle, ensure_ascii=False, separators=(',', ':'))
            os.replace(tmp, self.path)
        finally:
            try:
                if os.path.exists(tmp): os.unlink(tmp)
            except OSError:
                pass
        self.last_persisted_at = self.updated_at
        self.dirty = False

    def observe(self, mint: str, pair: str, dex: str, source: str, *,
                observed_at: int | None = None, onchain_verified: bool = False,
                persist: bool = True) -> bool:
        mint, pair = str(mint or '').strip(), str(pair or '').strip()
        if not mint or not pair:
            return False
        stamp = int(observed_at or time.time() * 1000)
        dex = str(dex or 'unknown').strip().lower() or 'unknown'
        source = str(source or 'unknown').strip() or 'unknown'
        with self.lock:
            row = self.pools.get(pair)
            created = row is None
            if row is None:
                row = {'pair': pair, 'mint': mint, 'dex': dex, 'sources': [],
                       'first_seen': stamp, 'last_seen': stamp, 'onchain_verified': False}
                self.pools[pair] = row
            old_mint = str(row.get('mint') or '')
            row['mint'] = mint
            self.known_mints.add(mint)
            if old_mint and old_mint != mint and not any(item.get('mint') == old_mint for key, item in self.pools.items() if key != pair):
                self.known_mints.discard(old_mint)
            if dex != 'unknown' or not row.get('dex'):
                row['dex'] = dex
            if source not in row['sources']:
                row['sources'].append(source)
            row['last_seen'] = max(int(row.get('last_seen') or 0), stamp)
            row['onchain_verified'] = bool(row.get('onchain_verified')) or bool(onchain_verified)
            self.updated_at = max(self.updated_at, stamp)
            self.dirty = True
            if len(self.pools) > self.max_pools:
                keep = sorted(self.pools.values(), key=lambda item: int(item.get('last_seen') or 0))[-self.max_pools:]
                self.pools = {item['pair']: item for item in keep}
                self.known_mints = {str(item['mint']) for item in keep if item.get('mint')}
        if persist:
            self.flush_if_due(stamp)
        return created

    def observe_market_pairs(self, pairs: Iterable[dict[str, Any]], *, source: str,
                             observed_at: int | None = None) -> int:
        added = 0
        stamp = int(observed_at or time.time() * 1000)
        for raw in pairs:
            if not isinstance(raw, dict):
                continue
            mint = str((raw.get('baseToken') or {}).get('address') or raw.get('address') or '').strip()
            pair = str(raw.get('pairAddress') or raw.get('pair') or '').strip()
            if self.observe(mint, pair, str(raw.get('dexId') or raw.get('dex') or 'unknown'), source,
                            observed_at=stamp, persist=False):
                added += 1
        self.flush_if_due(stamp)
        return added

    def observe_tape(self, tape: dict[str, Any]) -> int:
        added, stamp = 0, int(time.time() * 1000)
        for event in (tape.get('events') or []):
            if not isinstance(event, dict):
                continue
            mint, pair = str(event.get('address') or '').strip(), str(event.get('pairAddress') or '').strip()
            if not mint or not pair:
                continue
            event_at = int(event.get('ingested_at') or event.get('available_at') or event.get('ts') or stamp)
            if self.observe(mint, pair, 'pumpswap', 'live-tape-verified', observed_at=event_at,
                            onchain_verified=True, persist=False):
                added += 1
        self.flush_if_due(stamp)
        return added

    def flush_if_due(self, now: int | None = None, *, force: bool = False) -> None:
        stamp = int(now or time.time() * 1000)
        with self.lock:
            if not self.dirty or (not force and stamp - self.last_persisted_at < self.persist_interval_ms):
                return
            self.updated_at = max(self.updated_at, stamp)
            try:
                self._save()
            except OSError:
                pass

    def has_dex(self, mint: str) -> bool:
        with self.lock:
            return str(mint or '').strip() in self.known_mints

    def pairs_for(self, mint: str, limit: int = 20) -> list[dict[str, Any]]:
        needle = str(mint or '').strip()
        with self.lock:
            rows = [dict(row) for row in self.pools.values() if row.get('mint') == needle]
        rows.sort(key=lambda row: int(row.get('last_seen') or 0), reverse=True)
        return rows[:max(1, int(limit))]

    def stats(self) -> dict[str, Any]:
        with self.lock:
            rows = list(self.pools.values())
            verified = {str(row.get('mint')) for row in rows if row.get('mint') and row.get('onchain_verified')}
            dexes: dict[str, int] = {}
            for row in rows:
                dex = str(row.get('dex') or 'unknown')
                dexes[dex] = dexes.get(dex, 0) + 1
            return {'tokens': len(self.known_mints), 'pools': len(rows),
                    'onchain_verified_tokens': len(verified), 'dexes': dexes,
                    'updated_at': self.updated_at}

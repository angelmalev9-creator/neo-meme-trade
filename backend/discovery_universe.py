#!/usr/bin/env python3
"""Persistent rotating token universe for PAPER market discovery.

This module only decides which already-discovered token addresses are refreshed on
an engine scan. It does not change entry, exit, scoring, sizing or execution.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 1


def seed_addresses_from_json(paths: Iterable[Path | str]) -> list[str]:
    """Collect address-like fields from existing PAPER state for one-time seeding."""
    found: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        try:
            payload = json.loads(Path(raw_path).read_text(encoding='utf-8'))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {'address', 'tokenAddress', 'mint'} and isinstance(child, str):
                        address = child.strip()
                        if 32 <= len(address) <= 44 and address not in seen:
                            seen.add(address); found.append(address)
                    if isinstance(child, (dict, list)):
                        stack.append(child)
            elif isinstance(value, list):
                stack.extend(value)
    return found


def fresh_priority(current: Iterable[str], previous: Iterable[str] = (), urgent: Iterable[str] = ()) -> list[str]:
    """Return only genuinely new discovery addresses, with urgent pools first.

    The full provider snapshot must not be treated as priority on every scan or
    it can starve the rotating universe. Urgent addresses (for example brand-new
    pools) stay first even when they were present in the previous snapshot.
    """
    previous_set = {str(value or '').strip() for value in previous if str(value or '').strip()}
    selected: list[str] = []
    seen: set[str] = set()
    for rows, only_new in ((urgent, False), (current, True)):
        for raw in rows:
            address = str(raw or '').strip()
            if not address or address in seen or (only_new and address in previous_set):
                continue
            selected.append(address)
            seen.add(address)
    return selected


class RollingUniverse:
    def __init__(self, path: Path | str, *, max_items: int = 5000,
                 ttl_ms: int = 24 * 60 * 60 * 1000, batch_size: int = 240,
                 clock_ms: Callable[[], int] | None = None,
                 writer: Callable[[Path, Any], None] | None = None,
                 persist_interval_ms: int = 15_000) -> None:
        if max_items < 1 or batch_size < 1 or ttl_ms < 1:
            raise ValueError('invalid discovery universe configuration')
        self.path = Path(path)
        self.max_items = int(max_items)
        self.ttl_ms = int(ttl_ms)
        self.batch_size = min(int(batch_size), self.max_items)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.writer = writer
        self.persist_interval_ms = max(1000, int(persist_interval_ms))
        self.last_persisted_at = 0
        self.lock = threading.RLock()
        self.items: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.cursor = 0
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or int(raw.get('schema_version') or 0) != SCHEMA_VERSION:
                return
            rows = raw.get('items') or []
            if not isinstance(rows, list):
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue
                address = str(row.get('address') or '').strip()
                if not address or address in self.items:
                    continue
                self.items[address] = {
                    'last_seen': int(row.get('last_seen') or 0),
                    'metadata': dict(row.get('metadata') or {}),
                }
                self.order.append(address)
            self.cursor = max(0, int(raw.get('cursor') or 0))
            self._prune(self.clock_ms())
        except (FileNotFoundError, ValueError, TypeError, OSError, json.JSONDecodeError):
            return

    def _payload(self) -> dict[str, Any]:
        return {
            'schema_version': SCHEMA_VERSION,
            'cursor': self.cursor,
            'items': [
                {'address': address, 'last_seen': self.items[address]['last_seen'],
                 'metadata': self.items[address].get('metadata') or {}}
                for address in self.order if address in self.items
            ],
        }

    def _save(self) -> None:
        if self.writer is not None:
            self.writer(self.path, self._payload())
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp.write_text(json.dumps(self._payload(), ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.path)

    def _prune(self, now: int) -> None:
        keep = [address for address in self.order
                if address in self.items and 0 <= now - int(self.items[address].get('last_seen') or 0) <= self.ttl_ms]
        if len(keep) > self.max_items:
            keep = keep[-self.max_items:]
        allowed = set(keep)
        self.items = {address: self.items[address] for address in keep if address in self.items}
        self.order = keep
        self.cursor = 0 if not keep else self.cursor % len(keep)

    @staticmethod
    def _merge_metadata(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        merged = dict(old or {})
        incoming = dict(new or {})
        old_sources = list(merged.get('sources') or [])
        for source in incoming.get('sources') or []:
            if source not in old_sources:
                old_sources.append(source)
        if old_sources:
            merged['sources'] = old_sources
        for key, value in incoming.items():
            if key == 'sources':
                continue
            if value not in (None, '', [], {}):
                merged[key] = value
        return merged

    def observe(self, addresses: Iterable[str], metadata: dict[str, dict[str, Any]] | None = None,
                *, now: int | None = None, persist: bool = True) -> int:
        stamp = int(self.clock_ms() if now is None else now)
        metadata = metadata or {}
        added = 0
        with self.lock:
            for raw in addresses:
                address = str(raw or '').strip()
                if not address:
                    continue
                if address not in self.items:
                    self.items[address] = {'last_seen': stamp, 'metadata': {}}
                    self.order.append(address)
                    added += 1
                row = self.items[address]
                row['last_seen'] = stamp
                row['metadata'] = self._merge_metadata(row.get('metadata') or {}, metadata.get(address) or {})
            self._prune(stamp)
            if persist and stamp - self.last_persisted_at >= self.persist_interval_ms:
                self._save(); self.last_persisted_at = stamp
        return added

    def next_batch(self, *, priority: Iterable[str] = (), limit: int | None = None,
                   now: int | None = None) -> tuple[list[str], dict[str, dict[str, Any]]]:
        stamp = int(self.clock_ms() if now is None else now)
        cap = min(self.max_items, max(1, int(limit or self.batch_size)))
        with self.lock:
            self._prune(stamp)
            selected: list[str] = []
            selected_set: set[str] = set()
            for raw in priority:
                address = str(raw or '').strip()
                if address and address in self.items and address not in selected_set:
                    selected.append(address); selected_set.add(address)
                    if len(selected) >= cap:
                        break
            if self.order and len(selected) < cap:
                visited = 0
                index = self.cursor % len(self.order)
                while visited < len(self.order) and len(selected) < cap:
                    address = self.order[index]
                    if address not in selected_set:
                        selected.append(address); selected_set.add(address)
                    index = (index + 1) % len(self.order)
                    visited += 1
                self.cursor = index
            meta = {address: dict((self.items.get(address) or {}).get('metadata') or {}) for address in selected}
            return selected, meta

    def stats(self) -> dict[str, int]:
        with self.lock:
            return {'size': len(self.order), 'cursor': self.cursor, 'max_items': self.max_items,
                    'batch_size': self.batch_size, 'ttl_ms': self.ttl_ms}

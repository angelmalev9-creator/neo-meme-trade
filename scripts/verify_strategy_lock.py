#!/usr/bin/env python3
"""Verify strategy-lock.json without Node (same rule as verify-strategy-lock.mjs).

Used by the VPS deploy, where Node may not be installed.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path, hash_basis: str | None) -> str:
    data = path.read_bytes()
    if hash_basis == 'UTF8_LF':
        data = data.decode('utf-8').replace('\r\n', '\n').encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    lock = json.loads((ROOT / 'strategy-lock.json').read_text(encoding='utf-8'))
    basis = lock.get('hash_basis')
    if basis not in (None, 'UTF8_LF'):
        print('Unsupported strategy lock hash basis', file=sys.stderr)
        return 1
    expected = {lock['strategy_file']: lock['sha256'], **(lock.get('support_files_sha256') or {})}
    for name, want in expected.items():
        if digest(ROOT / name, basis) != want:
            print(f'STRATEGY LOCK FAILED: {name} changed.', file=sys.stderr)
            return 1
    print(f"STRATEGY LOCK OK: {lock['production_strategy_id']} {lock['sha256']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

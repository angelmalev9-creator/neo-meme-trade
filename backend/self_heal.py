#!/usr/bin/env python3
"""NEO PAPER runtime watchdog.

Operational recovery only: restarts a stuck account monitor / tape recorder and
reports provider backoff. It never edits strategy thresholds, balances, trades,
position state, sizing, stops or take-profit rules.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

ENGINE_URL = os.getenv('NEO_SELF_HEAL_ENGINE_URL', 'http://127.0.0.1:18804/state')
ENGINE_SERVICE = os.getenv('NEO_SELF_HEAL_ENGINE_SERVICE', 'neo-user-angel-paper.service')
TAPE_SERVICE = os.getenv('NEO_SELF_HEAL_TAPE_SERVICE', 'neo-live-tape-angel.service')
STATUS_PATH = Path(os.getenv('NEO_SELF_HEAL_STATUS_PATH', '/var/lib/neo-market/users/42d3192d-f033-4061-85d6-2408c5e168e7/self_heal.json'))
POLL_SECONDS = max(5.0, float(os.getenv('NEO_SELF_HEAL_POLL_SECONDS', '10')))
ENGINE_STALE_SECONDS = max(20.0, float(os.getenv('NEO_SELF_HEAL_ENGINE_STALE_SECONDS', '45')))
TAPE_STALE_SECONDS = max(15.0, float(os.getenv('NEO_SELF_HEAL_TAPE_STALE_SECONDS', '35')))
BAD_CHECKS = max(2, int(os.getenv('NEO_SELF_HEAL_BAD_CHECKS', '3')))
RESTART_COOLDOWN_SECONDS = max(30.0, float(os.getenv('NEO_SELF_HEAL_RESTART_COOLDOWN_SECONDS', '120')))

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NEO-Self-Heal/1.0', 'Accept': 'application/json'})


def now_ms() -> int:
    return int(time.time() * 1000)


def read_status() -> dict[str, Any]:
    try:
        data = json.loads(STATUS_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_status(status: str, component: str | None, action: str | None, detail: str | None) -> None:
    old = read_status()
    signature = (status, component, action, detail)
    old_signature = (old.get('status'), old.get('component'), old.get('action'), old.get('detail'))
    if signature == old_signature:
        return
    recoveries = int(old.get('recoveries') or 0)
    if status == 'healthy' and old.get('status') not in (None, 'healthy'):
        recoveries += 1
    payload = {
        'status': status,
        'component': component,
        'action': action,
        'detail': detail,
        'last_action_at': now_ms(),
        'recoveries': recoveries,
        'watchdog': 'NEO_SELF_HEAL_V1',
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + STATUS_PATH.name + '.', suffix='.tmp', dir=STATUS_PATH.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, STATUS_PATH)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def restart(service: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ['systemctl', 'restart', service], capture_output=True, text=True,
            timeout=20, check=False,
        )
        if result.returncode == 0:
            return True, 'restart successful'
        return False, (result.stderr or result.stdout or f'exit {result.returncode}').strip()[:300]
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'[:300]


def fetch_state() -> dict[str, Any]:
    response = SESSION.get(ENGINE_URL, timeout=(1.0, 3.0))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise TypeError('engine state is not an object')
    return data


def main() -> int:
    engine_bad = 0
    tape_bad = 0
    last_engine_restart = 0.0
    last_tape_restart = 0.0
    write_status('healthy', None, 'watchdog_started', 'NEO self-heal watchdog is active')

    while True:
        started = time.monotonic()
        try:
            state = fetch_state()
            current_ms = now_ms()
            last_scan = int(state.get('last_scan_at') or 0)
            scan_age = (current_ms - last_scan) / 1000.0 if last_scan else 999999.0
            engine_ok = bool(state.get('running')) and state.get('status') != 'error' and scan_age <= ENGINE_STALE_SECONDS
            engine_bad = 0 if engine_ok else engine_bad + 1

            health = state.get('runtime_health') or {}
            flow = health.get('order_flow') or {}
            flow_checked = int(flow.get('checked_at') or 0)
            flow_age = (current_ms - flow_checked) / 1000.0 if flow_checked else 999999.0
            flow_status = str(flow.get('status') or 'offline').lower()
            tape_ok = flow_status in {'online', 'warming'} and flow_age <= TAPE_STALE_SECONDS
            tape_bad = 0 if tape_ok else tape_bad + 1

            if engine_bad >= BAD_CHECKS and time.monotonic() - last_engine_restart >= RESTART_COOLDOWN_SECONDS:
                detail = f'engine stale/error for {engine_bad} checks; last scan age {scan_age:.0f}s'
                write_status('recovering', 'engine', 'restart_engine_service', detail)
                ok, result = restart(ENGINE_SERVICE)
                last_engine_restart = time.monotonic()
                engine_bad = 0
                write_status('recovering' if ok else 'error', 'engine', 'restart_engine_service', result)
            elif tape_bad >= BAD_CHECKS and time.monotonic() - last_tape_restart >= RESTART_COOLDOWN_SECONDS:
                detail = f'order-flow {flow_status}; age {flow_age:.0f}s for {tape_bad} checks'
                write_status('recovering', 'order_flow', 'restart_tape_service', detail)
                ok, result = restart(TAPE_SERVICE)
                last_tape_restart = time.monotonic()
                tape_bad = 0
                write_status('recovering' if ok else 'error', 'order_flow', 'restart_tape_service', result)
            else:
                market = health.get('market_data') or {}
                discovery = health.get('discovery') or {}
                provider_issues = []
                if str(market.get('status') or '').lower() in {'recovering', 'degraded', 'error'}:
                    provider_issues.append(f"market {market.get('status')}")
                if str(discovery.get('status') or '').lower() in {'recovering', 'degraded', 'error'}:
                    provider_issues.append(f"discovery {discovery.get('status')}")
                if provider_issues:
                    write_status('recovering', 'provider', 'adaptive_backoff_retry', ', '.join(provider_issues))
                elif engine_ok and tape_ok:
                    write_status('healthy', None, 'recovered', 'engine, discovery, market data and order-flow are healthy')
        except Exception as exc:
            engine_bad += 1
            if engine_bad >= BAD_CHECKS and time.monotonic() - last_engine_restart >= RESTART_COOLDOWN_SECONDS:
                write_status('recovering', 'engine', 'restart_engine_service', f'health endpoint unavailable: {type(exc).__name__}')
                ok, result = restart(ENGINE_SERVICE)
                last_engine_restart = time.monotonic()
                engine_bad = 0
                write_status('recovering' if ok else 'error', 'engine', 'restart_engine_service', result)
            else:
                write_status('recovering', 'engine', 'health_retry', f'{type(exc).__name__}: {exc}'[:240])

        elapsed = time.monotonic() - started
        time.sleep(max(0.5, POLL_SECONDS - elapsed))


if __name__ == '__main__':
    raise SystemExit(main())

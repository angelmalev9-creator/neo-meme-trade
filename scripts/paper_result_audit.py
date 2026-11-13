#!/usr/bin/env python3
"""Read-only evidence audit for NEO PAPER results.

No trade entry/exit decisions, order execution, optimizers or forecasts.
Usage: python3 scripts/paper_result_audit.py /path/to/PAPER-state.json
"""
from __future__ import annotations
from collections import Counter, defaultdict
import json
import math
import statistics
import sys
from typing import Any

VERSION = 'NEO_PAPER_RESULT_AUDIT_V1'

def fnum(value: Any):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None

def valid_trade(row):
    if not isinstance(row, dict) or row.get('technical_voided'):
        return False
    return (fnum(row.get('closed_at')) or 0) > 0 and fnum(row.get('pnl_usd')) is not None and fnum(row.get('pnl_pct')) is not None

def audit(data: dict, max_trades: int = 100) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Expected object state')
    if not isinstance(max_trades, int) or max_trades < 1:
        raise ValueError('max_trades must be positive integer')
    raw = data.get('history') or []
    if not isinstance(raw, list):
        raise ValueError('history must be an array')
    rows = sorted((r for r in raw if valid_trade(r)),
                  key=lambda r: fnum(r['closed_at']) or 0, reverse=True)[:max_trades]
    pnls = [fnum(r['pnl_usd']) for r in rows]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    profit = sum(wins)
    gross_loss = -sum(losses)
    avg_win = statistics.mean(wins) if wins else None
    avg_loss = statistics.mean(losses) if losses else None
    breakeven = ((gross_loss / len(losses)) / ((profit / len(wins)) + (gross_loss / len(losses)))) if wins and losses else None
    reasons = Counter(str(r.get('exit_reason') or 'UNKNOWN') for r in rows)
    early = []
    overshoots = []
    costs = []
    pairs = defaultdict(list)
    missing_quote_proof = 0
    for r in rows:
        opened = fnum(r.get('opened_at'))
        closed = fnum(r.get('closed_at'))
        if opened is not None and closed is not None and 0 <= closed - opened < 10000:
            early.append(r)
        stop = fnum(r.get('stop_loss_pct'))
        if stop is not None and stop > 0 and fnum(r['pnl_pct']) < -stop - 0.25:
            overshoots.append(r)
        initial = fnum(r.get('entry_roundtrip_pnl_pct'))
        if initial is not None:
            costs.append(initial)
        mint = str(r.get('address') or '')
        pool = str(r.get('pairAddress') or '')
        if mint and pool:
            pairs[(mint, pool, str(r.get('symbol') or ''))].append(r)
        if r.get('execution_mode') in ('JUPITER_QUOTE_V2', 'PUMPSWAP_RPC_ENTRY_V1') and not isinstance(r.get('exit_quote'), dict):
            missing_quote_proof += 1
    repeated = []
    for (mint, pair, symbol), trades in pairs.items():
        if len(trades) > 1:
            repeated.append({
                'symbol': symbol, 'mint': mint, 'pool': pair, 'trades': len(trades),
                'net_usd': round(sum(float(r['pnl_usd']) for r in trades), 4),
                'wins': sum(float(r['pnl_usd']) > 0 for r in trades),
            })
    repeated.sort(key=lambda x: (-x['trades'], x['symbol']))
    by_reason = [{
        'reason': key, 'trades': number,
        'net_usd': round(sum(float(r['pnl_usd']) for r in rows if str(r.get('exit_reason') or 'UNKNOWN') == key), 4),
    } for key, number in reasons.most_common()]
    worst = min(overshoots, key=lambda t: float(t['pnl_pct'])) if overshoots else None
    return {
        'version': VERSION, 'mode': 'READ_ONLY_PAPER_AUDIT',
        'validated_profitable_strategy': False,
        'trades_in_sample': len(rows),
        'historical_rows_outside_sample_or_invalid': len(raw) - len(rows),
        'net_pnl_usd': round(sum(pnls), 4),
        'wins': len(wins), 'losses': len(losses),
        'win_rate_pct': round(100 * len(wins) / len(rows), 4) if rows else None,
        'gross_profit_usd': round(profit, 4), 'gross_loss_usd': round(gross_loss, 4),
        'profit_factor': round(profit / gross_loss, 4) if gross_loss else None,
        'average_win_usd': round(avg_win, 4) if avg_win is not None else None,
        'average_loss_usd': round(avg_loss, 4) if avg_loss is not None else None,
        'break_even_win_rate_given_sample_payout_pct': round(100 * breakeven, 3) if breakeven is not None else None,
        'fast_closes_under_10_seconds': len(early),
        'fast_closes_net_usd': round(sum(float(r['pnl_usd']) for r in early), 4),
        'stop_overshoots_more_than_0_25pp': len(overshoots),
        'worst_stop_overshoot': ({k: worst.get(k) for k in ('symbol', 'pnl_pct', 'stop_loss_pct', 'exit_reason')} if worst else None),
        'initial_roundtrip_mark_median_pct': round(statistics.median(costs), 4) if costs else None,
        'initial_roundtrip_mark_average_pct': round(statistics.mean(costs), 4) if costs else None,
        'initial_roundtrip_mark_samples': len(costs),
        'quote_based_records_without_exit_quote_dict': missing_quote_proof,
        'top_repeat_pools': repeated[:12], 'exit_reasons': by_reason,
        'reporting_note': 'PAPER history is observational, not proof of guaranteed execution. UTC risk-day can differ from Sofia report day. Historical versions can coexist.',
        'interpretation': 'Historical outcomes only: no validated trade signal or real-money deployment.',
    }

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Usage: python3 scripts/paper_result_audit.py /path/to/state.json', file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], encoding='utf8') as inp:
        state = json.load(inp)
    print(json.dumps(audit(state), indent=2, ensure_ascii=False))

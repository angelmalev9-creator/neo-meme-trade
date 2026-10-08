"""Small read-only dashboard projection of Strategy Lab state."""

WHY_QUIET_TOP = 6


def why_quiet(book):
    """Which entry conditions have kept this book out of the market, cumulatively."""
    total = book.get('rejection_totals') if isinstance(book, dict) else None
    if not isinstance(total, dict):
        return None
    candidates = max(0, int(_finite(total.get('candidates')) or 0))
    reasons = total.get('reasons') if isinstance(total.get('reasons'), dict) else {}
    rows = sorted(((str(k), int(_finite(v) or 0)) for k, v in reasons.items()), key=lambda kv: -kv[1])
    return {
        'since': _finite(total.get('since')), 'scans': int(_finite(total.get('scans')) or 0),
        'candidates': candidates, 'last_rule_match_at': _finite(total.get('last_rule_match_at')),
        'reasons': [{'reason': k, 'count': v, 'share': round(v / candidates, 4) if candidates else None}
                    for k, v in rows[:WHY_QUIET_TOP] if v > 0],
    }

def compact_strategy_lab(data):
    if not isinstance(data, dict):
        return {'status': 'offline', 'books': {}, 'stats': {}}

    books = {}
    for key, raw in (data.get('books') or {}).items():
        if not isinstance(raw, dict):
            continue
        position = raw.get('position')
        if isinstance(position, dict):
            position = {
                field: position.get(field)
                for field in ('symbol', 'address', 'strategy_id', 'opened_at', 'pnl_pct', 'notional_usd')
            }
        else:
            position = None
        books[key] = {
            'id': raw.get('id', key),
            'name': raw.get('name', key),
            'starting_balance': raw.get('starting_balance', 0),
            'balance': raw.get('balance', 0),
            'position': position,
            'history': [],
            'why_quiet': why_quiet(raw),
        }

    result = {
        'started_at': data.get('started_at'),
        'updated_at': data.get('updated_at'),
        'status': data.get('status', 'offline'),
        'books': books,
        'stats': data.get('stats') or {},
        'data_integrity_note': data.get('data_integrity_note'),
        'activity_config': data.get('activity_config') or {},
    }

    paired = data.get('paired')
    if isinstance(paired, dict):
        result['paired'] = paired

    return result


_BOOK_ID_MAX = 64
TRADE_LIMIT = 500


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and number not in (float('inf'), float('-inf')) else None


def _trade_row(trade, open_position=False):
    opened, closed = _finite(trade.get('opened_at')), _finite(trade.get('closed_at'))
    last_seen = closed if not open_position else _finite(trade.get('updated_at'))
    hold = (last_seen - opened) / 1000 if opened and last_seen and last_seen >= opened else None
    return {
        'trade_no': trade.get('trade_no'), 'symbol': trade.get('symbol'), 'address': trade.get('address'),
        'open': open_position,
        'opened_at': opened, 'closed_at': None if open_position else closed,
        'hold_seconds': hold,
        # Chart price at the decision, and the simulated fill that was booked.
        'entry_price': _finite(trade.get('entry_price')),
        'execution_entry_price': _finite(trade.get('execution_entry_price')),
        'exit_price': None if open_position else _finite(trade.get('exit_price')),
        'execution_exit_price': None if open_position else _finite(trade.get('execution_exit_price')),
        'current_price': _finite(trade.get('current_price')) if open_position else None,
        'notional_usd': _finite(trade.get('notional_usd')),
        'pnl_usd': _finite(trade.get('open_pnl_usd') if open_position else trade.get('pnl_usd')),
        'pnl_pct': _finite(trade.get('pnl_pct')),
        'exit_reason': None if open_position else trade.get('exit_reason'),
        # The mark seen just before the exit and how long before: a stop that
        # booked far past its level shows here as a jump, not as a late check.
        'observed_exit_pnl_pct': None if open_position else _finite(trade.get('observed_exit_pnl_pct')),
        'pre_exit_pnl_pct': None if open_position else _finite(trade.get('pre_exit_pnl_pct')),
        'pre_exit_gap_seconds': None if open_position else _finite(trade.get('pre_exit_gap_seconds')),
        'exit_fill_model': None if open_position else trade.get('exit_fill_model'),
    }


def book_trades(data, book_id, limit=TRADE_LIMIT):
    """Every trade of one Strategy Lab book, newest first, for the dashboard drop-down."""
    book_id = str(book_id or '')
    books = (data.get('books') or {}) if isinstance(data, dict) else {}
    book = books.get(book_id) if 0 < len(book_id) <= _BOOK_ID_MAX else None
    if not isinstance(book, dict):
        return {'found': False, 'id': book_id[:_BOOK_ID_MAX], 'trades': [], 'total': 0, 'shown': 0}
    history = [t for t in (book.get('history') or []) if isinstance(t, dict)]
    history.sort(key=lambda t: _finite(t.get('closed_at')) or 0, reverse=True)
    rows = [_trade_row(t) for t in history[:max(0, int(limit))]]
    position = book.get('position')
    if isinstance(position, dict):
        rows.insert(0, _trade_row(position, open_position=True))
    return {'found': True, 'id': book_id, 'name': book.get('name', book_id), 'trades': rows,
            'total': len(history), 'shown': min(len(history), max(0, int(limit))),
            'updated_at': data.get('updated_at')}

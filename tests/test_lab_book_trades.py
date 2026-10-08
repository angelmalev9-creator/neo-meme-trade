"""Offline tests for the per-strategy trade list behind the dashboard drop-down."""
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import market_monitor as m
from lab_dashboard_projection import book_trades, compact_strategy_lab

T0 = 1_800_000_000_000


def trade(no, opened, held_ms, **extra):
    return {'trade_no': no, 'symbol': f'C{no}', 'address': 'A' * 44, 'opened_at': opened, 'closed_at': opened + held_ms,
            'entry_price': 1.0, 'execution_entry_price': 1.01, 'exit_price': 1.2, 'execution_exit_price': 1.19,
            'notional_usd': 150, 'pnl_usd': 25.5, 'pnl_pct': 17.0, 'exit_reason': 'TAKE_PROFIT_17_NET',
            'entry_features': {'big': 'x' * 500}, **extra}


LAB = {'updated_at': T0, 'books': {
    'TIKTOK': {'id': 'TIKTOK', 'name': 'TikTok Strategy', 'balance': 520,
               'history': [trade(2, T0 + 4_000_000, 134_000), trade(1, T0, 3_725_000, pnl_usd=-18, pnl_pct=-12.0,
                                                                  exit_reason='STOP_LOSS_12_NET')],
               'position': {'trade_no': 3, 'symbol': 'OPEN', 'address': 'B' * 44, 'opened_at': T0 + 4_200_000,
                            'updated_at': T0 + 4_205_000, 'entry_price': 2.0, 'execution_entry_price': 2.02,
                            'current_price': 2.1, 'notional_usd': 150, 'open_pnl_usd': 3.2, 'pnl_pct': 2.1}},
    'SCALPER': {'id': 'SCALPER', 'name': 'Fast Scalper 3/10', 'history': [], 'position': None},
}}


class Projection(unittest.TestCase):
    def test_every_trade_with_entry_exit_and_hold_newest_first(self):
        view = book_trades(LAB, 'TIKTOK')
        self.assertEqual((view['found'], view['name'], view['total'], view['shown']), (True, 'TikTok Strategy', 2, 2))
        open_row, newest, oldest = view['trades']
        self.assertEqual((newest['trade_no'], oldest['trade_no']), (2, 1))
        self.assertEqual(newest, {
            'trade_no': 2, 'symbol': 'C2', 'address': 'A' * 44, 'open': False,
            'opened_at': T0 + 4_000_000, 'closed_at': T0 + 4_134_000, 'hold_seconds': 134.0,
            'entry_price': 1.0, 'execution_entry_price': 1.01, 'exit_price': 1.2, 'execution_exit_price': 1.19,
            'current_price': None, 'notional_usd': 150.0, 'pnl_usd': 25.5, 'pnl_pct': 17.0,
            'exit_reason': 'TAKE_PROFIT_17_NET'})
        self.assertEqual((oldest['hold_seconds'], oldest['exit_reason'], oldest['pnl_pct']), (3725.0, 'STOP_LOSS_12_NET', -12.0))
        self.assertEqual((open_row['open'], open_row['closed_at'], open_row['exit_price'], open_row['current_price'],
                          open_row['hold_seconds'], open_row['pnl_usd'], open_row['exit_reason']),
                         (True, None, None, 2.1, 5.0, 3.2, None))

    def test_empty_unknown_and_malformed_books(self):
        self.assertEqual(book_trades(LAB, 'SCALPER')['trades'], [])
        for bad in ('NOPE', '', None, 'X' * 65, '../state'):
            view = book_trades(LAB, bad)
            self.assertEqual((view['found'], view['trades'], view['total']), (False, [], 0))
        for data in ({}, None, [], {'books': None}, {'books': {'TIKTOK': 'broken'}}):
            self.assertFalse(book_trades(data, 'TIKTOK')['found'])

    def test_limit_keeps_the_newest_and_reports_the_total(self):
        many = {'books': {'B': {'history': [trade(i, T0 + i * 1000, 500) for i in range(30)]}}}
        view = book_trades(many, 'B', limit=10)
        self.assertEqual((view['total'], view['shown'], len(view['trades'])), (30, 10, 10))
        self.assertEqual([row['trade_no'] for row in view['trades']], list(range(29, 19, -1)))

    def test_bad_numbers_never_reach_the_page(self):
        odd = {'books': {'B': {'history': [trade(1, T0, 1000, entry_price=float('nan'), pnl_usd=float('inf'),
                                                 execution_exit_price='oops', closed_at=None), 'not-a-trade']}}}
        row, = book_trades(odd, 'B')['trades']
        self.assertEqual((row['entry_price'], row['pnl_usd'], row['execution_exit_price'], row['hold_seconds']),
                         (None, None, None, None))
        json.dumps(book_trades(odd, 'B'), allow_nan=False)

    def test_state_poll_stays_small(self):
        # The 2-second /state poll still carries no per-book history.
        self.assertEqual(compact_strategy_lab(LAB)['books']['TIKTOK']['history'], [])


class EngineAndGateway(unittest.TestCase):
    def test_engine_reads_the_lab_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'strategy_lab.json'
            path.write_text(json.dumps(LAB), encoding='utf-8')
            with patch.object(m, 'STRATEGY_LAB_PATH', path):
                self.assertEqual(len(m.read_lab_book('TIKTOK')['trades']), 3)
                self.assertFalse(m.read_lab_book('NOPE')['found'])
            with patch.object(m, 'STRATEGY_LAB_PATH', Path(tmp) / 'missing.json'):
                self.assertEqual(m.read_lab_book('TIKTOK'), {'found': False, 'id': 'TIKTOK', 'trades': [], 'total': 0, 'shown': 0})

    def test_gateway_route_requires_login_and_forwards_only_the_book_id(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'NEO_USER_STATE_PATH': str(Path(tmp) / 'a.json')}):
            import user_gateway
            gateway = importlib.reload(user_gateway)
            sent = []

            def handler(path, user):
                h = gateway.Handler.__new__(gateway.Handler)
                h.path = path
                h.authenticated = lambda: user
                h.json_response = lambda payload, status=200: sent.append((status, payload))
                return h

            with patch.object(gateway, 'proxy_user_engine', return_value={'found': True}) as proxy:
                handler('/user/lab-book?id=TIKTOK', {'id': 'u1'}).do_GET()
                self.assertEqual(proxy.call_args.args, ({'id': 'u1'}, 'GET', '/lab-book?id=TIKTOK'))
                handler('/user/lab-book?id=A%20B%26x%3D1%2F..&other=1', {'id': 'u1'}).do_GET()
                self.assertEqual(proxy.call_args.args[2], '/lab-book?id=A+B%26x%3D1%2F..')
                handler('/user/lab-book?id=' + 'Z' * 200, {'id': 'u1'}).do_GET()
                self.assertEqual(proxy.call_args.args[2], '/lab-book?id=' + 'Z' * 64)
                calls = proxy.call_count
                handler('/user/lab-book?id=TIKTOK', None).do_GET()         # not logged in
                self.assertEqual(proxy.call_count, calls)
            self.assertEqual(sent[0], (200, {'found': True}))


if __name__ == '__main__':
    unittest.main(verbosity=2)

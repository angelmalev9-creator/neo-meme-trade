import unittest

import coin_flow

NOW = 1_800_000_000_000
A, P = 'A' * 44, 'P' * 44


def event(ts, direction, wallet, usd=50, **extra):
    return {'ts': ts, 'direction': direction, 'wallet': wallet, 'usd_amount': usd, 'address': A, 'pairAddress': P, **extra}


class TapeWindows(unittest.TestCase):
    def test_counts_trades_and_distinct_wallets_per_window(self):
        events = [event(NOW - 10_000, 'BUY', 'w1'), event(NOW - 20_000, 'BUY', 'w1'), event(NOW - 30_000, 'SELL', 'w2'),
                  event(NOW - 200_000, 'BUY', 'w3'), event(NOW - 1_000_000, 'SELL', 'w4'),
                  event(NOW - 5_000, 'BUY', 'bad', quality_flags=['dup']), event(NOW - 5_000, 'BUY', 'zero', usd=0),
                  {**event(NOW - 5_000, 'BUY', 'other'), 'pairAddress': 'Q' * 44}]
        view = coin_flow.tape_windows(events, A, P, now=NOW)
        self.assertEqual(view['windows']['m1'], {'buys': 2, 'sells': 1, 'buyers': 1, 'sellers': 1, 'buy_usd': 100.0, 'sell_usd': 50.0, 'complete': True})
        self.assertEqual((view['windows']['m5']['buys'], view['windows']['m5']['buyers']), (3, 2))
        self.assertEqual((view['windows']['h1']['sells'], view['windows']['h1']['sellers']), (2, 2))
        # The tape reaches back 1000 s here, so 30 m and 1 h are only partly observed.
        self.assertEqual([view['windows'][k]['complete'] for k in ('m1', 'm5', 'm15', 'm30', 'h1')], [True, True, True, False, False])
        self.assertEqual(view['events'], 5)

    def test_empty_tape(self):
        view = coin_flow.tape_windows([], A, P, now=NOW)
        self.assertEqual(view['windows']['m1']['buys'], 0)
        self.assertEqual(view['covered_seconds'], 0)


class Gecko(unittest.TestCase):
    PAYLOAD = {'data': {'attributes': {
        'transactions': {'m5': {'buys': 4, 'sells': 1, 'buyers': 3, 'sellers': 1}, 'h1': {'buys': 40, 'sells': 25, 'buyers': 22, 'sellers': 14},
                         'h24': {'buys': '400', 'sells': 300, 'buyers': 120, 'sellers': 90}},
        'volume_usd': {'h1': '1234.5', 'h24': 99999}, 'price_change_percentage': {'h1': '12.5'},
        'pool_created_at': '2026-10-08T10:00:00Z', 'reserve_in_usd': '15000', 'market_cap_usd': None, 'fdv_usd': '42000'}}}

    def setUp(self):
        coin_flow._CACHE.clear()

    def test_parse(self):
        row = coin_flow.parse_gecko_pool(self.PAYLOAD)
        self.assertEqual(row['windows']['m5'], {'buys': 4, 'sells': 1, 'buyers': 3, 'sellers': 1})
        self.assertEqual(row['windows']['h24']['buys'], 400)
        self.assertEqual(row['windows']['m15'], {'buys': 0, 'sells': 0, 'buyers': 0, 'sellers': 0})
        self.assertEqual((row['volume_usd']['h1'], row['price_change_pct']['h1'], row['market_cap_usd']), (1234.5, 12.5, 42000))

    def test_cache_and_error_cache(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if len(calls) == 2:
                raise RuntimeError('rate limited')
            return self.PAYLOAD
        first = coin_flow.gecko_pool(P, fetch, now=NOW)
        self.assertEqual(first['windows']['m5']['buys'], 4)
        self.assertIs(coin_flow.gecko_pool(P, fetch, now=NOW + 5_000), first)
        failed = coin_flow.gecko_pool(P, fetch, now=NOW + 11_000)
        self.assertIn('rate limited', failed['error'])
        self.assertIs(coin_flow.gecko_pool(P, fetch, now=NOW + 20_000), failed)
        self.assertEqual(coin_flow.gecko_pool(P, fetch, now=NOW + 50_000)['error'], None)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0], coin_flow.GECKO_POOL_URL.format(pair=P))


class Build(unittest.TestCase):
    def setUp(self):
        coin_flow._CACHE.clear()

    def test_windows_follow_age_and_sources_are_labelled(self):
        coin = {'address': A, 'pairAddress': P, 'ageMinutes': 40, 'txns': {'m5': {'buys': 3, 'sells': 2}, 'h1': {'buys': 30, 'sells': 20}}}
        tape = {'status': 'online', 'updated_at': NOW, 'events': [event(NOW - 5_000, 'BUY', 'w1')], 'pair_coverage': {P: {'status': 'COMPLETE'}}}
        out = coin_flow.build(A, P, coin=coin, tape=tape, fetch=lambda url: Gecko.PAYLOAD, now=NOW)
        self.assertEqual(out['available_windows'], ['m1', 'm5', 'm15', 'm30'])
        self.assertEqual(out['tape']['windows']['m1']['buys'], 1)
        self.assertEqual(out['tape']['coverage'], 'COMPLETE')
        self.assertEqual(out['gecko']['windows']['h1']['buyers'], 22)
        self.assertEqual(out['dexscreener']['h1'], {'buys': 30, 'sells': 20})
        self.assertEqual(coin_flow.available_windows(None), ['m1', 'm5', 'm15', 'm30', 'h1', 'h6', 'h24'])
        self.assertEqual(coin_flow.available_windows(2), ['m1', 'm5'])
        self.assertEqual(coin_flow.available_windows(3000), ['m1', 'm5', 'm15', 'm30', 'h1', 'h6', 'h24'])

    def test_without_pair_gecko_is_skipped(self):
        out = coin_flow.build(A, '', coin=None, tape={'events': []}, fetch=lambda url: (_ for _ in ()).throw(AssertionError('no fetch')), now=NOW)
        self.assertEqual(out['gecko']['error'], 'no_pair')
        self.assertEqual(out['gecko']['windows'], {})


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import patch

import requests

import live_tape_oct4_compat as tape


class Oct4CompatRpcTests(unittest.TestCase):
    def test_publicnode_get_transaction_falls_back_to_single_call_batches(self):
        calls = [('getTransaction', ['a']), ('getTransaction', ['b'])]
        self.assertEqual(tape._rpc_cap('https://solana-rpc.publicnode.com', calls), 1)

    def test_rpc_batch_falls_back_only_for_failed_calls(self):
        calls = [('getSignaturesForAddress', ['pair-a']), ('getSignaturesForAddress', ['pair-b'])]
        seen = []
        def fake_request(url, subset):
            seen.append((url, len(subset)))
            if url == tape.RPC_URL:
                return [
                    {'id': 1, 'result': [{'signature': 'ok'}]},
                    {'id': 2, 'error': {'code': 429}},
                ]
            return [{'id': 1, 'result': [{'signature': 'fallback'}]}]
        with patch.object(tape, '_rpc_request', side_effect=fake_request):
            rows = tape.rpc_batch(calls)
        self.assertEqual(rows[0]['result'][0]['signature'], 'ok')
        self.assertEqual(rows[1]['result'][0]['signature'], 'fallback')
        self.assertEqual(seen[0], (tape.RPC_URL, 2))
        self.assertEqual(seen[1][1], 1)

    def test_null_transaction_result_is_not_retried(self):
        calls = [('getTransaction', ['sig'])]
        with patch.object(tape, '_rpc_request', return_value=[{'id': 1, 'result': None}]) as request:
            rows = tape.rpc_batch(calls)
        self.assertIsNone(rows[0]['result'])
        request.assert_called_once()



class Oct4CompatTransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        tape.SEEN.clear()
        tape.PENDING_TX.clear()
        tape.INITIALIZED_PAIRS.clear()
        tape.PAIR_STARTED.clear()
        tape.PAIR_LAST_POLL.clear()
        tape.PAIR_LATEST_EVENT.clear()
        tape.EVENTS.clear()

    def tearDown(self):
        self.setUp()

    def test_rpc_keeps_successful_chunks_before_later_429(self):
        class FakeResponse:
            def __init__(self, ok):
                self.ok = ok
                self.status_code = 200 if ok else 429
            def raise_for_status(self):
                if not self.ok:
                    r = requests.Response()
                    r.status_code = 429
                    raise requests.HTTPError('rate limited', response=r)
            def json(self):
                return [{'id': 1, 'result': {'blockTime': 1000}}]
        calls = [('getTransaction', ['sig1']), ('getTransaction', ['sig2'])]
        with patch.object(tape.SESSION, 'post', side_effect=[FakeResponse(True), FakeResponse(False)]):
            rows = tape._rpc_request('https://solana-rpc.publicnode.com', calls)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['result']['blockTime'], 1000)
        self.assertEqual(rows[1]['error']['code'], 'HTTP_429')

    def test_transaction_retries_after_provider_error(self):
        meta = {'pair': 'pool-1', 'address': 'mint-1', 'symbol': 'TEST', 'price': 1}
        def answer(signature):
            return [{'result': [{'signature': signature, 'slot': 123}]}]
        responses = [
            answer('seed'),
            answer('new'), [{'error': {'code': 'HTTP_429'}}],
            answer('new'), [{'result': {'blockTime': 1234}}],
        ]
        example = {'ts': 1_000_010, 'available_at': 1_000_010, 'usd_amount': 8.0,
                   'direction': 'BUY', 'wallet': 'wallet1', 'quality_flags': []}
        with patch.object(tape, 'feed_snapshot', return_value=[meta]), \
             patch.object(tape, 'rpc_batch', side_effect=responses), \
             patch.object(tape, 'parse_trade', return_value=example), \
             patch.object(tape, 'now_ms', return_value=1_000_010), \
             patch.object(tape, 'atomic_write'):
            tape.poll_once()
            failed = tape.poll_once()
            self.assertEqual(failed['transaction_rpc_errors'], 1)
            self.assertEqual(failed['backlog'], 1)
            self.assertEqual(failed['pair_coverage']['pool-1']['status'], 'UNKNOWN')
            succeeded = tape.poll_once()
        self.assertEqual(succeeded['backlog'], 0)
        self.assertEqual(succeeded['transactions_hydrated'], 1)
        self.assertEqual(len(tape.EVENTS), 1)

    def test_high_activity_signature_gap_restarts_coverage(self):
        meta = {'pair': 'pool-1', 'address': 'mint-1', 'symbol': 'TEST', 'price': 1}
        rows = lambda names: [{'result': [{'signature': x, 'slot': 1} for x in names]}]
        with patch.object(tape, 'feed_snapshot', return_value=[meta]), \
             patch.object(tape, 'rpc_batch', side_effect=[rows(['old1', 'old2']),
                                                          rows(['new1', 'new2', 'new3', 'new4']),
                                                          [{'error': {'code': 429}}]]), \
             patch.object(tape, 'now_ms', side_effect=[100_000, 100_001, 200_000, 200_001, 200_002]), \
             patch.object(tape, 'atomic_write'):
            tape.poll_once()
            result = tape.poll_once()
        self.assertEqual(tape.PAIR_GAPS['pool-1'], 200_000)
        self.assertEqual(result['pair_coverage']['pool-1']['status'], 'UNKNOWN')

    def test_pending_tx_marks_pair_incomplete(self):
        meta = {'pair': 'pool-1', 'address': 'mint-1', 'symbol': 'TEST', 'price': 1}
        tape.PAIR_STARTED['pool-1'] = 1
        tape.PAIR_LAST_POLL['pool-1'] = 120_001
        tape.PENDING_TX['sig'] = ('sig', 1, meta, 120_000)
        row = tape.coverage_snapshot([meta], 120_002)['pool-1']
        self.assertEqual(row['status'], 'UNKNOWN')
        self.assertEqual(row['backlog'], 1)
        self.assertEqual(row['reason'], 'OCT4_TX_BACKLOG')


class _StateResponse:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class Oct4CompatPrewarmTests(unittest.TestCase):
    def setUp(self):
        tape.TRACKED.clear()
        tape.INITIALIZED_PAIRS.clear()
        tape.PAIR_STARTED.clear()
        tape.PAIR_LAST_POLL.clear()
        tape.PAIR_LATEST_EVENT.clear()

    def coin(self, symbol, *, score=50, liquidity=9000, age=40, m5=4, buys=20, sells=10, volume=2500):
        return {
            'address': f'addr-{symbol}', 'pairAddress': f'pair-{symbol}', 'symbol': symbol,
            'priceUsd': 0.001, 'score': score, 'liquidityUsd': liquidity, 'ageMinutes': age,
            'priceChange': {'m5': m5}, 'volume': {'m5': volume},
            'txns': {'m5': {'buys': buys, 'sells': sells}},
        }

    def test_prewarm_accepts_near_setup_before_full_entry_threshold(self):
        self.assertTrue(tape.preflow_candidate(self.coin('near', score=35, liquidity=3000, age=70, m5=25)))
        self.assertFalse(tape.preflow_candidate(self.coin('stale', score=60, liquidity=50000, age=260)))

    def test_oct4_gold_candidate_still_prewarms_after_four_hours(self):
        gold = self.coin('mature-gold', score=97, liquidity=65000, age=1500, m5=4)
        self.assertTrue(tape.gold_prefilter_candidate(gold))
        self.assertTrue(tape.preflow_candidate(gold))
        weak = self.coin('mature-weak', score=60, liquidity=65000, age=1500, m5=4)
        self.assertFalse(tape.preflow_candidate(weak))

    def test_gold_candidate_wins_priority_for_limited_tape_slots(self):
        gold = self.coin('mature-gold', score=97, liquidity=65000, age=1500, m5=4, buys=10, sells=8)
        high_ratio = self.coin('young-hype', score=64, liquidity=65000, age=10, m5=10, buys=200, sells=1)
        with patch.object(tape.SESSION, 'get', return_value=_StateResponse({'feed': [high_ratio, gold], 'positions': []})), \
             patch.object(tape, 'MAX_TRACKED', 1):
            rows = tape.feed_snapshot()
        self.assertEqual([row['symbol'] for row in rows], ['mature-gold'])

    def test_prewarm_is_broader_than_engine_but_still_bounded(self):
        self.assertTrue(tape.preflow_candidate(self.coin('young-low-score', score=24, liquidity=3300, age=28, m5=-6)))
        self.assertFalse(tape.preflow_candidate(self.coin('too-thin', score=80, liquidity=1200, age=20)))
        self.assertFalse(tape.preflow_candidate(self.coin('already-vertical', score=95, liquidity=40000, age=10, m5=45)))

    def test_feed_snapshot_prioritizes_buy_pressure(self):
        weak = self.coin('weak', score=70, liquidity=20000, buys=20, sells=18, volume=3000)
        strong = self.coin('strong', score=70, liquidity=20000, buys=45, sells=10, volume=6000)
        medium = self.coin('medium', score=70, liquidity=20000, buys=30, sells=15, volume=4500)
        with patch.object(tape.SESSION, 'get', return_value=_StateResponse({'feed': [weak, strong, medium], 'positions': []})), \
             patch.object(tape, 'MAX_TRACKED', 2):
            rows = tape.feed_snapshot()
        self.assertEqual([row['symbol'] for row in rows], ['strong', 'medium'])
        self.assertGreater(rows[0]['market_ratio_m5'], rows[1]['market_ratio_m5'])


if __name__ == '__main__':
    unittest.main()

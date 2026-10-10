import unittest
from unittest.mock import patch

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
        self.assertTrue(tape.preflow_candidate(self.coin('near', score=50, liquidity=9000, age=40)))
        self.assertFalse(tape.preflow_candidate(self.coin('stale', score=90, liquidity=50000, age=200)))

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

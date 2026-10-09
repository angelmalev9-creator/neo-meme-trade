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


if __name__ == '__main__':
    unittest.main()

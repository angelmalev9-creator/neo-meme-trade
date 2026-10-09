import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import market_monitor as monitor
import live_tape


class _Response:
    def __init__(self, rows):
        self._rows = rows
    def raise_for_status(self):
        return None
    def json(self):
        return self._rows


class MarketRefreshProgressTests(unittest.TestCase):
    def test_fetch_pairs_reports_live_progress_until_full_batch(self):
        addresses = [live_tape._b58encode(bytes([i]) * 32) for i in range(1, 66)]
        def fake_get(url, **_kwargs):
            raw = url.rsplit('/', 1)[-1]
            batch = raw.split(',') if raw else []
            return _Response([
                {'chainId': 'solana', 'baseToken': {'address': address}, 'pairAddress': address,
                 'liquidity': {'usd': 1}, 'priceUsd': '1'}
                for address in batch
            ])
        progress = []
        with patch.object(monitor.requests, 'get', side_effect=fake_get):
            rows = monitor.fetch_pairs(addresses, progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(len(rows), 65)
        self.assertGreaterEqual(len(progress), 3)
        self.assertEqual(progress[-1], (65, 65))
        self.assertEqual([x[0] for x in progress], sorted(x[0] for x in progress))
        self.assertTrue(all(total == 65 for _, total in progress))


if __name__ == '__main__':
    unittest.main()

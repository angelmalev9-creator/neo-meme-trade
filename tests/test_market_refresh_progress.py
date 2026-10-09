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
        self.assertEqual(monitor._MARKET_DATA_HEALTH['status'], 'online')
        self.assertEqual(monitor._MARKET_DATA_HEALTH['requested_tokens'], 65)
        self.assertEqual(monitor._MARKET_DATA_HEALTH['unavailable_tokens'], 0)
        self.assertEqual(len(rows), 65)
        self.assertGreaterEqual(len(progress), 3)
        self.assertEqual(progress[-1], (65, 65))
        self.assertEqual([x[0] for x in progress], sorted(x[0] for x in progress))
        self.assertTrue(all(total == 65 for _, total in progress))

    def test_fetch_pairs_recovers_a_timed_out_batch_by_splitting_retry(self):
        addresses = [live_tape._b58encode(bytes([i]) * 32) for i in range(1, 31)]
        calls = []
        def fake_get(url, **_kwargs):
            raw = url.rsplit('/', 1)[-1]
            batch = raw.split(',') if raw else []
            calls.append(len(batch))
            if len(batch) == 30:
                raise monitor.requests.Timeout('transient timeout')
            return _Response([
                {'chainId': 'solana', 'baseToken': {'address': address}, 'pairAddress': address,
                 'liquidity': {'usd': 1}, 'priceUsd': '1'}
                for address in batch
            ])
        with patch.object(monitor.requests, 'get', side_effect=fake_get), patch.object(monitor.time, 'sleep'):
            rows = monitor.fetch_pairs(addresses)
        self.assertEqual(len(rows), 30)
        self.assertEqual(calls, [30, 15, 15])


    def test_fetch_pairs_projects_current_degraded_health_after_retry_failure(self):
        addresses = [live_tape._b58encode(bytes([i]) * 32) for i in range(1, 31)]
        def fake_get(url, **_kwargs):
            raw = url.rsplit('/', 1)[-1]
            batch = raw.split(',') if raw else []
            if len(batch) in (30, 15):
                raise monitor.requests.Timeout('provider still unavailable')
            return _Response([])
        with patch.object(monitor.requests, 'get', side_effect=fake_get), patch.object(monitor.time, 'sleep'):
            rows = monitor.fetch_pairs(addresses)
        self.assertEqual(rows, [])
        self.assertEqual(monitor._MARKET_DATA_HEALTH['status'], 'degraded')
        self.assertEqual(monitor._MARKET_DATA_HEALTH['requested_tokens'], 30)
        self.assertEqual(monitor._MARKET_DATA_HEALTH['unavailable_tokens'], 30)

    def test_discover_projects_aggregate_source_health(self):
        calls = []
        def fake_api(path):
            calls.append(path)
            if path == '/token-profiles/latest/v1':
                raise monitor.requests.Timeout('one source down')
            return []
        with patch.object(monitor, 'api', side_effect=fake_api):
            order, metadata = monitor.discover()
        self.assertEqual(order, [])
        self.assertEqual(metadata, {})
        self.assertEqual(len(calls), 5)
        self.assertEqual(monitor._DISCOVERY_HEALTH['status'], 'degraded')
        self.assertEqual(monitor._DISCOVERY_HEALTH['sources_ok'], 4)
        self.assertEqual(monitor._DISCOVERY_HEALTH['sources_failed'], 1)

    def test_discovery_api_retries_one_transient_timeout(self):
        calls = []
        def fake_get(url, **_kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise monitor.requests.Timeout('transient timeout')
            return _Response([{'ok': True}])
        with patch.object(monitor.SESSION, 'get', side_effect=fake_get), patch.object(monitor.time, 'sleep'):
            rows = monitor.api('/test')
        self.assertEqual(rows, [{'ok': True}])
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()

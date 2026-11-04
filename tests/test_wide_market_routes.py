"""Dashboard route regression tests: query params must not become route names."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from wide_market_observer_paper import Handler


class WideMarketRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_dashboard_tracking_query_is_ignored(self):
        for route in ('/', '/?utm_source=chatgpt.com', '/?utm_source=chatgpt.com&mode=paper',
                      '/index.html?tracking=1'):
            with self.subTest(route=route):
                with urllib.request.urlopen(self.base + route, timeout=3) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn('text/html', resp.headers['Content-Type'])
                    self.assertIn('NEO', resp.read(4096).decode('utf8'))

    def test_status_tracking_query_is_ignored(self):
        with urllib.request.urlopen(self.base + '/status?utm_source=chatgpt.com', timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            data = json.load(resp)
            self.assertEqual(data.get('mode'), 'READ_ONLY_PAPER_MARKET_OBSERVER')

    def test_unknown_page_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(self.base + '/does-not-exist?utm_source=chatgpt.com', timeout=3)
        self.assertEqual(err.exception.code, 404)


if __name__ == '__main__':
    unittest.main()

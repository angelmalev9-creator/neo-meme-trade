import tempfile
import unittest
from pathlib import Path

from backend.dex_registry import DexRegistry


class DexRegistryTests(unittest.TestCase):
    def test_persists_and_deduplicates_pool(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'dex.json'
            r = DexRegistry(path, persist_interval_ms=1000)
            self.assertTrue(r.observe('MINT', 'PAIR', 'raydium', 'gecko', observed_at=1000))
            self.assertFalse(r.observe('MINT', 'PAIR', 'raydium', 'engine', observed_at=2000))
            r.flush_if_due(3000, force=True)
            loaded = DexRegistry(path)
            self.assertTrue(loaded.has_dex('MINT'))
            self.assertEqual(loaded.stats()['pools'], 1)
            self.assertEqual(set(loaded.pairs_for('MINT')[0]['sources']), {'gecko', 'engine'})

    def test_verified_tape_marks_onchain(self):
        with tempfile.TemporaryDirectory() as td:
            r = DexRegistry(Path(td) / 'dex.json')
            r.observe_tape({'events': [{'address': 'MINT', 'pairAddress': 'PAIR', 'ts': 1234}]})
            self.assertEqual(r.stats()['onchain_verified_tokens'], 1)
            self.assertTrue(r.pairs_for('MINT')[0]['onchain_verified'])


if __name__ == '__main__':
    unittest.main()

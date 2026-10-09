import json
import tempfile
import unittest
from pathlib import Path

from discovery_universe import RollingUniverse, fresh_priority


class RollingUniverseTests(unittest.TestCase):
    def test_fresh_priority_does_not_reprioritize_the_whole_snapshot(self):
        previous = ['a', 'b', 'c', 'd']
        current = ['a', 'b', 'c', 'd', 'new1', 'new2']
        self.assertEqual(fresh_priority(current, previous, urgent=['d', 'new2']), ['d', 'new2', 'new1'])

    def test_priority_then_rotation_covers_the_universe(self):
        with tempfile.TemporaryDirectory() as td:
            now = [1_000_000]
            u = RollingUniverse(Path(td) / 'u.json', max_items=20, ttl_ms=100_000,
                                batch_size=4, clock_ms=lambda: now[0])
            u.observe(['a','b','c','d','e','f'], {x:{'sources':['seed']} for x in 'abcdef'})
            first, _ = u.next_batch(priority=['f'], limit=4)
            second, _ = u.next_batch(priority=['f'], limit=4)
            self.assertEqual(first[0], 'f')
            self.assertEqual(second[0], 'f')
            self.assertGreaterEqual(len(set(first + second)), 5)
            self.assertEqual(u.stats()['size'], 6)

    def test_metadata_merges_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'u.json'
            now = [2_000_000]
            u = RollingUniverse(path, max_items=20, ttl_ms=100_000, batch_size=5, clock_ms=lambda: now[0])
            u.observe(['x'], {'x': {'sources':['latest'], 'icon':'one'}})
            now[0] += 20_000
            u.observe(['x'], {'x': {'sources':['boosted'], 'icon':'two'}})
            restored = RollingUniverse(path, max_items=20, ttl_ms=100_000, batch_size=5, clock_ms=lambda: now[0])
            batch, meta = restored.next_batch()
            self.assertEqual(batch, ['x'])
            self.assertEqual(meta['x']['sources'], ['latest','boosted'])
            self.assertEqual(meta['x']['icon'], 'two')

    def test_ttl_and_max_items_prune_oldest(self):
        with tempfile.TemporaryDirectory() as td:
            now = [10_000]
            u = RollingUniverse(Path(td) / 'u.json', max_items=3, ttl_ms=1000, batch_size=3, clock_ms=lambda: now[0])
            u.observe(['a','b','c'], now=10_000)
            u.observe(['d'], now=10_100)
            self.assertEqual(u.stats()['size'], 3)
            self.assertNotIn('a', u.items)
            now[0] = 11_500
            batch, _ = u.next_batch()
            self.assertEqual(batch, [])

    def test_invalid_state_fails_open_as_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'u.json'; path.write_text('{bad', encoding='utf-8')
            u = RollingUniverse(path, max_items=10, ttl_ms=1000, batch_size=2, clock_ms=lambda: 100)
            self.assertEqual(u.stats()['size'], 0)


if __name__ == '__main__':
    unittest.main()

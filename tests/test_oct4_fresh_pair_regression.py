"""Historical GTA regression: a stale saved pin must not win over a fresh feed."""
import unittest
import market_monitor as m

PAIR = 'DMCn25oKj7zCiWS9SS1bxEo6QbzGb8r9tA6CSfR6LG3k'

class FreshExactPairRegression(unittest.TestCase):
    def test_gta_live_feed_beats_older_pinned_pair(self):
        incoming = {'pairAddress': PAIR, 'updatedAt': 1793864107223, 'priceUsd': 0.00246}
        pinned = {'pairAddress': PAIR, 'updatedAt': 1793830137164, 'priceUsd': 0.002833}
        self.assertEqual(m.select_fresh_exact_pair_market(incoming,pinned,PAIR),incoming)

    def test_newer_matching_pin_wins(self):
        incoming={'pairAddress': PAIR, 'updatedAt': 5}
        pinned={'pairAddress': PAIR, 'updatedAt': 6}
        self.assertIs(m.select_fresh_exact_pair_market(incoming,pinned,PAIR),pinned)

    def test_wrong_pool_never_selected(self):
        wrong={'pairAddress': 'DIFFERENT', 'updatedAt': 1000}
        right={'pairAddress': PAIR, 'updatedAt': 5}
        self.assertIs(m.select_fresh_exact_pair_market(wrong,right,PAIR),right)
        self.assertIsNone(m.select_fresh_exact_pair_market(wrong,None,PAIR))

if __name__=='__main__':unittest.main()

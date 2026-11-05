"""Historical GTA regression: a stale saved pin must not win over a fresh feed."""
import unittest
import market_monitor as m

PAIR = 'DMCn25oKj7zCiWS9SS1bxEo6QbzGb8r9tA6CSfR6LG3k'

class FreshExactPairRegression(unittest.TestCase):
    def test_gta_live_feed_beats_older_pinned_pair(self):
        incoming = {'pairAddress': PAIR, 'updatedAt': 1793864107223, 'priceUsd': 0.00246}
        pinned = {'pairAddress': PAIR, 'updatedAt': 1793830137164, 'priceUsd': 0.002833}
        self.assertEqual(m.select_fresh_exact_pair_market(PAIR,incoming,pinned),incoming)

    def test_stale_pair_key_cannot_shadow_fresh_address_feed(self):
        # fast_position_check supplies both a pair-key pin and address feed.
        old_pair_key = {'pairAddress': PAIR, 'updatedAt': 1793830137164}
        live_address = {'pairAddress': PAIR, 'updatedAt': 1793864107223}
        persisted_pin = {'pairAddress': PAIR, 'updatedAt': 1793830137164}
        self.assertIs(m.select_fresh_exact_pair_market(
            PAIR, old_pair_key, live_address, persisted_pin), live_address)

    def test_newer_matching_pin_wins(self):
        incoming={'pairAddress': PAIR, 'updatedAt': 5}
        pinned={'pairAddress': PAIR, 'updatedAt': 6}
        self.assertIs(m.select_fresh_exact_pair_market(PAIR,incoming,pinned),pinned)

    def test_wrong_pool_never_selected(self):
        wrong={'pairAddress': 'DIFFERENT', 'updatedAt': 1000}
        right={'pairAddress': PAIR, 'updatedAt': 5}
        self.assertIs(m.select_fresh_exact_pair_market(PAIR,wrong,right),right)
        self.assertIsNone(m.select_fresh_exact_pair_market(PAIR,wrong,None))

if __name__=='__main__':unittest.main()

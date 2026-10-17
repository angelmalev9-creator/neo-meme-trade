import time
import unittest

import engine_entry_policy as entry_policy
import order_flow_adaptive_oct4 as oct4


class StopProtectionV2Tests(unittest.TestCase):
    def quote(self, impact=0.5):
        return {
            'token_raw_expected': 1_000_000,
            'price_impact_pct': impact,
            'quoted_at': int(time.time() * 1000),
        }

    def test_clean_execution_inside_stop_buffer_passes(self):
        reasons = entry_policy.quote_rejections(
            self.quote(0.8), -1.40, -2.60,
            max_impact=2.0, max_cost=2.75, max_conservative_cost=4.5,
            now=int(time.time() * 1000),
        )
        self.assertEqual(reasons, [])

    def test_roundtrip_near_stop_is_rejected_even_if_profile_is_looser(self):
        reasons = entry_policy.quote_rejections(
            self.quote(0.8), -1.51, -2.60,
            max_impact=2.0, max_cost=2.75, max_conservative_cost=4.5,
            now=int(time.time() * 1000),
        )
        self.assertIn('roundtrip_cost', reasons)

    def test_worst_case_near_stop_is_rejected(self):
        reasons = entry_policy.quote_rejections(
            self.quote(0.8), -1.30, -2.76,
            max_impact=2.0, max_cost=2.75, max_conservative_cost=4.5,
            now=int(time.time() * 1000),
        )
        self.assertIn('worst_case_cost', reasons)

    def test_entry_impact_above_one_percent_is_rejected(self):
        reasons = entry_policy.quote_rejections(
            self.quote(1.01), -1.0, -2.0,
            max_impact=2.0, max_cost=2.75, max_conservative_cost=4.5,
            now=int(time.time() * 1000),
        )
        self.assertIn('impact', reasons)

    def test_recent_loss_has_longer_same_token_cooldown_than_win(self):
        now = int(time.time() * 1000)
        history = [
            {'address': 'LOSS', 'closed_at': now - 50 * 60 * 1000, 'pnl_pct': -4.0},
            {'address': 'WIN', 'closed_at': now - 50 * 60 * 1000, 'pnl_pct': 10.0},
        ]
        blocked = oct4.cooldown_addresses(history, now, seconds=20 * 60)
        self.assertIn('LOSS', blocked)
        self.assertNotIn('WIN', blocked)


if __name__ == '__main__':
    unittest.main(verbosity=2)

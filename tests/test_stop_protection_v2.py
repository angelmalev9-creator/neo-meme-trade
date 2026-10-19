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

    def check(self, impact, expected, conservative, *, protect=True):
        return entry_policy.quote_rejections(
            self.quote(impact), expected, conservative,
            max_impact=2.0, max_cost=2.75, max_conservative_cost=4.5,
            now=int(time.time() * 1000), protect_stop=protect,
        )

    def test_clean_execution_inside_stop_buffer_passes(self):
        self.assertEqual(self.check(0.8, -2.70, -3.70), [])

    def test_roundtrip_near_stop_is_rejected_in_fixed_stop_profile(self):
        self.assertIn('roundtrip_cost', self.check(0.8, -2.76, -3.60))

    def test_worst_case_near_stop_is_rejected(self):
        self.assertIn('worst_case_cost', self.check(0.8, -2.60, -3.76))

    def test_entry_impact_above_one_percent_is_rejected(self):
        self.assertIn('impact', self.check(1.01, -1.0, -2.0))


    def test_prime_quality_gate_accepts_clean_structure(self):
        coin = {
            'score': 98, 'liquidityUsd': 120000,
            'volume': {'h1': 240000}, 'priceChange': {'m5': 5.0},
            'txns': {'m5': {'buys': 30, 'sells': 15}},
        }
        self.assertEqual(entry_policy.fixed_target_quality_rejections(coin, {'conviction': 95}), [])

    def test_prime_quality_gate_rejects_churn_and_chase(self):
        coin = {
            'score': 96, 'liquidityUsd': 50000,
            'volume': {'h1': 350000}, 'priceChange': {'m5': 15.0},
            'txns': {'m5': {'buys': 20, 'sells': 20}},
        }
        reasons = entry_policy.fixed_target_quality_rejections(coin, {'conviction': 90})
        self.assertIn('momentum', reasons)
        self.assertIn('market_buyers', reasons)
        self.assertIn('turnover_churn', reasons)

    def test_prime_quality_gate_rejects_low_score_or_conviction(self):
        coin = {
            'score': 84, 'liquidityUsd': 100000,
            'volume': {'h1': 150000}, 'priceChange': {'m5': 4.0},
            'txns': {'m5': {'buys': 30, 'sells': 10}},
        }
        reasons = entry_policy.fixed_target_quality_rejections(coin, {'conviction': 60})
        self.assertIn('score', reasons)
        self.assertIn('conviction', reasons)

    def test_other_profiles_keep_their_validated_execution_limits(self):
        self.assertEqual(self.check(1.20, -2.0, -4.0, protect=False), [])

    def test_recent_loss_has_longer_same_token_cooldown_than_win_when_enabled(self):
        now = int(time.time() * 1000)
        history = [
            {'address': 'LOSS', 'closed_at': now - 50 * 60 * 1000, 'pnl_pct': -4.0},
            {'address': 'WIN', 'closed_at': now - 50 * 60 * 1000, 'pnl_pct': 10.0},
        ]
        blocked = oct4.cooldown_addresses(history, now, seconds=20 * 60, loss_multiplier=3.0)
        self.assertIn('LOSS', blocked)
        self.assertNotIn('WIN', blocked)

    def test_default_cooldown_behavior_stays_backward_compatible(self):
        now = int(time.time() * 1000)
        history = [{'address': 'LOSS', 'closed_at': now - 21 * 60 * 1000, 'pnl_pct': -4.0}]
        self.assertNotIn('LOSS', oct4.cooldown_addresses(history, now, seconds=20 * 60))


if __name__ == '__main__':
    unittest.main(verbosity=2)

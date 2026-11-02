import unittest
import paper_quality_guard as q
import market_monitor as m
import order_flow_adaptive_oct4 as oct4


class PaperQualityGuardTests(unittest.TestCase):
    def coin(self):
        return {'liquidityUsd': 100_000, 'volume': {'h1': 200_000},
                'txns': {'m5': {'buys': 30, 'sells': 15}}}

    def flow(self):
        return {'buyer_wallets': 4, 'unique_wallets': 5, 'seller_wallets': 2,
                'wallet_buy_sell_ratio': 2.0, 'repeat_buy_wallets': 1,
                'buy_usd': 1000, 'sell_usd': 300}

    def test_plus_profile_is_separate_from_historical_profile(self):
        self.assertEqual(m.apply_strategy_profile(oct4.PROFILE), oct4.PROFILE)
        self.assertEqual(m.ENTRY_POLICY_VERSION, oct4.ENTRY_POLICY_VERSION)
        self.assertEqual(m.apply_strategy_profile(m.OCT4_PLUS_PROFILE), m.OCT4_PLUS_PROFILE)
        self.assertIn(q.VERSION, m.ENTRY_POLICY_VERSION)
        self.assertEqual(m.EXIT_POLICY, oct4.EXIT_POLICY)
        self.assertEqual(m.MAX_POSITIONS, 1)
        self.assertEqual(m.TRADE_NOTIONAL_USD, 200.0)
        self.assertEqual(m.apply_strategy_profile(oct4.PROFILE), oct4.PROFILE)

    def test_clean_cluster_passes(self):
        self.assertEqual(q.market_rejections(self.coin(), self.flow(), {'conviction': 90}), [])

    def test_thin_cluster_rejected(self):
        r=q.market_rejections(self.coin(), {**self.flow(), 'buyer_wallets': 2, 'unique_wallets': 3}, {'conviction': 90})
        self.assertIn('buyer_cluster', r)

    def test_repeat_confirmation_or_broad_cluster_required(self):
        self.assertIn('repeat_buyers', q.market_rejections(self.coin(), {**self.flow(), 'repeat_buy_wallets': 0, 'buyer_wallets': 4}, {'conviction': 90}))
        self.assertNotIn('repeat_buyers', q.market_rejections(self.coin(), {**self.flow(), 'repeat_buy_wallets': 0, 'buyer_wallets': 5}, {'conviction': 90}))

    def test_churn_and_extreme_imbalance_rejected(self):
        c={**self.coin(), 'volume': {'h1': 450_000}, 'txns': {'m5': {'buys': 90, 'sells': 10}}}
        r=q.market_rejections(c, self.flow(), {'conviction': 90})
        self.assertIn('turnover_churn', r)
        self.assertIn('extreme_buy_imbalance', r)

    def test_execution_basis_guard(self):
        r,m=q.execution_rejections(market_price=1.0, fill_price=1.02, impact_pct=.8,
                                   immediate_roundtrip_pct=-2.0, conservative_roundtrip_pct=-3.0)
        self.assertEqual(r, [])
        self.assertAlmostEqual(m['execution_basis_pct'], 2.0)
        r,_=q.execution_rejections(market_price=1.0, fill_price=1.03, impact_pct=.8,
                                   immediate_roundtrip_pct=-2.0, conservative_roundtrip_pct=-3.0)
        self.assertIn('execution_basis', r)

    def test_regime_brake_is_strategy_scoped_and_expires(self):
        now=10_000_000
        losses=[{'strategy_id':'ORDER_FLOW_ADAPTIVE','closed_at':now-i*1000,'pnl_pct':-1} for i in range(4)]
        self.assertEqual(q.regime_rejections(losses, now, strategy_id='ORDER_FLOW_ADAPTIVE'), ['regime_brake'])
        self.assertEqual(q.regime_rejections(losses, now, strategy_id='OTHER'), [])
        old=[{**row,'closed_at':row['closed_at']-q.LOSS_STREAK_COOLDOWN_MS-1} for row in losses]
        self.assertEqual(q.regime_rejections(old, now, strategy_id='ORDER_FLOW_ADAPTIVE'), [])


if __name__ == '__main__': unittest.main(verbosity=2)

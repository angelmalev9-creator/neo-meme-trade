import time
import unittest

import market_monitor as monitor


class BuyPressureGateTests(unittest.TestCase):
    def coin(self, *, buys=100, sells=40, volume=20000, age=20, m5=9):
        return {
            "address": "AVJk6piE392EvzCnP8SXTbq158ALSfUUeY3wKEsNpump",
            "pairAddress": "GEb74S9yJKTNFc8YzfojKnFEGJXaViqt1emcctCK6uAf",
            "dexId": "pumpswap", "priceUsd": 0.0005, "score": 80,
            "liquidityUsd": 60000, "ageMinutes": age, "updatedAt": int(time.time()*1000),
            "priceChange": {"m5": m5}, "volume": {"m5": volume},
            "txns": {"m5": {"buys": buys, "sells": sells}},
        }

    def test_young_high_activity_market_can_advance_while_wallet_tape_warms(self):
        reasons = monitor.buy_pressure_rejections(
            self.coin(), {"trades": 0, "buy_sell_usd_ratio": 0}, {"conviction": 0}
        )
        self.assertEqual(reasons, [])

    def test_thin_market_still_requires_verified_flow(self):
        reasons = monitor.buy_pressure_rejections(
            self.coin(buys=8, sells=3, volume=6000),
            {"trades": 0, "buy_sell_usd_ratio": 0}, {"conviction": 0}
        )
        self.assertIn("flow_count", reasons)

    def test_older_market_does_not_use_warming_tape_exception(self):
        reasons = monitor.buy_pressure_rejections(
            self.coin(age=70), {"trades": 0, "buy_sell_usd_ratio": 0}, {"conviction": 0}
        )
        self.assertIn("flow_count", reasons)


if __name__ == "__main__":
    unittest.main()

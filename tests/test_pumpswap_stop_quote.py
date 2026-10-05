import unittest
import pumpswap_stop_quote as p


class PumpSwapStopQuoteMathTests(unittest.TestCase):
    def test_constant_product_sell_matches_recorded_pool_snapshot(self):
        result = p.quote_from_reserves(
            base_reserve=166244294493322,
            quote_reserve=117793268940,
            virtual_quote_reserve=17583730545,
            base_amount=356114352398,
            fee_schedule={"lp": 25, "protocol": 5, "creator": 0},
        )
        self.assertEqual(result["raw_quote_out"], 289373194)
        self.assertEqual(result["final_quote_out"], 288505074)
        self.assertGreater(result["impact_pct"], 0)
        self.assertLess(result["impact_pct"], 1)

    def test_virtual_reserve_is_used(self):
        without = p.quote_from_reserves(1_000_000, 500_000, 0, 10_000, {"lp": 0, "protocol": 0, "creator": 0})
        with_virtual = p.quote_from_reserves(1_000_000, 500_000, 100_000, 10_000, {"lp": 0, "protocol": 0, "creator": 0})
        self.assertGreater(with_virtual["final_quote_out"], without["final_quote_out"])

    def test_fee_components_are_charged_separately(self):
        result = p.quote_from_reserves(
            1_000_000, 500_000, 0, 10_000,
            {"lp": 25, "protocol": 5, "creator": 10},
        )
        self.assertEqual(
            result["final_quote_out"],
            result["raw_quote_out"] - result["lp_fee_raw"] - result["protocol_fee_raw"] - result["creator_fee_raw"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

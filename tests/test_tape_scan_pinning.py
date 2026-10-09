import unittest

import market_monitor as m


class TapeScanPinningTests(unittest.TestCase):
    def test_active_tape_pins_require_valid_mint_and_pool(self):
        mint = "So11111111111111111111111111111111111111112"
        pair = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        tape = {"pair_coverage": {
            pair: {"address": mint, "pairAddress": pair, "status": "DEGRADED"},
            "bad": {"address": "x", "pairAddress": pair},
        }}
        self.assertEqual(m.active_tape_pins(tape), {mint: pair})

    def test_exact_taped_pool_overrides_higher_liquidity_pair_for_same_mint(self):
        mint = "So11111111111111111111111111111111111111112"
        taped = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        other = "11111111111111111111111111111111"
        taped_row = {"baseToken": {"address": mint}, "pairAddress": taped, "liquidity": {"usd": 10}}
        other_row = {"baseToken": {"address": mint}, "pairAddress": other, "liquidity": {"usd": 1000}}
        chosen = {mint: other_row}
        m.prefer_exact_tape_pairs(chosen, [other_row, taped_row], {mint: taped})
        self.assertIs(chosen[mint], taped_row)


if __name__ == "__main__":
    unittest.main()

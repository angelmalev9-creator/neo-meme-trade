"""Regression tests for an honest, PAPER-only exact-pool loss cooldown.

These tests use synthetic history and no credentials, RPC, HTTP or orders.
Run: python3 -m unittest discover -s scripts -p test_pool_loss_guard.py -v
"""
from __future__ import annotations
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import paper_pool_loss_guard as guard

H = 3_600_000
MIN = 60_000
T = 1_800_000_000_000
SESSION = "SYNTHETIC_PAPER"

def closed(at, pnl, *, pool="P", mint="M", session=SESSION, void=False, trade_id=None):
    return {
        "id": trade_id or f"trade-{at}",
        "address": mint, "pairAddress": pool, "session_id": session,
        "closed_at": at, "pnl_usd": pnl, "technical_voided": void,
        "symbol": "TEST",
    }

def blocked(history, now, pool="P", mint="M", session=SESSION):
    return guard.blocked_pools(history, now, session).get((mint, pool))

class PoolLossGuardTests(unittest.TestCase):
    def test_recent_consecutive_losses_block_for_three_hours(self):
        history = [closed(T - 35 * MIN, -2), closed(T - 5 * MIN, -3)]
        result = blocked(history, T)
        self.assertIsNotNone(result)
        self.assertEqual(result["blocked_until"], T - 5 * MIN + 3 * H)
        self.assertEqual(result["losses"], 2)

    def test_first_loss_aging_out_does_not_cancel_active_block(self):
        # Losses are 5h50m apart; at decision time the first is older
        # than 6h, but the *second* was closed just twenty minutes ago.
        first = T
        second = T + 5 * H + 50 * MIN
        now = second + 20 * MIN
        result = blocked([closed(first, -2), closed(second, -3)], now)
        self.assertIsNotNone(result)
        self.assertEqual(result["blocked_until"], second + 3 * H)

    def test_pause_ends_exactly_three_hours_after_latest_loss(self):
        history = [closed(T, -1), closed(T + 1 * H, -2)]
        self.assertIsNotNone(blocked(history, T + 4 * H - 1))
        self.assertIsNone(blocked(history, T + 4 * H))

    def test_losing_closes_more_than_six_hours_apart_do_not_block(self):
        history = [closed(T, -1), closed(T + 6 * H + 1, -2)]
        self.assertIsNone(blocked(history, T + 6 * H + 30 * MIN))

    def test_intervening_win_breaks_streak(self):
        history = [closed(T, -1), closed(T + MIN, -2), closed(T + 2 * MIN, +1)]
        self.assertIsNone(blocked(history, T + 3 * MIN))

    def test_same_symbol_different_exact_pool_isolated(self):
        history = [closed(T, -1, pool="A"), closed(T + MIN, -2, pool="B")]
        self.assertIsNone(blocked(history, T + 2 * MIN, pool="A"))
        self.assertIsNone(blocked(history, T + 2 * MIN, pool="B"))

    def test_session_and_voided_trades_excluded(self):
        history = [
            closed(T, -1),
            closed(T + MIN, -2, session="OTHER"),
            closed(T + 2 * MIN, -3, void=True),
        ]
        self.assertIsNone(blocked(history, T + 3 * MIN))

    def test_future_closes_ignored_and_closed_order_is_authoritative(self):
        history = [
            closed(T + 1 * MIN, -1, trade_id="A"),
            closed(T, -2, trade_id="B"),
            closed(T + 5 * MIN, 1, trade_id="FUTURE"),
        ]
        self.assertIsNotNone(blocked(history, T + 2 * MIN))
        self.assertIsNone(blocked(history, T + 6 * MIN))

    def test_missing_nonfinite_pnl_cannot_be_a_loss(self):
        history = [closed(T, -1), closed(T + MIN, float("nan"))]
        self.assertIsNone(blocked(history, T + 2 * MIN))

    def test_empty_data_and_timestamp_do_not_create_blocks(self):
        self.assertEqual(guard.blocked_pools([], T, SESSION), {})
        self.assertEqual(guard.blocked_pools([closed(T, -1)], 0, SESSION), {})

if __name__ == "__main__":
    unittest.main()

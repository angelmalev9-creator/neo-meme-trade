"""Regression tests for a PAPER-only strategy-level loss-streak brake.

No market access, buy/sell calls, real-money or wallet integration.
"""
from __future__ import annotations
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import paper_quality_guard as quality

H = 3_600_000
MIN = 60_000
T = 1_800_000_000_000
SESSION = "PAPER_A"
STRATEGY = "ORDER_FLOW_ADAPTIVE"

def close(ts, pnl=-1.0, *, session=SESSION, strategy=STRATEGY, void=False, trade_id="X"):
    return {
        "closed_at": ts, "pnl_pct": pnl, "pnl_usd": pnl,
        "strategy_id": strategy, "session_id": session,
        "technical_voided": void, "id": f"{trade_id}:{ts}",
    }

def reasons(rows, now=T, *, session=SESSION):
    return quality.regime_rejections(rows, now, strategy_id=STRATEGY, session_id=session)

class BrakeTests(unittest.TestCase):
    def test_four_losses_in_recent_six_hours_block(self):
        rows = [close(T - x * MIN) for x in (3, 6, 9, 12)]
        self.assertIn("regime_brake", reasons(rows))

    def test_three_losses_do_not_block(self):
        rows = [close(T - x * MIN) for x in (3, 6, 9)]
        self.assertEqual(reasons(rows), [])

    def test_intervening_win_resets_streak(self):
        rows = [close(T - x * MIN) for x in (3, 6, 9)]
        rows += [close(T - 10 * MIN, pnl=2.0), close(T - 11 * MIN)]
        self.assertEqual(reasons(rows), [])

    def test_previous_sessions_are_not_mixed(self):
        rows = [close(T - x * MIN) for x in (3, 6)]
        rows += [close(T - x * MIN, session="PAPER_B") for x in (7, 8)]
        self.assertEqual(reasons(rows), [])

    def test_technical_voids_do_not_count_as_losses(self):
        rows = [close(T - x * MIN) for x in (3, 6, 9)]
        rows += [close(T - 10 * MIN, void=True)]
        self.assertEqual(reasons(rows), [])

    def test_stale_losses_from_days_ago_do_not_trigger_new_pause(self):
        rows = [close(T - 10 * MIN)]
        rows += [close(T - days * 24 * H) for days in (1,2,3)]
        self.assertEqual(reasons(rows), [])

    def test_future_closed_trades_are_never_used(self):
        rows = [close(T - x * MIN) for x in (3, 6, 9)]
        rows += [close(T + MIN)]
        self.assertEqual(reasons(rows), [])

    def test_pause_expires_after_two_hours(self):
        rows = [close(T - 2 * H - 5 * MIN - x * MIN) for x in range(4)]
        self.assertEqual(reasons(rows), [])

    def test_other_strategy_does_not_count(self):
        rows = [close(T - x * MIN) for x in (3, 6, 9)]
        rows += [close(T - 11 * MIN, strategy="ANOTHER_STRATEGY")]
        self.assertEqual(reasons(rows), [])

    def test_oldest_of_four_losses_must_be_inside_six_hours(self):
        rows = [close(T - 3 * MIN),close(T - H),close(T - 2 * H),close(T - 8 * H)]
        self.assertEqual(reasons(rows), [])

if __name__ == "__main__":
    unittest.main()

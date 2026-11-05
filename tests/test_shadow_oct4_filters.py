"""Regression tests for read-only Oct4 PAPER shadow screens."""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from scripts import shadow_oct4_filters as shadow


TZ = ZoneInfo("Europe/Sofia")
BASE = int(dt.datetime(2026, 11, 5, 10, tzinfo=TZ).timestamp()*1000)
MIN = 60_000


def trade(i, opened, closed, pnl=-5.0, mint="mint-a", pair="pool-a",
          cost=-1.5, void=False, session="S1"):
    return {"id":str(i),"trade_no":i,"symbol":"DUPLICATE_SYMBOL",
            "session_id":session,"address":mint,"pairAddress":pair,
            "opened_at":opened,"closed_at":closed,"pnl_usd":pnl,
            "entry_roundtrip_pnl_pct":cost,"technical_voided":void}


class ShadowScreens(unittest.TestCase):
    def test_cost_cap_pre_entry_only_and_missing_evidence(self):
        self.assertIsNone(shadow.cost_rejection({"entry_roundtrip_pnl_pct":-2.0}))
        self.assertEqual(shadow.cost_rejection({"entry_roundtrip_pnl_pct":-2.001}),"PRE_ENTRY_ROUNDTRIP_ABOVE_CAP")
        self.assertEqual(shadow.cost_rejection({"entry_roundtrip_pnl_pct":float("nan")}),"UNVERIFIABLE_PRE_ENTRY_COST")
        self.assertEqual(shadow.cost_rejection({}),"UNVERIFIABLE_PRE_ENTRY_COST")

    def test_two_losses_must_be_closed_before_candidate_entry(self):
        a=trade(1,BASE,BASE+1*MIN,-3)
        b=trade(2,BASE+2*MIN,BASE+3*MIN,-4)
        candidate=trade(3,BASE+4*MIN,BASE+5*MIN,99)
        self.assertEqual(shadow.recent_loss_brake(candidate,[a,b]),"TWO_RECENT_LOSSES_SAME_POOL")
        candidate_early=trade(4,BASE+2*MIN+1,BASE+6*MIN,-10)
        self.assertIsNone(shadow.recent_loss_brake(candidate_early,[a]))
        self.assertIsNone(shadow.recent_loss_brake(candidate_early,[]))

    def test_cooldown_ends_after_three_hours(self):
        a=trade(1,BASE,BASE+MIN,-3)
        b=trade(2,BASE+2*MIN,BASE+3*MIN,-4)
        candidate=trade(3,BASE+3*MIN+shadow.COOLDOWN_MS+1,BASE+3*MIN+shadow.COOLDOWN_MS+2)
        self.assertIsNone(shadow.recent_loss_brake(candidate,[a,b]))

    def test_positive_last_close_breaks_streak(self):
        a=trade(1,BASE,BASE+MIN,-3)
        b=trade(2,BASE+2*MIN,BASE+3*MIN,-4)
        c=trade(3,BASE+4*MIN,BASE+5*MIN,3)
        candidate=trade(4,BASE+6*MIN,BASE+7*MIN)
        self.assertIsNone(shadow.recent_loss_brake(candidate,[a,b,c]))

    def test_same_ticker_different_token_pair_never_mixed(self):
        a=trade(1,BASE,BASE+MIN,-3)
        b=trade(2,BASE+2*MIN,BASE+3*MIN,-4)
        candidate=trade(3,BASE+4*MIN,BASE+5*MIN,-5,mint="mint-b",pair="pool-b")
        self.assertIsNone(shadow.recent_loss_brake(candidate,[a,b]))
        missing=trade(4,BASE+4*MIN,BASE+5*MIN,-5,mint="",pair="")
        self.assertEqual(shadow.recent_loss_brake(missing,[a,b]),"UNVERIFIABLE_TOKEN_ID")

    def test_historical_technical_voids_cannot_cause_shadow_brake(self):
        a=trade(1,BASE,BASE+MIN,-3,void=True)
        b=trade(2,BASE+2*MIN,BASE+3*MIN,-4,void=True)
        candidate=trade(3,BASE+4*MIN,BASE+5*MIN)
        self.assertIsNone(shadow.recent_loss_brake(candidate,[a,b]))

    def test_unobserved_future_pnl_never_used_for_current_entry(self):
        # Two losses will only be *known* after candidate #3 opened.
        a=trade(1,BASE,BASE+20*MIN,-3)
        b=trade(2,BASE+2*MIN,BASE+15*MIN,-4)
        candidate=trade(3,BASE+5*MIN,BASE+6*MIN,100)
        result=shadow.analyze({"demo_session_id":"S1","history":[a,b,candidate]})
        self.assertEqual(result["cases"]["POOL_LOSS_BRAKE"]["observed_entries"],3)
        self.assertEqual(result["cases"]["ORIGINAL_OBSERVED"]["observed_recorded_pnl_usd"],93)

    def test_same_day_uses_sofia_time_and_voids_excluded(self):
        a=trade(1,BASE,BASE+MIN,-12,cost=-2.7,void=True)
        b=trade(2,BASE+2*MIN,BASE+3*MIN,4,cost=-1.0)
        next_day=int(dt.datetime(2026,11,6,11,tzinfo=TZ).timestamp()*1000)
        c=trade(3,next_day,next_day+MIN,-500,cost=-1.0)
        result=shadow.analyze({"demo_session_id":"S1","history":[a,b,c]})
        self.assertEqual(result["cases"]["ORIGINAL_OBSERVED"]["observed_entries"],1)
        self.assertEqual(result["cases"]["ORIGINAL_OBSERVED"]["observed_recorded_pnl_usd"],4)

    def test_observation_cutoff_preserves_prior_history_for_brake(self):
        a=trade(1,BASE,BASE+MIN,-3)
        b=trade(2,BASE+2*MIN,BASE+3*MIN,-4)
        c=trade(3,BASE+4*MIN,BASE+5*MIN,-5,cost=-1.5)
        cutoff=BASE+4*MIN
        report=shadow.analyze({"demo_session_id":"S1","history":[a,b,c]},since_ms=cutoff)
        self.assertEqual(report["cases"]["ORIGINAL_OBSERVED"]["observed_entries"],1)
        self.assertEqual(report["cases"]["POOL_LOSS_BRAKE"]["observed_entries"],0)
        self.assertEqual(report["cases"]["COST_CAP_2PCT"]["observed_entries"],1)

    def test_report_refuses_duplicate_ids(self):
        a=trade(1,BASE,BASE+MIN,-3)
        b=trade(1,BASE+2*MIN,BASE+3*MIN,-4)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            shadow.analyze({"demo_session_id":"S1","history":[a,b]})

    def test_report_does_not_mutate_original(self):
        trades=[trade(1,BASE,BASE+MIN,-3),trade(2,BASE+2*MIN,BASE+3*MIN,-4),
                trade(3,BASE+4*MIN,BASE+5*MIN,-5)]
        state={"demo_session_id":"S1","history":trades}
        original=copy.deepcopy(state)
        report=shadow.analyze(state)
        self.assertEqual(state, original)
        self.assertFalse(report["original_strategy_modified"])
        self.assertEqual(report["cases"]["ORIGINAL_OBSERVED"]["observed_entries"],3)
        self.assertEqual(report["cases"]["BOTH_SHADOW_FILTERS"]["observed_entries"],2)


if __name__=="__main__":
    unittest.main()

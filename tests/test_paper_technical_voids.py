"""Offline tests for audited, paper-only invalidation of stale-market exits."""
import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from scripts import void_stale_paper_exits as v


def fixture():
    stamp=int(dt.datetime(2026,11,5,7,30,tzinfo=ZoneInfo('Europe/Sofia')).timestamp()*1000)
    amounts=[-4.0]*25+[-28.68161534]
    return [{"id":f"paper-{i}","session_id":v.TARGET_SESSION,
             "closed_at":stamp+i*100,"exit_reason":"STALE_MARKET_EXIT",
             "notional_usd":200.0,"pnl_usd":amounts[i],"pnl_pct":amounts[i]/2}
            for i in range(26)]


class TechnicalCorrectionTests(unittest.TestCase):
    def test_exactly_26_stale_bug_trades_and_correct_credit(self):
        all_trades=fixture()+[
            {"id":"valid-loss","session_id":v.TARGET_SESSION,
             "closed_at":fixture()[0]["closed_at"],"pnl_usd":-10.38,
             "exit_reason":"STOP_LOSS"}]
        correction=v.create_correction(all_trades,[])
        self.assertEqual(correction["trade_count"],26)
        self.assertEqual(correction["amount_usd"],128.68161534)
        updated=v.apply_flags(all_trades,correction,12345)
        self.assertEqual(sum(bool(t.get("technical_voided")) for t in updated),26)
        self.assertFalse(updated[-1].get("technical_voided"))
        self.assertEqual(sum(float(t["pnl_usd"]) for t in updated if t.get("technical_voided")),
                         sum(float(t["pnl_usd"]) for t in all_trades[:26]))
        self.assertIsNone(v.create_correction(updated,[correction]))

    def test_rejects_unexpected_count_or_mixed_ledger(self):
        with self.assertRaisesRegex(ValueError,"Expected"):
            v.create_correction(fixture()[:25],[])
        tampered=fixture()
        tampered[-1]["technical_voided"]=True
        with self.assertRaisesRegex(ValueError,"already voided"):
            v.create_correction(tampered,[])
        duplicates=fixture()
        duplicates[1]["id"]=duplicates[0]["id"]
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            v.create_correction(duplicates,[])

    def test_state_persistence_and_display_metrics_are_separate(self):
        import market_monitor as m
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"state.json"
            fake=m.State(load_state=False)
            c=v.create_correction(fixture(),[])
            fake.history=v.apply_flags(fixture(),c,123)
            fake.demo_balance_usd=1128.68161534
            fake.paper_corrections=[c]
            with patch.object(m,"STATE_PATH",target):
                fake.save()
                reloaded=m.State()
                self.assertEqual(len(reloaded.paper_corrections),1)
                self.assertEqual(len([t for t in reloaded.history if t.get("technical_voided")]),26)
                self.assertAlmostEqual(reloaded.demo_balance_usd,1128.68161534)


if __name__=="__main__":unittest.main()

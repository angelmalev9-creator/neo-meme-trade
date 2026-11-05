"""Authenticated PAPER-only comparison route: isolation and read-only guarantees."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import user_gateway as gateway


TZ = ZoneInfo("Europe/Sofia")


def ms(day, hour, minute=0):
    return int(dt.datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp() * 1000)


def filled_trade(uid, number, opened, pnl, *, technical_voided=False):
    return {
        "id": f"{uid}-{number}",
        "session_id": f"paper-{uid}", "trade_no": number,
        "address": f"mint-{uid}", "pairAddress": "pool",
        "symbol": "TOK", "opened_at": opened,
        "closed_at": opened + 60_000, "pnl_usd": pnl,
        "entry_roundtrip_pnl_pct": -1.8,
        "technical_voided": technical_voided,
    }


class PrivateShadowRoute(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch_root = patch.object(gateway, "USER_ENGINE_ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def state(self, uid, records):
        dest = self.root / uid / "state.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"demo_session_id": f"paper-{uid}", "history": records}))
        return dest

    def test_read_only_and_per_account_isolation(self):
        a=self.state("user-a", [filled_trade("user-a",1,ms(5,13),-5)])
        b=self.state("user-b", [filled_trade("user-b",1,ms(5,13),+7)])
        before_a=a.read_bytes()
        before_b=b.read_bytes()
        result_a=gateway.authenticated_shadow_report("user-a", "today", at_ms=ms(5,15))
        result_b=gateway.authenticated_shadow_report("user-b", "today", at_ms=ms(5,15))
        self.assertEqual(result_a["cases"]["ORIGINAL_OBSERVED"]["observed_recorded_pnl_usd"], -5)
        self.assertEqual(result_b["cases"]["ORIGINAL_OBSERVED"]["observed_recorded_pnl_usd"], 7)
        self.assertEqual(a.read_bytes(), before_a)
        self.assertEqual(b.read_bytes(), before_b)
        self.assertTrue(result_a["report_only"])
        self.assertEqual(result_a["source"], "AUTHENTICATED_ACCOUNT_PAPER_STATE")

    def test_since_restore_multi_day_window(self):
        rows = [
            filled_trade("user-a",1,ms(5,11),-5),
            filled_trade("user-a",2,ms(5,13),-4),
            filled_trade("user-a",3,ms(6,10),+9),
            filled_trade("user-a",4,ms(6,15),-100),
            filled_trade("user-a",5,ms(7,10),-100),
        ]
        self.state("user-a", rows)
        res=gateway.authenticated_shadow_report("user-a", "since_restore", at_ms=ms(6,12))
        base=res["cases"]["ORIGINAL_OBSERVED"]
        self.assertEqual(base["observed_entries"],2)
        self.assertEqual(base["observed_recorded_pnl_usd"],5)
        self.assertEqual(res["period"],"since_restore")

    def test_technical_void_is_always_excluded(self):
        self.state("user-a", [
            filled_trade("user-a",1,ms(5,13),-200,technical_voided=True),
            filled_trade("user-a",2,ms(5,14),+6),
        ])
        result=gateway.authenticated_shadow_report("user-a","today",at_ms=ms(5,16))
        self.assertEqual(result["cases"]["ORIGINAL_OBSERVED"]["observed_entries"],1)
        self.assertEqual(result["cases"]["ORIGINAL_OBSERVED"]["observed_recorded_pnl_usd"],6)

    def test_unknown_period_and_unknown_state_fail_closed(self):
        with self.assertRaises(ValueError):
            gateway.authenticated_shadow_report("user-a","unsafe")
        with self.assertRaises(FileNotFoundError):
            gateway.authenticated_shadow_report("user-missing","today",at_ms=ms(5,16))


if __name__ == "__main__":
    unittest.main()

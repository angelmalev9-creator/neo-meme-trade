"""Independent read-only archive audit checks; no real quote reconstruction."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo
from scripts.oct4_paper_audit import audit


class AuditTests(unittest.TestCase):
    def test_day_filter_and_cost_sensitivity(self):
        tz=ZoneInfo("Europe/Sofia")
        ts=lambda day: int(dt.datetime(2026,10,day,12,tzinfo=tz).timestamp()*1000)
        rows=[
            {"id":"a","session_id":"one","closed_at":ts(4),"pnl_usd":10,
             "notional_usd":200,"strategy_id":"ORDER_FLOW_ADAPTIVE"},
            {"id":"b","session_id":"two","closed_at":ts(4),"pnl_usd":-5,
             "notional_usd":200,"execution_mode":"JUPITER_QUOTE_V2",
             "jupiter_exit_quote_at":ts(4)},
            {"id":"c","session_id":"three","closed_at":ts(5),"pnl_usd":100,
             "notional_usd":200},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"archive.json"
            p.write_text(json.dumps({"history":rows}))
            result=audit(p)
        self.assertEqual(result["recorded"]["trades"],2)
        self.assertEqual(result["recorded"]["net_pnl_usd"],5)
        self.assertEqual(result["execution_modes"]["UNRECORDED"]["trades"],1)
        self.assertEqual(result["jupiter_recorded_exit_quotes"],1)
        self.assertEqual(result["illustrative_incremental_roundtrip_friction"]["1%"]["combined_paper_pnl_after_extra_cost_usd"],3)
        self.assertEqual(result["duplicate_ids"],0)

    def test_duplicate_trade_ids_reported_and_no_rows_out_of_day(self):
        tz=ZoneInfo("Europe/Sofia")
        stamp=int(dt.datetime(2026,10,4,9,tzinfo=tz).timestamp()*1000)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"a.json"
            p.write_text(json.dumps({"trades":[
                {"id":"dup","closed_at":stamp,"pnl_usd":1,"notional_usd":200},
                {"id":"dup","closed_at":stamp+1000,"pnl_usd":-1,"notional_usd":200}
            ]}))
            report=audit(p)
        self.assertEqual(report["duplicate_ids"],1)
        self.assertEqual(report["recorded"]["flats"],0)
        self.assertEqual(report["recorded"]["net_pnl_usd"],0)


if __name__=="__main__":unittest.main()

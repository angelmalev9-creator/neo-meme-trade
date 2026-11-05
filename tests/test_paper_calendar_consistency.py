"""PAPER Sofia calendar reporting must not silently change UTC risk controls."""
import datetime as dt
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
import market_monitor as market


def ms(value: dt.datetime):
    return int(value.timestamp() * 1000)


class PaperCalendarReporting(unittest.TestCase):
    def test_sofia_midnight_is_not_utc_midnight(self):
        # UTC Nov 4 22:30 is already Nov 5 in Sofia (UTC+2).
        date = dt.datetime(2026, 11, 4, 22, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(market.paper_report_day(ms(date)), '2026-11-05')

    def test_technical_voids_not_counted_as_strategy_profit(self):
        start = ms(dt.datetime(2026,11,4,22,30,tzinfo=dt.timezone.utc))
        next_day = ms(dt.datetime(2026,11,5,22,30,tzinfo=dt.timezone.utc))
        trades = [
            {'closed_at': start, 'pnl_usd': -12, 'technical_voided': True},
            {'closed_at': start+1000, 'pnl_usd': 4},
            {'closed_at': next_day, 'pnl_usd': 1000},
        ]
        result = market.paper_day_results(trades, start+3000)
        self.assertEqual(result['day'],'2026-11-05')
        self.assertEqual(result['timezone'],'Europe/Sofia')
        self.assertEqual(result['valid_pnl_usd'],4)
        self.assertEqual(result['raw_pnl_usd'],-8)
        self.assertEqual(result['technical_voided_count'],1)
        self.assertEqual(result['valid_trade_count'],1)
        self.assertEqual(result['raw_trade_count'],2)

    def test_existing_utc_risk_limit_not_reset_by_reporting_day(self):
        state = market.State(load_state=False)
        state.demo_balance_usd = 940
        state.risk_day_start_balance_usd = 1000
        state.risk_day_key = dt.datetime.now(dt.timezone.utc).date().isoformat()
        self.assertEqual(round(state.risk_day_pnl(),2), -60)
        state.history=[{'closed_at':ms(dt.datetime(2026,11,4,22,30,tzinfo=dt.timezone.utc)),
                        'pnl_usd':10}]
        with patch.object(market,'now_ms',return_value=ms(dt.datetime(2026,11,5,8,tzinfo=dt.timezone.utc))):
            self.assertEqual(state.realized_today(), 10)
        self.assertEqual(market.PAPER_RISK_TZ, 'UTC')


if __name__=='__main__':
    unittest.main()

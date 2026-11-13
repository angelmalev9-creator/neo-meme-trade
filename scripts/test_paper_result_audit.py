"""Synthetic tests for read-only NEO PAPER outcome audit (no network/orders)."""
import unittest
from scripts.paper_result_audit import audit

def trade(at, pnl, pct, *, symbol='A', addr='M', pair='P',
          reason='ORDERFLOW_EXIT', open_delta=5000, **extra):
    return dict(closed_at=at, opened_at=at-open_delta, pnl_usd=pnl, pnl_pct=pct,
                stop_loss_pct=5, address=addr, pairAddress=pair,
                symbol=symbol, exit_reason=reason, **extra)

class AuditTests(unittest.TestCase):
    def test_outcomes_and_stop_overshoot(self):
        now=1794549000000
        rows=[trade(now,-10,-5.5),trade(now-10000,3,1.5,open_delta=25000),
              trade(now-20000,-2,-1,open_delta=40000)]
        result=audit({'history':rows})
        self.assertEqual((result['wins'],result['losses']),(1,2))
        self.assertEqual(result['stop_overshoots_more_than_0_25pp'],1)
        self.assertEqual(result['fast_closes_under_10_seconds'],1)
        self.assertAlmostEqual(result['net_pnl_usd'],-9)
        self.assertFalse(result['validated_profitable_strategy'])
        self.assertEqual(result['top_repeat_pools'][0]['trades'],3)
    def test_voided_and_nan_rows_ignored(self):
        rows=[trade(1794549000000,1,2),
              trade(1794549000001,-100,-50,technical_voided=True),
              {'closed_at':1794549000002,'pnl_usd':'NaN','pnl_pct':2},'invalid']
        self.assertEqual(audit({'history':rows})['trades_in_sample'],1)
    def test_costs_not_substituted_for_realized_results(self):
        result=audit({'history':[trade(1794549000000,-2,-1,entry_roundtrip_pnl_pct=-2.3)]})
        self.assertEqual(result['initial_roundtrip_mark_average_pct'],-2.3)
        self.assertEqual(result['net_pnl_usd'],-2)
    def test_empty_sample_is_not_a_win_rate(self):
        result=audit({'history':[]})
        self.assertIsNone(result['win_rate_pct'])
    def test_break_even_sample_arithmetic(self):
        result=audit({'history':[trade(1794549000000,10,5),trade(1794548990000,-5,-2)]})
        self.assertEqual(result['break_even_win_rate_given_sample_payout_pct'],33.333)
    def test_limit_sample_and_worst_case(self):
        rows=[trade(1794549000000+i,-float(i+1),-float(i+5)) for i in range(20)]
        result=audit({'history':rows},max_trades=5)
        self.assertEqual(result['trades_in_sample'],5)
        self.assertEqual(result['worst_stop_overshoot']['pnl_pct'],-24)

if __name__=='__main__':
    unittest.main()

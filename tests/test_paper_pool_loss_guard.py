"""Exact-pool loss-streak protection for PAPER; no writes or market calls."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import paper_pool_loss_guard as guard

NOW = 1793887200000
MIN = 60_000
HOUR = 60*MIN
SESSION = 'oct4-paper-session'


def make_row(i, closed, pnl, *, mint='mint-A', pair='pool-1', session=SESSION,
             voided=False, symbol='GTA'):
    return {'id':f'{session}:{i}', 'trade_no':i, 'session_id':session,
            'address':mint, 'pairAddress':pair, 'symbol':symbol,
            'closed_at':closed, 'opened_at':closed-20_000,
            'pnl_usd':pnl, 'technical_voided':voided}


class ExactPoolLossGuardTests(unittest.TestCase):
    def check(self, history, *, now=NOW):
        return guard.blocked_pools(history, now, SESSION)

    def test_two_recent_losses_block_exact_pool_for_three_hours(self):
        history = [make_row(1,NOW-50*MIN,-12),make_row(2,NOW-10*MIN,-4)]
        active=self.check(history)
        self.assertEqual(set(active),{('mint-A','pool-1')})
        self.assertEqual(active['mint-A','pool-1']['blocked_until'],NOW-10*MIN+guard.BLOCK_MS)
        self.assertEqual(active['mint-A','pool-1']['losses'],2)
        self.assertEqual(active['mint-A','pool-1']['reason'],guard.REJECTION_CODE)

    def test_other_pool_same_mint_not_blocked(self):
        history=[make_row(1,NOW-40*MIN,-4),make_row(2,NOW-10*MIN,-6)]
        self.assertNotIn(('mint-A','pool-2'),self.check(history))

    def test_same_ticker_different_mint_not_blocked(self):
        history=[make_row(1,NOW-40*MIN,-4),make_row(2,NOW-10*MIN,-6)]
        self.assertNotIn(('mint-B','pool-1'),self.check(history))

    def test_one_loss_alone_is_not_enough(self):
        self.assertEqual(self.check([make_row(1,NOW-10*MIN,-8)]),{})

    def test_recent_win_interrupts_streak(self):
        history=[make_row(1,NOW-60*MIN,-8),make_row(2,NOW-40*MIN,-4),
                 make_row(3,NOW-10*MIN,5)]
        self.assertEqual(self.check(history),{})

    def test_break_on_breakeven_as_well(self):
        history=[make_row(1,NOW-60*MIN,-8),make_row(2,NOW-40*MIN,-4),
                 make_row(3,NOW-10*MIN,0)]
        self.assertEqual(self.check(history),{})

    def test_technical_voids_do_not_count(self):
        history=[make_row(1,NOW-40*MIN,-8,voided=True),
                 make_row(2,NOW-10*MIN,-4)]
        self.assertEqual(self.check(history),{})

    def test_session_isolation(self):
        history=[make_row(1,NOW-40*MIN,-8,session='old-session'),
                 make_row(2,NOW-10*MIN,-4)]
        self.assertEqual(self.check(history),{})

    def test_future_closes_are_not_used(self):
        history=[make_row(1,NOW-40*MIN,-8),make_row(2,NOW+20*MIN,-4)]
        self.assertEqual(self.check(history),{})

    def test_loss_streak_expires_after_three_hours(self):
        history=[make_row(1,NOW-5*HOUR,-8),make_row(2,NOW-4*HOUR,-4)]
        self.assertEqual(self.check(history),{})

    def test_stale_losses_over_six_hours_do_not_count(self):
        history=[make_row(1,NOW-8*HOUR,-8),make_row(2,NOW-7*HOUR,-4)]
        self.assertEqual(self.check(history),{})

    def test_close_order_not_insert_order(self):
        history=[make_row(4,NOW-2*MIN,-4),make_row(1,NOW-70*MIN,2),
                 make_row(2,NOW-40*MIN,-8)]
        self.assertIn(('mint-A','pool-1'),self.check(history))

    def test_nan_and_malformed_pnl_ignored(self):
        history=[make_row(1,NOW-40*MIN,float('nan')),make_row(2,NOW-10*MIN,-4)]
        self.assertEqual(self.check(history),{})
        history[0]['pnl_usd']='bad'
        self.assertEqual(self.check(history),{})

    def test_missing_pool_identity_does_not_blacklist_entire_ticker(self):
        history=[make_row(1,NOW-40*MIN,-5,pair=''),
                 make_row(2,NOW-10*MIN,-4,pair='')]
        self.assertEqual(self.check(history),{})
        self.assertIsNone(guard.pool_key({'address':'mint-A'}))

    def test_description_metadata_and_disable(self):
        rows=[make_row(1,NOW-60*MIN,-8),make_row(2,NOW-10*MIN,-4)]
        result=guard.description(rows,NOW,SESSION,enabled=True)
        self.assertEqual(result['blocked_pool_count'],1)
        self.assertEqual(result['blocked_pools'][0]['symbol'],'GTA')
        self.assertEqual(result['scope'],'PAPER_ONLY_SAME_MINT_AND_POOL_CURRENT_SESSION')
        self.assertEqual(guard.description(rows,NOW,SESSION,enabled=False)['blocked_pools'],[])
        self.assertEqual(result['lookback_hours'],6)
        self.assertEqual(result['block_hours'],3)

    def test_history_immutable(self):
        rows=[make_row(1,NOW-40*MIN,-8),make_row(2,NOW-10*MIN,-4)]
        saved=json.dumps(rows,sort_keys=True)
        self.check(rows)
        self.assertEqual(json.dumps(rows,sort_keys=True),saved)

    def test_paper_profile_is_enforced_but_nonpaper_disabled(self):
        script='import market_monitor as m; print(m.PAPER_POOL_LOSS_GUARD_ENABLED)'
        env=os.environ.copy()
        env.update(NEO_STRATEGY_PROFILE='ORDER_FLOW_ADAPTIVE_OCT4',
                   NEO_ENGINE_MODE='PAPER', NEO_EXECUTION_MODE='PAPER')
        env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'backend')
        live=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(live.returncode,0,live.stderr)
        self.assertEqual(live.stdout.strip(),'True')
        env['NEO_ENGINE_MODE']='LIVE'
        disabled=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(disabled.returncode,0,disabled.stderr)
        self.assertEqual(disabled.stdout.strip(),'False')
        env['NEO_ENGINE_MODE']='PAPER'
        env['NEO_PAPER_POOL_LOSS_GUARD']='false'
        disabled_explicit=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(disabled_explicit.returncode,0,disabled_explicit.stderr)
        self.assertEqual(disabled_explicit.stdout.strip(),'False')


if __name__ == '__main__':
    unittest.main()

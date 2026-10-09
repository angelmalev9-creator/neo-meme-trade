"""Offline regression tests for the restored 2026-10-04 ORDER_FLOW_ADAPTIVE strategy.

Quotes, flow and safety results are fixtures; no test calls an external API
and nothing here can submit a swap. Section numbers refer to the restore
handoff's required-regression list.
"""
import copy
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import market_monitor as m
import order_flow_adaptive_oct4 as oct4
import engine_exit_policy as exit_policy

A, B, C = 'A' * 44, 'B' * 44, 'C' * 44
NOW = 1_800_000_000_000


def setUpModule():
    m.apply_strategy_profile(oct4.PROFILE)


def good_coin(now=NOW):
    return {'address': A, 'pairAddress': B, 'symbol': 'FIXTURE', 'name': 'Offline fixture',
            'priceUsd': 2.0, 'priceNative': .02, 'liquidityUsd': 200_000, 'marketCap': 1_000_000,
            'ageMinutes': 60, 'score': 90, 'priceChange': {'m5': 5, 'h1': 8},
            'txns': {'m5': {'buys': 10, 'sells': 3}}, 'volume': {'h1': 50_000},
            'signals': [], 'updatedAt': now}


def good_flow():
    return {'quality': 'COMPLETE', 'trades': 4, 'buys': 3, 'sells': 1, 'buy_usd': 300, 'sell_usd': 100,
            'buy_sell_usd_ratio': 2.0, 'unique_wallets': 4, 'buyer_wallets': 3,
            'wallet_buy_sell_ratio': 3, 'max_sell_usd': 50}


def context(conviction, **extra):
    return {'conviction': conviction, **oct4.hold_mode(conviction), 'fast_flow': {}, 'slow_flow': {}, **extra}


class EntryFilter(unittest.TestCase):
    """ORDER_FLOW_GOLD_V1: the entry rule in force through the 2026-10-04 trading day."""

    def rejected(self, coin=None, flow=None, conviction=80):
        return oct4.signal_rejections(coin or good_coin(), flow or good_flow(),
                                      {'conviction': conviction}, now=NOW)

    def test_01_valid_historical_entry_passes(self):
        self.assertEqual(self.rejected(), [])

    def test_02_score_84_rejects_and_85_passes(self):
        self.assertEqual(self.rejected({**good_coin(), 'score': 84}), ['score'])
        self.assertEqual(self.rejected({**good_coin(), 'score': 85}), [])

    def test_03_liquidity_below_15k_rejects(self):
        self.assertEqual(self.rejected({**good_coin(), 'liquidityUsd': 14_999}), ['liquidity'])
        self.assertEqual(self.rejected({**good_coin(), 'liquidityUsd': 15_000}), [])

    def test_04_conviction_below_75_rejects(self):
        self.assertEqual(self.rejected(conviction=74.9), ['conviction'])
        self.assertEqual(self.rejected(conviction=75), [])

    def test_05_flow_ratio_below_1_30_rejects(self):
        self.assertEqual(self.rejected(flow={**good_flow(), 'buy_sell_usd_ratio': 1.29}), ['flow_ratio'])
        self.assertEqual(self.rejected(flow={**good_flow(), 'buy_sell_usd_ratio': 1.30}), [])

    def test_06_flow_needs_three_trades_and_one_wallet(self):
        self.assertEqual(self.rejected(flow={**good_flow(), 'trades': 2}), ['flow_count'])
        self.assertEqual(self.rejected(flow={**good_flow(), 'trades': 3}), [])
        self.assertEqual(self.rejected(flow={**good_flow(), 'unique_wallets': 0}), ['wallet_count'])
        self.assertEqual(self.rejected(flow={**good_flow(), 'unique_wallets': 1}), [])

    def test_07_large_sell_protection(self):
        # Limit is max($750, 80% of verified buy USD); the sell must be strictly below it.
        self.assertEqual(self.rejected(flow={**good_flow(), 'max_sell_usd': 750}), ['large_sells'])
        self.assertEqual(self.rejected(flow={**good_flow(), 'max_sell_usd': 749.99}), [])
        big = {**good_flow(), 'buy_usd': 2000}
        self.assertEqual(self.rejected(flow={**big, 'max_sell_usd': 1599}), [])
        self.assertEqual(self.rejected(flow={**big, 'max_sell_usd': 1600}), ['large_sells'])

    def test_remaining_named_checks_and_boundaries(self):
        coin, flow = good_coin(), good_flow()
        cases = {
            'invalid_pair': ({**coin, 'pairAddress': 'not-a-pool'}, flow),
            'invalid_price': ({**coin, 'priceUsd': 0}, flow),
            'stale_feed': ({**coin, 'updatedAt': NOW - 30_001}, flow),
            'momentum': ({**coin, 'priceChange': {'m5': 25.1}}, flow),
        }
        for reason, (c, f) in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(self.rejected(c, f), [reason])
        self.assertEqual(self.rejected({**coin, 'priceChange': {'m5': -5.1}}), ['momentum'])
        for m5 in (-5, 25):
            self.assertEqual(self.rejected({**coin, 'priceChange': {'m5': m5}}), [])
        self.assertEqual(self.rejected({**coin, 'updatedAt': NOW - 30_000}), [])

    def test_checks_that_belonged_only_to_the_late_evening_filter_are_gone(self):
        # ORDER_FLOW_BALANCED_V4 also demanded these; the trading-day rule never did.
        coin = {**good_coin(), 'liquidityUsd': 15_000, 'marketCap': 50_000_000,       # liquidity/mcap 0.0003
                'priceChange': {'m5': 5, 'h1': -80}, 'txns': {'m5': {'buys': 1, 'sells': 9}}}
        flow = {'quality': 'COMPLETE', 'trades': 3, 'buy_usd': 20, 'sell_usd': 10, 'buy_sell_usd_ratio': 1.3,
                'unique_wallets': 1, 'buyer_wallets': 1, 'wallet_buy_sell_ratio': .2, 'max_sell_usd': 10}
        self.assertEqual(self.rejected(coin, flow, conviction=75), [])
        self.assertEqual(set(oct4.ENTRY_LIMITS), {
            'min_score', 'min_liquidity_usd', 'min_conviction', 'min_m5_pct', 'max_m5_pct', 'min_flow_trades',
            'min_flow_buy_sell_usd_ratio', 'min_unique_wallets', 'large_sell_floor_usd', 'large_sell_buy_fraction'})

    def test_27_missing_evidence_fails_closed(self):
        self.assertIn('flow_quality', self.rejected(flow={**good_flow(), 'quality': 'DEGRADED'}))
        self.assertIn('flow_quality', self.rejected(flow={k: v for k, v in good_flow().items() if k != 'quality'}))
        coin = good_coin()
        del coin['priceChange']
        self.assertEqual(self.rejected(coin), ['momentum'])
        self.assertEqual(oct4.signal_rejections(good_coin(), good_flow(), {}, now=NOW), ['conviction'])
        empty = oct4.signal_rejections({}, {}, {}, now=NOW)
        self.assertEqual(len(empty), 12)


class ConvictionModel(unittest.TestCase):
    """Handoff tests 8-12 plus the section 41 scoring table."""

    def score(self, coin=None, fast=None, slow=None, entry_liquidity=None):
        neutral = {'buy_sell_usd_ratio': 1.2, 'unique_wallets': 3}
        return oct4.conviction_score(coin or {'score': 90, 'liquidityUsd': 100, 'priceChange': {'m5': 15, 'h1': 200},
                                              'txns': {'m5': {'buys': 1, 'sells': 1}}},
                                     fast or neutral, slow or neutral, entry_liquidity)['conviction']

    def test_neutral_inputs_score_base_plus_liquidity_only(self):
        self.assertEqual(self.score(), 53.0)  # 50 base, +3 liquidity held vs entry

    def test_each_adjustment_matches_the_recovered_table(self):
        base = self.score()
        coin = {'score': 90, 'liquidityUsd': 100, 'priceChange': {'m5': 15, 'h1': 200},
                'txns': {'m5': {'buys': 1, 'sells': 1}}}
        neutral = {'buy_sell_usd_ratio': 1.2, 'unique_wallets': 3}
        for ratio, delta in ((3.0, 18), (2.0, 12), (1.4, 6), (0.99, -10), (0.79, -20)):
            self.assertEqual(self.score(fast={**neutral, 'buy_sell_usd_ratio': ratio}) - base, delta, ratio)
        for ratio, delta in ((2.0, 12), (1.4, 7), (0.99, -7), (0.79, -15)):
            self.assertEqual(self.score(slow={**neutral, 'buy_sell_usd_ratio': ratio}) - base, delta, ratio)
        for wallets, delta in ((10, 6), (5, 3), (2, -5)):
            self.assertEqual(self.score(slow={**neutral, 'unique_wallets': wallets}) - base, delta, wallets)
        for repeat, delta in ((3, 6), (1, 3)):
            self.assertEqual(self.score(slow={**neutral, 'repeat_buy_wallets': repeat}) - base, delta, repeat)
        self.assertEqual(self.score(slow={**neutral, 'whale_buy_usd': 1300, 'whale_sell_usd': 1000}) - base, 6)
        self.assertEqual(self.score(slow={**neutral, 'whale_buy_usd': 100, 'whale_sell_usd': 750}) - base, -8)
        self.assertEqual(self.score(slow={**neutral, 'whale_buy_usd': 0, 'whale_sell_usd': 749}) - base, 0)
        for m5, delta in ((0, 8), (10, 8), (-2, 2), (20.1, -8), (-5.1, -15), (-4, 0)):
            self.assertEqual(self.score({**coin, 'priceChange': {'m5': m5, 'h1': 200}}) - base, delta, m5)
        for h1, delta in ((0, 4), (120, 4), (-15.1, -8), (250.1, -5)):
            self.assertEqual(self.score({**coin, 'priceChange': {'m5': 15, 'h1': h1}}) - base, delta, h1)
        for buys, sells, delta in ((14, 10, 8), (11, 10, 4), (7, 10, -8)):
            self.assertEqual(self.score({**coin, 'txns': {'m5': {'buys': buys, 'sells': sells}}}) - base, delta)
        for entry_liquidity, delta in ((105, 0), (112, -3 - 7), (126, -3 - 15)):
            self.assertEqual(self.score(entry_liquidity=entry_liquidity) - base, delta, entry_liquidity)
        self.assertEqual(self.score({**coin, 'score': 95}) - base, 4)
        self.assertEqual(self.score({**coin, 'score': 84.9}) - base, -4)

    def test_conviction_is_clamped_to_0_100(self):
        bull = {'buy_sell_usd_ratio': 5, 'unique_wallets': 20, 'repeat_buy_wallets': 5, 'whale_buy_usd': 5000}
        coin = {'score': 99, 'liquidityUsd': 100, 'priceChange': {'m5': 5, 'h1': 50},
                'txns': {'m5': {'buys': 30, 'sells': 10}}}
        self.assertEqual(self.score(coin, bull, bull), 100.0)
        bear = {'buy_sell_usd_ratio': .1, 'unique_wallets': 1, 'whale_sell_usd': 5000}
        coin = {'score': 50, 'liquidityUsd': 50, 'priceChange': {'m5': -20, 'h1': -50},
                'txns': {'m5': {'buys': 1, 'sells': 10}}}
        self.assertEqual(self.score(coin, bear, bear, entry_liquidity=100), 0.0)

    def test_08_to_12_hold_modes(self):
        expected = [
            (100, 'RUNNER', 60, None, 15, 7), (85, 'RUNNER', 60, None, 15, 7),
            (84.9, 'STRONG', 30, None, 12, 6), (72, 'STRONG', 30, None, 12, 6),
            (71.9, 'NORMAL', 15, 20, 9, 5), (58, 'NORMAL', 15, 20, 9, 5),
            (57.9, 'CAUTIOUS', 8, 14, 7, 4), (45, 'CAUTIOUS', 8, 14, 7, 4),
            (44.9, 'WEAK', 4, 8, 5, 3), (0, 'WEAK', 4, 8, 5, 3),
        ]
        for conviction, mode, hold, target, arm, trail in expected:
            with self.subTest(conviction=conviction):
                self.assertEqual(oct4.hold_mode(conviction), {
                    'mode': mode, 'max_hold_minutes': hold, 'target_pct': target,
                    'trail_arm_pct': arm, 'trail_pct': trail})


class ExitDecision(unittest.TestCase):
    """Handoff tests 13-19: section 43 exit priority."""

    def decide(self, conviction, net, *, signal='same', peak=None, hold=1, fast=None):
        ctx = context(conviction, fast_flow=fast or {})
        signal_pct = net if signal == 'same' else signal
        peak_signal = None if signal_pct is None else max(signal_pct, peak if peak is not None else signal_pct)
        return oct4.exit_reason(ctx, net_pct=net, peak_net_pct=max(net, peak if peak is not None else net),
                                hold_minutes=hold, signal_pct=signal_pct, peak_signal_pct=peak_signal)

    SELL_HEAVY = {'trades': 5, 'sells': 4, 'sell_usd': 900, 'buy_usd': 100}

    def test_13_stop_loss_takes_priority_over_everything(self):
        self.assertEqual(self.decide(20, -5.0, hold=500, fast=self.SELL_HEAVY), 'STOP_LOSS')
        self.assertEqual(self.decide(95, -5.0), 'STOP_LOSS')
        self.assertIsNone(self.decide(95, -4.99))
        # The stop reads the net executable mark only, never the chart.
        self.assertEqual(self.decide(95, -5.0, signal=+3.0), 'STOP_LOSS')
        self.assertIsNone(self.decide(95, -1.0, signal=-9.0))

    def test_14_runner_and_strong_are_not_closed_at_plus_18(self):
        self.assertIsNone(self.decide(90, 18.0))
        self.assertIsNone(self.decide(75, 18.0))
        self.assertIsNone(self.decide(90, 60.0))
        self.assertEqual(self.decide(60, 20.0), 'ADAPTIVE_TP_20')
        self.assertIsNone(self.decide(60, 19.9, peak=19.9))
        self.assertEqual(self.decide(50, 14.0), 'ADAPTIVE_TP_14')
        self.assertEqual(self.decide(40, 8.0), 'ADAPTIVE_TP_8')

    def test_15_strong_sell_flow_causes_orderflow_exit(self):
        self.assertEqual(self.decide(80, 2.9, fast=self.SELL_HEAVY), 'ORDERFLOW_EXIT')
        self.assertIsNone(self.decide(80, 3.0, fast=self.SELL_HEAVY))
        for weaker in ({'trades': 3}, {'sells': 2}, {'sell_usd': 199}, {'buy_usd': 451}):
            with self.subTest(weaker=weaker):
                self.assertIsNone(self.decide(80, 1.0, fast={**self.SELL_HEAVY, **weaker}))

    def test_16_conviction_collapse_causes_conviction_exit(self):
        self.assertEqual(self.decide(34.9, -0.1), 'CONVICTION_EXIT')
        self.assertIsNone(self.decide(35, -0.1))
        self.assertIsNone(self.decide(34.9, 0.0))
        # Historical basis is the observed pool price; the net mark is the fallback.
        self.assertEqual(self.decide(34.9, 1.0, signal=-0.1), 'CONVICTION_EXIT')
        self.assertIsNone(self.decide(34.9, -2.0, signal=+0.5))
        self.assertEqual(self.decide(34.9, -2.0, signal=None), 'CONVICTION_EXIT')

    def test_conviction_profit_lock(self):
        self.assertEqual(self.decide(49.9, 2.1, peak=10.0), 'CONVICTION_PROFIT_LOCK')
        self.assertIsNone(self.decide(50, 9.9, peak=10.0))
        self.assertIsNone(self.decide(49.9, 6.0, peak=6.4))

    def test_17_adaptive_trailing(self):
        # STRONG: arms at +12%, exits 6% below the peak price.
        self.assertIsNone(self.decide(75, 11.0, peak=11.9))
        self.assertIsNone(self.decide(75, 13.0, peak=20.0))            # floor is 120 * .94 = 112.8
        self.assertEqual(self.decide(75, 12.8, peak=20.0), 'ADAPTIVE_TRAILING')
        # RUNNER: arms at +15%, 7% trail -> floor 150 * .93 = 139.5.
        self.assertIsNone(self.decide(90, 39.6, peak=50.0))
        self.assertEqual(self.decide(90, 39.5, peak=50.0), 'ADAPTIVE_TRAILING')

    def test_18_adaptive_max_hold_only_below_strong_conviction(self):
        self.assertEqual(self.decide(60, 1.0, hold=15), 'ADAPTIVE_MAX_HOLD')
        self.assertIsNone(self.decide(60, 1.0, hold=14.9))
        self.assertEqual(self.decide(40, 1.0, hold=4), 'ADAPTIVE_MAX_HOLD')
        self.assertIsNone(self.decide(72, 1.0, hold=119))
        self.assertIsNone(self.decide(90, 1.0, hold=119))

    def test_19_absolute_max_hold_is_120_minutes(self):
        self.assertEqual(self.decide(95, 1.0, hold=120), 'ABSOLUTE_MAX_HOLD')
        self.assertEqual(self.decide(72, 1.0, hold=120), 'ABSOLUTE_MAX_HOLD')

    def test_priority_order(self):
        self.assertEqual(self.decide(30, -1.0, fast=self.SELL_HEAVY), 'CONVICTION_EXIT')
        self.assertEqual(self.decide(40, 8.0, peak=12, hold=500), 'ADAPTIVE_TP_8')
        self.assertEqual(self.decide(60, 12.0, peak=30, hold=500), 'ADAPTIVE_TRAILING')


class EngineHarness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='neo-oct4-account-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = [NOW]
        for p in [patch.object(m, 'STATE_PATH', self.root / 'state.json'),
                  patch.object(m, 'AUDIT_PATH', self.root / 'audit.jsonl'),
                  patch.object(m, 'LIVE_TAPE_PATH', self.root / 'tape.json'),
                  patch.object(m, 'ALL_TIME_HISTORY_PATH', self.root / 'all_time_history.json'),
                  patch.object(m, 'now_ms', side_effect=lambda: self.clock[0]),
                  patch.object(m, 'sol_usd_market_price', return_value=100),
                  patch.object(m.pumpswap_stop, 'prime_positions')]:
            p.start(); self.addCleanup(p.stop)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.addCleanup(self.monitor.stop)
        self.coin = good_coin()
        self.flow = good_flow()
        self.context = context(80)
        self.safety = {'status': 'pass', 'metrics': {'decimals': 6, 'token_account_rent_lamports': 1_650_000}}
        self.quote_age_ms = 0

    def entry_patches(self):
        def prepared(address, pair, notional):
            return ({'token_raw_expected': int(notional / 2 * 1e6), 'token_raw_amount': int(notional / 2 * 1e6),
                     'input_usdc_raw': int(notional * 1e6), 'price_impact_pct': .2,
                     'quoted_at': self.clock[0] - self.quote_age_ms, 'raw_quote': {'fixture': True}},
                    {'expected_usdc': notional - 1, 'floor_usdc': notional - 2})
        self.mocks = {}
        for name, p in {
            'flow': patch.object(m.STATE, 'live_flow', side_effect=lambda *a, **k: self.flow),
            'context': patch.object(self.monitor, 'market_context', side_effect=lambda *a, **k: self.context),
            'safety': patch.object(m.rug_guard, 'check', side_effect=lambda *a, **k: self.safety),
            'price': patch.object(m.price_integrity, 'check', return_value={'status': 'pass'}),
            'prepare': patch.object(m.paper_quotes, 'prepare_entry', side_effect=prepared),
        }.items():
            self.mocks[name] = p.start(); self.addCleanup(p.stop)

    def rejections(self):
        return m.STATE.entry_diagnostics['rejections']

    def position(self, **overrides):
        pos = {'id': 'position-1', 'address': A, 'pairAddress': B, 'symbol': 'FIXTURE', 'trade_no': 1,
               'session_id': m.STATE.demo_session_id, 'strategy_id': oct4.STRATEGY_ID,
               'entry_policy_version': oct4.ENTRY_POLICY_VERSION, 'exit_policy': oct4.EXIT_POLICY,
               'exit_policy_version': oct4.EXIT_POLICY_VERSION,
               'entry_price': 2.0, 'current_price': 2.0, 'peak_price': 2.0, 'quantity': 100,
               'notional_usd': 200, 'original_notional_usd': 200, 'capital_committed_usd': 200.23,
               'entry_network_fee_usd': .03, 'entry_account_reserve_usd': .2, 'entry_liquidity_usd': 200_000,
               'opened_at': self.clock[0], 'updated_at': self.clock[0], 'jupiter_token_raw_amount': 100_000_000,
               'execution_mode': 'JUPITER_QUOTE_V2', 'coin_snapshot': copy.deepcopy(self.coin), **overrides}
        m.STATE.positions = [pos]
        m.STATE.trade_seq = 1
        return pos

    def mark(self, price, net, conviction):
        """One position check at an observed pool price and a net sale quote."""
        self.coin = {**self.coin, 'priceUsd': price, 'updatedAt': self.clock[0]}
        quote = {'net_proceeds_usd': net, 'gross_proceeds_usd': net + .03, 'network_fee_usd': .03,
                 'dex_fee_usd': 0, 'fill_price': net / 100, 'impact_pct': .2, 'slippage_pct': .1,
                 'latency_pct': 0, 'quoted_at': self.clock[0], 'from_cache': False,
                 'execution_source': 'OFFLINE_FIXTURE'}
        with patch.object(m.paper_quotes, 'position_mark', return_value=quote) as mark, \
                patch.object(self.monitor, 'market_context', return_value=context(conviction)):
            self.monitor.update_positions({A: self.coin})
        return mark


class EngineEntries(EngineHarness):
    def setUp(self):
        super().setUp()
        self.entry_patches()

    def test_valid_entry_opens_a_200_dollar_order_flow_adaptive_position(self):
        self.monitor.maybe_open([self.coin])
        self.assertEqual(len(m.STATE.positions), 1)
        pos = m.STATE.positions[0]
        self.assertEqual(pos['notional_usd'], 200)
        self.assertEqual(pos['strategy_id'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(pos['strategy_version'], 'gold-2026-10-04')
        self.assertEqual(pos['entry_policy_version'], 'ORDER_FLOW_GOLD_V1')
        self.assertEqual(pos['learning_mode'], 'ADAPTIVE_CONTEXT_HOLD')
        self.assertEqual(pos['exit_policy'], oct4.EXIT_POLICY)
        self.assertEqual(pos['exit_policy_version'], oct4.EXIT_POLICY_VERSION)
        self.assertEqual(pos['entry_hold_mode'], 'STRONG')
        self.assertEqual(pos['entry_flow_window_seconds'], 60)
        self.assertEqual(pos['planned_stop_net_pct'], -5)
        self.assertIsNone(pos['hard_stop_net_pct'])
        self.assertIsNone(pos['take_profit_net_pct'])
        self.assertLess(pos['entry_roundtrip_pnl_pct'], 0)
        self.assertEqual(m.STATE.entry_diagnostics['status'], 'opened')
        self.assertEqual(m.STATE.entry_diagnostics['policy_version'], 'ORDER_FLOW_GOLD_V1')
        self.assertEqual((m.STATE.entry_diagnostics['safety_passed'], m.STATE.entry_diagnostics['quoted'],
                          m.STATE.entry_diagnostics['quote_returned'], m.STATE.entry_diagnostics['quote_passed']),
                         (1, 1, 1, 1))
        self.assertEqual(self.mocks['flow'].call_args.args[1], 60)

    def test_user_controls_are_recorded_on_the_new_position(self):
        self.addCleanup(m.apply_strategy_profile, oct4.PROFILE)
        m.apply_strategy_profile(m.OCT4_FIXED_PROFILE)
        m.STATE.engine_settings = m.normalize_engine_settings({
            'trade_notional_usd': 125, 'stop_loss_pct': 4.25, 'take_profit_pct': 11.5,
        })
        self.monitor.maybe_open([self.coin])
        self.assertEqual(len(m.STATE.positions), 1)
        pos = m.STATE.positions[0]
        self.assertEqual(pos['notional_usd'], 125.0)
        self.assertEqual(pos['requested_notional_usd'], 125.0)
        self.assertEqual(pos['stop_loss_pct'], 4.25)
        self.assertEqual(pos['planned_stop_net_pct'], -4.25)
        self.assertEqual(pos['take_profit_net_pct'], 11.5)
        self.assertEqual(pos['exit_policy'], 'fixed_targets')
        self.assertEqual(pos['exit_policy_version'], exit_policy.USER_FIXED_VERSION)
        self.assertEqual(pos['engine_settings_version'], exit_policy.USER_FIXED_VERSION)


    def test_post_gold_rejection_is_reported_separately(self):
        self.safety = {
            'status': 'blocked', 'reasons': ['reported_linked_insiders'],
            'metrics': {'decimals': 6, 'token_account_rent_lamports': 1_650_000},
        }
        self.monitor.maybe_open([self.coin])
        diagnostics = m.STATE.entry_diagnostics
        self.assertEqual(diagnostics['signal_passed'], 1)
        self.assertEqual(diagnostics['safety_passed'], 0)
        self.assertEqual(diagnostics['quoted'], 0)
        self.assertEqual(diagnostics['post_signal_rejections'], {'reported_linked_insiders': 1})
        self.mocks['prepare'].assert_not_called()

    def test_filter_rejections_are_named_in_diagnostics_before_any_quote(self):
        self.coin['score'] = 84
        self.flow['unique_wallets'] = 0
        self.context = context(74)
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.mocks['prepare'].assert_not_called()
        self.assertEqual(self.rejections(), {'score': 1, 'wallet_count': 1, 'conviction': 1})
        diagnostics = m.STATE.entry_diagnostics
        self.assertEqual((diagnostics['candidates'], diagnostics['evaluated'], diagnostics['signal_passed'],
                          diagnostics['quoted'], diagnostics['opened']), (1, 1, 0, 0, 0))
        self.assertEqual(diagnostics['examples'][0]['reasons'], ['score', 'wallet_count', 'conviction'])
        self.assertEqual(diagnostics['examples'][0]['metrics']['conviction'], 74)
        self.assertEqual(set(diagnostics['reason_labels']), {'score', 'wallet_count', 'conviction'})
        self.assertIn('Откази', diagnostics['message'])

    def test_20_same_token_cooldown_is_20_minutes_for_wins_and_losses(self):
        for pnl in (+9.0, -4.0):
            with self.subTest(pnl=pnl):
                m.STATE.positions = []
                m.STATE.history = [{'id': 'old', 'address': A, 'pnl_pct': pnl, 'pnl_usd': pnl * 2,
                                    'closed_at': NOW - 20 * 60_000 + 1}]
                self.monitor.maybe_open([self.coin])
                self.assertFalse(m.STATE.positions)
                self.assertEqual(self.rejections(), {'cooldown': 1})
                m.STATE.history[0]['closed_at'] = NOW - 20 * 60_000 - 1
                self.monitor.maybe_open([self.coin])
                self.assertEqual(len(m.STATE.positions), 1)
        self.assertEqual(oct4.cooldown_addresses([{'address': C, 'closed_at': NOW - 60_000}], NOW), {C})

    def test_21_only_one_open_position(self):
        self.monitor.maybe_open([self.coin, {**self.coin, 'address': C}])
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertEqual(self.mocks['prepare'].call_count, 1)
        self.monitor.maybe_open([{**self.coin, 'address': C}])
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertEqual(self.rejections(), {'position_open': 1})
        self.assertEqual(m.STATE.entry_diagnostics['status'], 'position_open')
        self.assertEqual(self.mocks['prepare'].call_count, 1)

    def test_22_daily_loss_limit_is_a_100_dollar_gate_not_a_resize(self):
        m.STATE.demo_balance_usd, m.STATE.risk_day_start_balance_usd = 900.0, 1000.0
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(self.rejections(), {'daily_limit': 1})
        self.assertEqual(m.STATE.entry_diagnostics['status'], 'daily_limit')
        self.mocks['prepare'].assert_not_called()
        m.STATE.demo_balance_usd = 900.01
        self.monitor.maybe_open([self.coin])
        self.assertEqual(m.STATE.positions[0]['notional_usd'], 200)

    def test_quote_attempts_are_bounded_to_two_per_scan(self):
        self.mocks['prepare'].side_effect = None
        self.mocks['prepare'].return_value = None
        self.monitor.maybe_open([{**self.coin, 'address': x * 44} for x in 'ACDEF'])
        self.assertEqual(self.mocks['prepare'].call_count, 2)
        self.assertEqual(self.rejections(), {'quote_inconsistent': 2, 'quote_budget': 3})

    def test_26_stale_quote_cannot_enter(self):
        self.quote_age_ms = 10_001
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(self.rejections(), {'quote_age': 1})

    def test_quote_cost_caps(self):
        cases = {'impact': ({'price_impact_pct': 2.01}, {}),
                 'roundtrip_cost': ({}, {'expected_usdc': 194.0, 'floor_usdc': 192.0}),
                 'worst_case_cost': ({}, {'expected_usdc': 199.0, 'floor_usdc': 190.5})}
        for reason, (entry_change, exit_change) in cases.items():
            with self.subTest(reason=reason):
                entry = {'token_raw_expected': 100_000_000, 'token_raw_amount': 100_000_000,
                         'input_usdc_raw': 200_000_000, 'price_impact_pct': .2, 'quoted_at': NOW,
                         'raw_quote': {}, **entry_change}
                sale = {'expected_usdc': 199.0, 'floor_usdc': 198.0, **exit_change}
                self.mocks['prepare'].side_effect = None
                self.mocks['prepare'].return_value = (entry, sale)
                self.monitor.maybe_open([self.coin])
                self.assertFalse(m.STATE.positions)
                self.assertIn(reason, self.rejections())

    def test_27_missing_safety_data_fails_closed(self):
        for safety in ({'status': 'pending', 'reasons': ['risk_check_pending']},
                       {'status': 'unavailable', 'reasons': ['risk_data_unavailable']},
                       {'status': 'pass', 'provisional_early': True, 'metrics': self.safety['metrics']},
                       {}):
            with self.subTest(safety=safety):
                self.safety = safety
                self.monitor.maybe_open([self.coin])
                self.assertFalse(m.STATE.positions)
                self.mocks['prepare'].assert_not_called()
        self.safety = {'status': 'pass', 'metrics': {'decimals': 6}}   # rent reserve unknown
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(self.rejections(), {'risk_data_unavailable': 1})

    def test_27_failed_price_check_and_degraded_tape_fail_closed(self):
        self.mocks['price'].return_value = {'status': 'blocked', 'reason': 'price_source_disagreement'}
        self.monitor.maybe_open([self.coin])
        self.assertEqual(self.rejections(), {'price_source_disagreement': 1})
        self.mocks['price'].return_value = {'status': 'pass'}
        self.flow['quality'] = 'UNKNOWN'
        self.monitor.maybe_open([self.coin])
        self.assertEqual(self.rejections(), {'flow_quality': 1})
        self.assertFalse(m.STATE.positions)
        self.mocks['prepare'].assert_not_called()

    def test_23_24_existing_history_balance_and_sequence_survive(self):
        history = [{'id': f'old-{i}', 'address': C, 'pnl_usd': -1.0, 'pnl_pct': -0.5, 'closed_at': 1000 + i,
                    'strategy_id': 'ORDER_FLOW_EARLY_SCOUT_PAPER_V10', 'session_id': 'KEEP-MY-SESSION'}
                   for i in range(350)]
        m.STATE_PATH.write_text(json.dumps({
            'schema_version': 3, 'running': True, 'positions': [], 'history': history,
            'demo_starting_balance_usd': 1000.0, 'demo_balance_usd': 650.0, 'equity_peak_usd': 1000.0,
            'demo_started_at': 123, 'demo_session_id': 'KEEP-MY-SESSION', 'trade_seq': 350,
            'risk_day_key': '1970-01-01', 'risk_day_start_balance_usd': 1000.0}), encoding='utf-8')
        m.STATE = m.State()
        self.addCleanup(patch.object(m.STATE, 'live_flow', side_effect=lambda *a, **k: self.flow).start().stop)
        self.assertEqual((m.STATE.demo_balance_usd, m.STATE.trade_seq, len(m.STATE.history)), (650.0, 350, 350))
        self.monitor.maybe_open([self.coin])
        self.assertEqual(m.STATE.positions[0]['trade_no'], 351)
        self.assertEqual(m.STATE.positions[0]['id'], f'KEEP-MY-SESSION:{A}:351')
        self.assertEqual(m.STATE.demo_balance_usd, 650.0)
        self.assertEqual(m.STATE.history, history)
        saved = json.loads(m.STATE_PATH.read_text(encoding='utf-8'))
        self.assertEqual((saved['demo_balance_usd'], saved['trade_seq'], saved['demo_session_id'],
                          saved['demo_starting_balance_usd'], len(saved['history'])),
                         (650.0, 351, 'KEEP-MY-SESSION', 1000.0, 350))
        events = [json.loads(line)['event'] for line in m.AUDIT_PATH.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(events, ['ENTRY'])

    def test_entries_are_evaluated_on_the_market_scan_only(self):
        self.assertFalse(m.ENTRY_ON_POSITION_GUARD)
        with patch.object(self.monitor, 'maybe_open') as maybe_open, \
                patch.object(self.monitor.stop_event, 'wait', side_effect=lambda _: self.monitor.stop_event.set()) as wait:
            self.monitor.run_position_guard()
        maybe_open.assert_not_called()
        wait.assert_called_once_with(2.0)


class EngineExits(EngineHarness):
    def closed(self):
        self.assertEqual(len(m.STATE.history), 1)
        self.assertFalse(m.STATE.positions)
        return m.STATE.history[0]

    def test_14_strong_and_runner_positions_ride_past_plus_18(self):
        self.position()
        self.mark(2.36, 235.6, conviction=90)        # +18% observed, about +17.7% net
        self.assertFalse(m.STATE.history)
        live = m.STATE.positions[0]
        self.assertEqual((live['hold_mode'], live['adaptive_target_pct'], live['peak_price']), ('RUNNER', None, 2.36))
        self.assertEqual(live['exit_signal_basis'], 'OBSERVED_POOL_PRICE')
        self.mark(3.00, 298.0, conviction=75)        # +50% and still STRONG
        self.assertFalse(m.STATE.history)
        self.assertEqual(m.STATE.positions[0]['hold_mode'], 'STRONG')

    def test_17_trailing_exit_books_the_honest_quote(self):
        self.position()
        self.mark(3.00, 298.0, conviction=75)
        self.mark(2.83, 281.0, conviction=75)        # floor 3.00 * .94 = 2.82
        self.assertFalse(m.STATE.history)
        self.mark(2.82, 279.0, conviction=75)
        trade = self.closed()
        self.assertEqual(trade['exit_reason'], 'ADAPTIVE_TRAILING')
        self.assertAlmostEqual(trade['pnl_usd'], 279.0 - 200.23)
        self.assertEqual((trade['strategy_id'], trade['entry_policy_version'], trade['exit_policy_version']),
                         ('ORDER_FLOW_ADAPTIVE', 'ORDER_FLOW_GOLD_V1', oct4.EXIT_POLICY_VERSION))
        self.assertAlmostEqual(m.STATE.demo_balance_usd, 1000 + 279.0 - 200.23)
        public = m.STATE.snapshot()['history'][0]
        self.assertEqual((public['strategy_id'], public['entry_policy_version'], public['exit_reason']),
                         ('ORDER_FLOW_ADAPTIVE', 'ORDER_FLOW_GOLD_V1', 'ADAPTIVE_TRAILING'))

    def test_13_gap_through_the_stop_is_booked_uncapped(self):
        self.position()
        self.mark(1.40, 140.0, conviction=95)        # a 30% gap: the stop intent cannot cap it
        trade = self.closed()
        self.assertEqual(trade['exit_reason'], 'STOP_LOSS')
        self.assertAlmostEqual(trade['pnl_usd'], -60.23)
        self.assertLess(trade['pnl_pct'], -30)
        self.assertFalse(trade['paper_stop_capped'])
        self.assertAlmostEqual(m.STATE.demo_balance_usd, 939.77)

    def test_15_16_18_19_context_exits_through_the_engine(self):
        sell_heavy = {'trades': 5, 'sells': 4, 'sell_usd': 900, 'buy_usd': 100}
        cases = [('CONVICTION_EXIT', dict(price=1.98, net=197.0, conviction=30), 0, {}),
                 ('ORDERFLOW_EXIT', dict(price=2.02, net=200.0, conviction=80), 0, sell_heavy),
                 ('ADAPTIVE_TP_8', dict(price=2.16, net=214.0, conviction=40), 0, {}),
                 ('ADAPTIVE_MAX_HOLD', dict(price=2.02, net=200.0, conviction=60), 15, {}),
                 ('ABSOLUTE_MAX_HOLD', dict(price=2.02, net=200.0, conviction=95), 120, {})]
        for reason, mark, minutes, fast in cases:
            with self.subTest(reason=reason):
                m.STATE.history = []
                self.position(opened_at=self.clock[0] - minutes * 60_000)
                self.coin = {**self.coin, 'priceUsd': mark['price'], 'updatedAt': self.clock[0]}
                quote = {'net_proceeds_usd': mark['net'], 'network_fee_usd': .03, 'dex_fee_usd': 0,
                         'fill_price': mark['net'] / 100, 'impact_pct': .2, 'slippage_pct': .1, 'latency_pct': 0,
                         'quoted_at': self.clock[0], 'from_cache': False}
                with patch.object(m.paper_quotes, 'position_mark', return_value=quote), \
                        patch.object(self.monitor, 'market_context',
                                     return_value=context(mark['conviction'], fast_flow=fast)):
                    self.monitor.update_positions({A: self.coin})
                self.assertEqual(self.closed()['exit_reason'], reason)

    def test_strong_conviction_holds_past_its_mode_time(self):
        self.position(opened_at=self.clock[0] - 90 * 60_000)
        self.mark(2.02, 200.0, conviction=75)
        self.assertFalse(m.STATE.history)

    def test_25_wrong_pool_cannot_update_or_exit_the_held_position(self):
        self.position()
        m.STATE.position_market = {f'{A}:{B}': dict(self.coin)}
        wrong = {**self.coin, 'pairAddress': C, 'priceUsd': 0.5, 'liquidityUsd': 5, 'updatedAt': self.clock[0]}
        quote = {'net_proceeds_usd': 201.0, 'network_fee_usd': .03, 'dex_fee_usd': 0, 'fill_price': 2.01,
                 'impact_pct': .2, 'slippage_pct': .1, 'latency_pct': 0, 'quoted_at': self.clock[0], 'from_cache': False}
        with patch.object(m.paper_quotes, 'position_mark', return_value=quote) as mark, \
                patch.object(self.monitor, 'market_context', return_value=context(80)) as ctx:
            self.monitor.update_positions({A: wrong})
        self.assertFalse(m.STATE.history)
        live = m.STATE.positions[0]
        self.assertEqual((live['current_price'], live['peak_price'], live['pairAddress']), (2.0, 2.0, B))
        self.assertEqual(live['signal_pnl_pct'], 0)
        self.assertEqual(mark.call_args.args[1]['pairAddress'], B)
        self.assertEqual(ctx.call_args.args[0]['pairAddress'], B)

    def test_stale_observed_price_falls_back_to_the_net_mark(self):
        self.position()
        m.STATE.position_market = {}
        stale = {**self.coin, 'priceUsd': 9.0, 'updatedAt': self.clock[0] - 45_000}
        quote = {'net_proceeds_usd': 198.0, 'network_fee_usd': .03, 'dex_fee_usd': 0, 'fill_price': 1.98,
                 'impact_pct': .2, 'slippage_pct': .1, 'latency_pct': 0, 'quoted_at': self.clock[0], 'from_cache': False}
        with patch.object(m.paper_quotes, 'position_mark', return_value=quote), \
                patch.object(self.monitor, 'market_context', return_value=context(40)):
            self.monitor.update_positions({A: stale})
        # A stale +350% chart cannot fake ADAPTIVE_TP_8; the net mark shows no profit.
        self.assertFalse(m.STATE.history)
        self.assertEqual(m.STATE.positions[0]['exit_signal_basis'], 'NET_MARK_FALLBACK')

    def test_positions_opened_under_v10_keep_their_own_fixed_rules(self):
        self.position(exit_policy='fixed', exit_policy_version='HONEST_NET_EXIT_V1',
                      strategy_id='ORDER_FLOW_EARLY_SCOUT_PAPER_V10', take_profit_net_pct=10.0)
        with patch.object(self.monitor, 'market_context') as ctx:
            self.mark(2.40, 221.0, conviction=20)
        ctx.assert_not_called()
        trade = self.closed()
        self.assertEqual(trade['exit_reason'], 'TAKE_PROFIT_10_NET')
        self.assertEqual(trade['exit_policy_version'], 'HONEST_NET_EXIT_V1')
        self.assertEqual(trade['strategy_id'], 'ORDER_FLOW_EARLY_SCOUT_PAPER_V10')

    def test_no_sell_route_keeps_the_exposure_and_invents_no_fill(self):
        self.position()
        self.coin = {**self.coin, 'updatedAt': self.clock[0]}
        with patch.object(m.paper_quotes, 'position_mark', return_value=None), \
                patch.object(self.monitor, 'market_context', return_value=context(20)):
            self.monitor.update_positions({A: self.coin})
        self.assertFalse(m.STATE.history)
        self.assertEqual(m.STATE.positions[0]['valuation_status'], 'unavailable')
        self.assertEqual(m.STATE.demo_balance_usd, 1000)


    def test_user_fixed_position_ignores_time_liquidity_and_impact_shortcuts(self):
        self.position(exit_policy='fixed_targets', exit_policy_version=exit_policy.USER_FIXED_VERSION,
                      stop_loss_pct=4.0, take_profit_net_pct=10.0,
                      opened_at=self.clock[0] - 10_000 * 60_000)
        self.coin = {**self.coin, 'liquidityUsd': 1.0, 'updatedAt': self.clock[0]}
        quote = {'net_proceeds_usd': 199.0, 'gross_proceeds_usd': 199.03, 'network_fee_usd': .03,
                 'dex_fee_usd': 0, 'fill_price': 1.99, 'impact_pct': 99.0, 'slippage_pct': .1,
                 'latency_pct': 0, 'quoted_at': self.clock[0], 'from_cache': False,
                 'execution_source': 'OFFLINE_FIXTURE'}
        with patch.object(m.paper_quotes, 'position_mark', return_value=quote):
            self.monitor.update_positions({A: self.coin})
        self.assertFalse(m.STATE.history)
        self.assertEqual(m.STATE.positions[0]['exit_state'], 'OPEN')

    def test_user_fixed_cached_stop_must_still_hold_on_forced_fresh_quote(self):
        self.position(exit_policy='fixed_targets', exit_policy_version=exit_policy.USER_FIXED_VERSION,
                      stop_loss_pct=4.0, take_profit_net_pct=10.0)
        self.coin = {**self.coin, 'updatedAt': self.clock[0]}
        cached = {'net_proceeds_usd': 191.0, 'gross_proceeds_usd': 191.03, 'network_fee_usd': .03,
                  'dex_fee_usd': 0, 'fill_price': 1.91, 'impact_pct': .2, 'slippage_pct': .1,
                  'latency_pct': 0, 'quoted_at': self.clock[0], 'from_cache': True,
                  'execution_source': 'OFFLINE_FIXTURE'}
        recovered = {**cached, 'net_proceeds_usd': 199.0, 'gross_proceeds_usd': 199.03,
                     'fill_price': 1.99, 'from_cache': False}
        with patch.object(m.paper_quotes, 'position_mark', side_effect=[cached, recovered]) as mark:
            self.monitor.update_positions({A: self.coin})
        self.assertEqual(mark.call_count, 2)
        self.assertFalse(m.STATE.history)
        self.assertEqual(m.STATE.positions[0]['exit_state'], 'OPEN')


class EffectiveConfig(EngineHarness):
    def test_state_reports_the_effective_strategy(self):
        config = m.STATE.snapshot()['config']
        expected = {
            'signal_strategy': 'ORDER_FLOW_ADAPTIVE', 'strategy_profile': 'ORDER_FLOW_ADAPTIVE_OCT4',
            'strategy_version': 'gold-2026-10-04', 'learning_mode': 'ADAPTIVE_CONTEXT_HOLD',
            'entry_policy_version': 'ORDER_FLOW_GOLD_V1', 'exit_policy': 'oct4_adaptive',
            'exit_policy_version': 'ADAPTIVE_CONTEXT_HOLD_NET_V1',
            'scan_seconds': 15, 'position_scan_seconds': 2.0, 'max_positions': 1,
            'trade_notional_usd': 200.0, 'max_daily_loss_usd': 100.0, 'daily_loss_cap_enabled': True,
            'stop_loss_pct': 5.0, 'take_profit_pct': 18.0, 'trailing_pct': 4.0, 'max_hold_minutes': 7,
            'same_token_cooldown_seconds': 1200, 'entry_flow_window_seconds': 60,
            'entry_score': 85.0, 'min_liquidity_usd': 15_000.0,
            'strict_entry_score': 85.0, 'strict_min_conviction': 75.0, 'strict_min_liquidity_usd': 15_000.0,
            'strict_max_entry_impact_pct': 2.0, 'strict_max_roundtrip_cost_pct': 2.75,
            'strict_max_worst_case_cost_pct': 4.5, 'max_quoted_candidates_per_scan': 2,
            'daily_budget_sizing': False, 'entry_on_position_guard': False,
            'config_source': 'order_flow_adaptive_oct4.CONFIG', 'paper_only': True,
            'runtime_version': 'RUNTIME_DURABLE_PAPER_V2', 'execution_verification_version': 'QUOTE_EVIDENCE_V9',
            'rug_guard': 'RUG_GUARD_V2', 'effective_entry_thresholds': dict(oct4.ENTRY_LIMITS),
        }
        self.assertEqual({key: config.get(key) for key in expected}, expected)
        self.assertEqual(config['adaptive_strategy'], oct4.describe())
        self.assertEqual(len(config['effective_config_hash']), 64)
        self.assertEqual([mode['mode'] for mode in config['adaptive_strategy']['hold_modes']],
                         ['RUNNER', 'STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK'])
        json.dumps(config, allow_nan=False)

    def test_owner_fixed_profile_keeps_gold_entries_and_exposes_user_controls(self):
        self.addCleanup(m.apply_strategy_profile, oct4.PROFILE)
        self.assertEqual(m.apply_strategy_profile(m.OCT4_FIXED_PROFILE), m.OCT4_FIXED_PROFILE)
        m.STATE.engine_settings = m.normalize_engine_settings({'trade_notional_usd': 175, 'stop_loss_pct': 4.5, 'take_profit_pct': 12})
        config = m.STATE.snapshot()['config']
        self.assertEqual(config['entry_policy_version'], 'ORDER_FLOW_GOLD_V1')
        self.assertEqual(config['signal_strategy'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(config['trade_notional_usd'], 175.0)
        self.assertEqual(config['stop_loss_pct'], 4.5)
        self.assertEqual(config['take_profit_pct'], 12.0)
        self.assertEqual(config['exit_policy'], 'fixed_targets')
        self.assertEqual(config['exit_policy_version'], 'USER_FIXED_TARGETS_V1')
        self.assertTrue(config['user_controls']['enabled'])
        self.assertFalse(config['user_controls']['max_hold_enabled'])
        self.assertIsNone(config['max_hold_minutes'])
        self.assertEqual(config['public_history_min_notional_usd'], 200.0)

    def test_user_fixed_targets_have_no_time_or_emergency_exit_shortcut(self):
        self.assertIsNone(exit_policy.exit_reason({}, {}, net_pct=0.0, peak_net_pct=50.0, hold_minutes=10000,
                                                  stop_pct=4.0, take_profit_pct=10.0, policy='fixed_targets'))
        self.assertEqual(exit_policy.exit_reason({}, {}, net_pct=-4.01, peak_net_pct=0.0, hold_minutes=0,
                                                 stop_pct=4.0, take_profit_pct=10.0, policy='fixed_targets'),
                         'STOP_LOSS_NET_TARGET')
        self.assertEqual(exit_policy.exit_reason({}, {}, net_pct=10.01, peak_net_pct=10.01, hold_minutes=0,
                                                 stop_pct=4.0, take_profit_pct=10.0, policy='fixed_targets'),
                         'TAKE_PROFIT_NET_TARGET')

    def test_scan_counters_persist_across_engine_restart(self):
        m.STATE.scan_count = 321
        m.STATE.scanned_address_slots_total = 76_543
        m.STATE.save()
        restored = m.State()
        self.assertEqual(restored.scan_count, 321)
        self.assertEqual(restored.scanned_address_slots_total, 76_543)

    def test_live_tape_cache_keeps_exact_pair_rows(self):
        tape = {
            'status': 'online',
            'events': [
                {'address': A, 'pairAddress': B, 'ts': NOW, 'direction': 'BUY', 'usd_amount': 10},
                {'address': A, 'pairAddress': C, 'ts': NOW, 'direction': 'SELL', 'usd_amount': 20},
            ],
            'pair_coverage': {},
        }
        m.LIVE_TAPE_PATH.write_text(json.dumps(tape), encoding='utf-8')
        self.assertEqual(len(m.live_tape_events(A)), 2)
        self.assertEqual([row['pairAddress'] for row in m.live_tape_events(A, B)], [B])
        self.assertIs(m.read_live_tape(), m.read_live_tape())

    def test_engine_settings_validate_ranges_and_persist_shape(self):
        row = m.normalize_engine_settings({'trade_notional_usd': 123.45, 'stop_loss_pct': 4, 'take_profit_pct': 11, 'updated_at': 123456})
        self.assertEqual((row['trade_notional_usd'], row['stop_loss_pct'], row['take_profit_pct']), (123.45, 4.0, 11.0))
        self.assertEqual(row['version'], 'USER_FIXED_TARGETS_V1')
        self.assertEqual(row['updated_at'], 123456)
        with self.assertRaises(ValueError): m.normalize_engine_settings({'stop_loss_pct': 0})

    def test_public_history_hides_sub_200_trades_without_touching_internal_ledger(self):
        archive = self.root / 'all_time_history.json'
        archive.write_text(json.dumps({'history': [
            {'id': 'small', 'notional_usd': 199.99, 'closed_at': 1},
            {'id': 'full', 'notional_usd': 200.0, 'closed_at': 2},
        ]}), encoding='utf-8')
        m._ALL_TIME_HISTORY_CACHE.update(mtime_ns=None, rows=[])
        rows = m.read_all_time_history([
            {'id': 'current-small', 'notional_usd': 50.0, 'closed_at': 3},
            {'id': 'future-user-small', 'notional_usd': 50.0, 'closed_at': 5, 'engine_settings_version': m.USER_ENGINE_SETTINGS_VERSION},
            {'id': 'current-full', 'notional_usd': 200.0, 'closed_at': 4},
        ])
        self.assertEqual([row['id'] for row in rows], ['future-user-small', 'current-full', 'full'])

    def test_environment_cannot_drift_the_strategy(self):
        self.addCleanup(m.apply_strategy_profile, oct4.PROFILE)
        drift = {'NEO_MAX_POSITIONS': '20', 'NEO_TRADE_NOTIONAL_USD': '50', 'NEO_STOP_LOSS_PCT': '4',
                 'NEO_SCAN_SECONDS': '3', 'NEO_MAX_DAILY_LOSS_USD': '0', 'NEO_STRICT_ENTRY_SCORE': '40'}
        before = m.effective_config_hash()
        with patch.dict(os.environ, drift):
            m.apply_strategy_profile(oct4.PROFILE)
            self.assertEqual((m.MAX_POSITIONS, m.TRADE_NOTIONAL_USD, m.STOP_LOSS_PCT, m.SCAN_SECONDS,
                              m.MAX_DAILY_LOSS_USD, m.STRICT_ENTRY_SCORE), (1, 200.0, 5.0, 15, 100.0, 85.0))
            self.assertEqual(m.STATE.snapshot()['config']['ignored_env_overrides'], sorted(drift))
            self.assertEqual(m.effective_config_hash(), before)

    def test_profile_selection_is_explicit_and_fails_closed(self):
        self.addCleanup(m.apply_strategy_profile, oct4.PROFILE)
        self.assertEqual(m.DEFAULT_STRATEGY_PROFILE, 'ORDER_FLOW_ADAPTIVE_LEARNING')
        self.assertEqual(oct4.PROFILE, 'ORDER_FLOW_ADAPTIVE_OCT4')
        with patch.dict(os.environ, {'NEO_STRATEGY_PROFILE': 'something-else'}):
            with self.assertRaisesRegex(ValueError, 'refusing to start'):
                m.apply_strategy_profile()
        self.assertTrue(m.is_adaptive() and not m.is_learner())
        adaptive_hash = m.effective_config_hash()
        with patch.dict(os.environ, {'NEO_STRATEGY_PROFILE': 'early_scout_v10'}):
            self.assertEqual(m.apply_strategy_profile(), 'EARLY_SCOUT_V10')
        self.assertEqual((m.SIGNAL_STRATEGY, m.EXIT_POLICY), ('ORDER_FLOW_EARLY_SCOUT_PAPER_V10', 'fixed'))
        self.assertNotEqual(m.effective_config_hash(), adaptive_hash)

    def test_live_mode_is_refused(self):
        with patch.dict(os.environ, {'NEO_ENGINE_MODE': 'LIVE'}):
            with self.assertRaisesRegex(ValueError, 'Only PAPER'):
                m.main()


class AccountIsolation(unittest.TestCase):
    """Handoff test 28: account A cannot read or write account B's state."""

    def test_28_each_account_engine_gets_only_its_own_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {'NEO_USER_STATE_PATH': str(Path(tmp) / 'accounts.json'),
                   'NEO_USER_ENGINE_ROOT': str(Path(tmp) / 'users')}
            with patch.dict(os.environ, env):
                import user_gateway
                gateway = importlib.reload(user_gateway)
                launched = []

                class Process:
                    returncode = None
                    def poll(self): return None

                def popen(command, **kwargs):
                    launched.append(kwargs['env'])
                    return Process()

                ports = iter([18801, 18802])
                with patch.object(gateway.subprocess, 'Popen', side_effect=popen), \
                        patch.object(gateway, 'allocate_port', side_effect=lambda _: next(ports)), \
                        patch.object(gateway, 'bootstrap_if_needed'), patch.object(gateway, 'save_store'), \
                        patch.object(gateway, 'engine_health', return_value=True):
                    port_a = gateway.start_engine({'id': 'account-a'}, {})
                    port_b = gateway.start_engine({'id': 'account-b'}, {})
            self.assertNotEqual(port_a, port_b)
            a, b = launched
            for key in ('NEO_MARKET_STATE_PATH', 'NEO_MARKET_AUDIT_PATH', 'NEO_TRAINING_ROOT', 'NEO_MONITOR_PORT'):
                self.assertNotEqual(a[key], b[key], key)
            self.assertIn('account-a', a['NEO_MARKET_STATE_PATH'])
            self.assertNotIn('account-b', a['NEO_MARKET_STATE_PATH'])
            self.assertIn('account-b', b['NEO_MARKET_AUDIT_PATH'])
            self.assertEqual(Path(a['NEO_MARKET_STATE_PATH']).parent, Path(a['NEO_MARKET_AUDIT_PATH']).parent)
            self.assertNotEqual(Path(a['NEO_MARKET_STATE_PATH']).parent, Path(b['NEO_MARKET_STATE_PATH']).parent)
            for launched_env in launched:
                self.assertEqual((launched_env['NEO_ENGINE_MODE'], launched_env['NEO_EXECUTION_MODE']), ('PAPER', 'PAPER'))
                self.assertEqual(launched_env['NEO_MONITOR_HOST'], '127.0.0.1')


if __name__ == '__main__':
    unittest.main(verbosity=2)

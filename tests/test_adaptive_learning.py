"""Offline tests for ORDER_FLOW_ADAPTIVE_LEARNING: tiers, sizing and learning from closed trades.

Quotes, flow and safety results are fixtures; nothing calls an external API.
"""
import copy
import json
import unittest
from unittest.mock import patch

import adaptive_learning as learner
import market_monitor as m
import order_flow_adaptive_oct4 as oct4
from test_order_flow_adaptive_oct4 import A, B, C, NOW, EngineHarness, context, good_coin, good_flow

HOUR = 3_600_000
EXPLORE_COIN = {**good_coin(), 'score': 72, 'liquidityUsd': 12_000, 'marketCap': 60_000,
                'priceChange': {'m5': 30, 'h1': 8}}
EXPLORE_FLOW = {**good_flow(), 'trades': 2, 'buy_sell_usd_ratio': 1.1}


def setUpModule():
    m.apply_strategy_profile(learner.PROFILE)


def closed(pnl_pct, *, age_hours=0.0, features=None, evidence=True, **extra):
    trade = {'id': f'h{pnl_pct}-{age_hours}-{len(extra)}', 'address': C, 'pnl_pct': pnl_pct, 'pnl_usd': pnl_pct,
             'closed_at': NOW - age_hours * HOUR, 'learn_features': features or {'tier': 'EXPLORE'}, **extra}
    if evidence:
        trade['execution_verification_version'] = 'QUOTE_EVIDENCE_V9'
    return trade


class Tiers(unittest.TestCase):
    def tier(self, coin=None, flow=None, conviction=80):
        return learner.entry_tier(coin or good_coin(), flow or good_flow(), {'conviction': conviction}, now=NOW)

    def test_core_is_the_gold_rule(self):
        self.assertEqual(self.tier(), ('CORE', []))
        self.assertIs(learner.TIER_LIMITS['CORE'], oct4.ENTRY_LIMITS)

    def test_explore_accepts_what_core_rejects(self):
        self.assertEqual(oct4.signal_rejections(EXPLORE_COIN, EXPLORE_FLOW, {'conviction': 58}, now=NOW),
                         ['score', 'liquidity', 'momentum', 'flow_count', 'flow_ratio', 'conviction'])
        self.assertEqual(self.tier(EXPLORE_COIN, EXPLORE_FLOW, 58), ('EXPLORE', []))

    def test_explore_limits(self):
        cases = {'score': ({**EXPLORE_COIN, 'score': 69.9}, EXPLORE_FLOW, 58),
                 'liquidity': ({**EXPLORE_COIN, 'liquidityUsd': 9_999}, EXPLORE_FLOW, 58),
                 'momentum': ({**EXPLORE_COIN, 'priceChange': {'m5': 40.1}}, EXPLORE_FLOW, 58),
                 'flow_count': (EXPLORE_COIN, {**EXPLORE_FLOW, 'trades': 1}, 58),
                 'flow_ratio': (EXPLORE_COIN, {**EXPLORE_FLOW, 'buy_sell_usd_ratio': 1.09}, 58),
                 'wallet_count': (EXPLORE_COIN, {**EXPLORE_FLOW, 'unique_wallets': 0}, 58),
                 'large_sells': (EXPLORE_COIN, {**EXPLORE_FLOW, 'max_sell_usd': 1000}, 58),
                 'conviction': (EXPLORE_COIN, EXPLORE_FLOW, 57.9)}
        for reason, (coin, flow, conviction) in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(self.tier(coin, flow, conviction), (None, [reason]))

    def test_unverified_flow_and_stale_feed_fail_closed_in_every_tier(self):
        self.assertEqual(self.tier(flow={**good_flow(), 'quality': 'DEGRADED'}), (None, ['flow_quality']))
        self.assertEqual(self.tier({**good_coin(), 'updatedAt': NOW - 30_001}), (None, ['stale_feed']))
        self.assertEqual(self.tier({**good_coin(), 'pairAddress': 'x'}), (None, ['invalid_pair']))


class Sizing(unittest.TestCase):
    def test_size_follows_liquidity(self):
        size = learner.base_notional
        self.assertEqual(size({'liquidityUsd': 15_000}, 'CORE'), 56.25)       # about 0.75% impact per side
        self.assertEqual(size({'liquidityUsd': 40_000}, 'CORE'), 150.0)
        self.assertEqual(size({'liquidityUsd': 500_000}, 'CORE'), 200.0)      # capped
        self.assertEqual(size({'liquidityUsd': 15_000}, 'EXPLORE'), 40.0)     # floor
        self.assertEqual(size({'liquidityUsd': 40_000}, 'EXPLORE'), 75.0)     # half of CORE
        self.assertEqual(size({}, 'CORE'), 40.0)


class Learning(unittest.TestCase):
    F = {'tier': 'EXPLORE', 'liquidity': '<20k'}

    def table(self, trades, now=NOW):
        return learner.build_table(trades, now)

    def test_no_history_means_neutral(self):
        verdict = learner.assess(self.table([]), self.F)
        self.assertEqual((verdict['size_multiplier'], verdict['avoid'], verdict['evidence']), (1.0, None, []))

    def test_only_quote_evidenced_closed_trades_teach(self):
        trades = [closed(-9, evidence=False), {'pnl_pct': -9}, closed(float('nan')), None,
                  {**closed(-9), 'closed_at': 0}, closed(4.0)]
        self.assertEqual(self.table(trades)['trades'], 1)

    def test_results_decay_with_a_24_hour_half_life(self):
        row = self.table([closed(10, age_hours=0), closed(10, age_hours=24), closed(10, age_hours=48)])['buckets']['tier=EXPLORE']
        self.assertAlmostEqual(row['weight'], 1.75)
        self.assertEqual(row['trades'], 3)

    def test_a_few_trades_do_not_move_the_size(self):
        verdict = learner.assess(self.table([closed(-9)] * 4), self.F)   # weight 4 < 5
        self.assertEqual((verdict['size_multiplier'], verdict['evidence']), (1.0, []))

    def test_mean_is_shrunk_toward_zero(self):
        row = self.table([closed(8.0, features=self.F)] * 8)['buckets']['tier=EXPLORE']
        self.assertAlmostEqual(row['mean_pct'], 4.0)          # 64 / (8 trades + 8 prior)
        self.assertEqual(row['win_rate'], 1.0)

    def test_winning_context_is_sized_up_to_the_cap(self):
        mild = learner.assess(self.table([closed(4.0, features=self.F)] * 8), self.F)
        self.assertEqual((mild['edge_pct'], mild['size_multiplier']), (2.0, 1.5))
        small = learner.assess(self.table([closed(1.0, features=self.F)] * 8), self.F)
        self.assertEqual(small['size_multiplier'], 1.125)
        huge = learner.assess(self.table([closed(40.0, features=self.F)] * 30), self.F)
        self.assertEqual(huge['size_multiplier'], 1.5)

    def test_losing_context_is_sized_down_to_the_floor(self):
        trades = [closed(-2.0, features=self.F), closed(1.0, features=self.F)] * 6       # 50% wins: not avoided
        verdict = learner.assess(self.table(trades), self.F)
        self.assertIsNone(verdict['avoid'])
        self.assertLess(verdict['size_multiplier'], 1.0)
        worse = learner.assess(self.table([closed(-20.0, features=self.F), closed(1.0, features=self.F)] * 10), self.F)
        self.assertIsNone(worse['avoid'])                      # half the trades still win
        self.assertEqual(worse['size_multiplier'], 0.25)

    OTHER = [closed(1.0, features={'tier': 'CORE', 'liquidity': '50-150k'})] * 10

    def test_repeated_mistake_is_avoided_then_retried_when_the_evidence_ages(self):
        trades = [closed(-9.0, features=self.F)] * 9 + [closed(3.0, features=self.F)] + self.OTHER
        verdict = learner.assess(self.table(trades), self.F)
        self.assertEqual(verdict['avoid'], 'tier=EXPLORE')
        self.assertIn('tier=EXPLORE', learner.summary(self.table(trades))['avoided'])
        # Other contexts are unaffected.
        self.assertIsNone(learner.assess(self.table(trades), {'tier': 'CORE', 'liquidity': '50-150k'})['avoid'])
        # Two days later the same evidence weighs a quarter: no longer enough to avoid.
        later = learner.assess(self.table(trades, now=NOW + 48 * HOUR), self.F)
        self.assertIsNone(later['avoid'])

    def test_avoid_needs_all_three_conditions(self):
        few = [closed(-9.0, features=self.F)] * 7 + self.OTHER                       # weight below 8
        self.assertIsNone(learner.assess(self.table(few), self.F)['avoid'])
        shallow = [closed(-3.0, features=self.F)] * 9 + self.OTHER                    # mean above -2.5 after shrinkage
        self.assertIsNone(learner.assess(self.table(shallow), self.F)['avoid'])
        mixed = [closed(-20.0, features=self.F)] * 6 + [closed(1.0, features=self.F)] * 4 + self.OTHER   # 40% wins
        self.assertIsNone(learner.assess(self.table(mixed), self.F)['avoid'])

    def test_a_bad_day_shrinks_sizes_but_never_halts_trading(self):
        # Every recent trade lost in the same context: it holds all the evidence,
        # so it cannot be singled out. Size falls to the floor instead.
        everything = [closed(-9.0, features=self.F)] * 30
        verdict = learner.assess(self.table(everything), self.F)
        self.assertIsNone(verdict['avoid'])
        self.assertEqual(verdict['size_multiplier'], 0.25)
        self.assertEqual(learner.summary(self.table(everything))['avoided'], [])

    def test_five_straight_losses_halve_every_size(self):
        other = {'tier': 'CORE'}
        trades = [closed(-1.0, features=other, age_hours=i / 10) for i in range(5)]
        verdict = learner.assess(self.table(trades), {'tier': 'EXPLORE'})
        self.assertEqual((verdict['loss_streak_brake'], verdict['size_multiplier']), (True, 0.5))
        trades.insert(0, closed(2.0, features=other))          # newest trade is a win
        self.assertFalse(learner.assess(self.table(trades), {'tier': 'EXPLORE'})['loss_streak_brake'])

    def test_older_trades_are_bucketed_from_their_recorded_fields(self):
        legacy = {'entry_mode': 'MICRO_SCOUT', 'entry_hold_mode': 'NORMAL', 'entry_liquidity_usd': 30_000,
                  'entry_market_cap': 400_000, 'entry_change_m5': 7, 'score': 85,
                  'entry_flow': {'buy_sell_usd_ratio': 2.0},
                  'coin_snapshot': {'ageMinutes': 45, 'dexId': 'pumpswap'}}
        self.assertEqual(learner.features_from_trade(legacy), {
            'tier': 'LEGACY', 'mode': 'NORMAL', 'liquidity': '20-50k', 'market_cap': '100k-1M', 'age': '30m-3h',
            'm5': '5-12', 'flow_ratio': '1.5-2.5', 'score': '80-90', 'dex': 'pumpswap'})
        candidate = learner.features({**good_coin(), 'dexId': 'raydium'}, good_flow(), context(80), 'CORE')
        self.assertEqual(candidate, {
            'tier': 'CORE', 'mode': 'STRONG', 'liquidity': '150k+', 'market_cap': '1M+', 'age': '30m-3h',
            'm5': '5-12', 'flow_ratio': '1.5-2.5', 'score': '90+', 'dex': 'other'})

    def test_summary_is_serialisable(self):
        trades = [closed(4.0, features=self.F)] * 12 + [closed(-9.0, features={'tier': 'CORE'})] * 9
        view = learner.summary(self.table(trades))
        json.dumps(view, allow_nan=False)
        self.assertEqual(view['best'][0]['bucket'], 'tier=EXPLORE')
        self.assertEqual(view['avoided'], ['tier=CORE'])
        json.dumps(learner.describe(), allow_nan=False)


class EngineLearning(EngineHarness):
    def setUp(self):
        super().setUp()
        self.entry_patches()

    def open_one(self, coin=None):
        self.monitor.maybe_open([coin or self.coin])
        return m.STATE.positions[-1] if m.STATE.positions else None

    def test_default_profile_and_reported_config(self):
        self.assertEqual(m.DEFAULT_STRATEGY_PROFILE, 'ORDER_FLOW_ADAPTIVE_LEARNING')
        snapshot = m.STATE.snapshot()
        config = snapshot['config']
        expected = {'signal_strategy': 'ORDER_FLOW_ADAPTIVE_LEARNING', 'strategy_profile': 'ORDER_FLOW_ADAPTIVE_LEARNING',
                    'entry_policy_version': 'ORDER_FLOW_TIERED_LEARNER_V1', 'learning_mode': 'ONLINE_CONTEXT_EXPECTANCY_V1',
                    'exit_policy': 'fixed', 'scan_seconds': 5, 'position_scan_seconds': 1.0, 'max_positions': 8,
                    'trade_notional_usd': 200.0, 'max_daily_loss_usd': 0.0, 'daily_loss_cap_enabled': False,
                    'stop_loss_pct': 4.0, 'take_profit_pct': 10.0, 'reentry_seconds': 120, 'loss_reentry_seconds': 600,
                    'strict_max_roundtrip_cost_pct': 4.0, 'strict_max_worst_case_cost_pct': 6.0,
                    'max_quoted_candidates_per_scan': 6, 'unsellable_blocks_entries': False,
                    'config_source': 'adaptive_learning.CONFIG', 'paper_only': True}
        self.assertEqual({k: config.get(k) for k in expected}, expected)
        self.assertEqual(set(config['effective_entry_thresholds']), {'CORE', 'EXPLORE'})
        self.assertEqual(config['adaptive_strategy'], learner.describe())
        self.assertEqual(snapshot['learning']['mode'], 'ONLINE_CONTEXT_EXPECTANCY_V1')
        json.dumps(snapshot['learning'], allow_nan=False)

    def test_core_entry_is_liquidity_scaled_and_records_what_it_will_learn_from(self):
        pos = self.open_one()
        self.assertEqual((pos['entry_mode'], pos['notional_usd']), ('CORE', 200.0))
        self.assertEqual((pos['strategy_id'], pos['entry_policy_version'], pos['exit_policy']),
                         ('ORDER_FLOW_ADAPTIVE_LEARNING', 'ORDER_FLOW_TIERED_LEARNER_V1', 'fixed'))
        self.assertEqual((pos['stop_loss_pct'], pos['planned_stop_net_pct'], pos['take_profit_net_pct']), (4.0, -4.0, 10.0))
        self.assertEqual(pos['learn_features']['tier'], 'CORE')
        self.assertEqual(set(pos['learn_features']), {'tier', 'mode', 'liquidity', 'market_cap', 'age', 'm5',
                                                      'flow_ratio', 'score', 'dex'})
        self.assertEqual(pos['learning_size_multiplier'], 1.0)

    def test_explore_entry_trades_small(self):
        self.coin, self.flow, self.context = dict(EXPLORE_COIN), dict(EXPLORE_FLOW), context(60)
        pos = self.open_one()
        self.assertEqual((pos['entry_mode'], pos['notional_usd'], pos['entry_hold_mode']), ('EXPLORE', 40.0, 'NORMAL'))

    def test_learned_edge_changes_the_next_size(self):
        self.coin['liquidityUsd'] = 40_000                      # CORE base size $150
        features = learner.features(self.coin, self.flow, self.context, 'CORE')
        m.STATE.history = [closed(-2.0, features=features), closed(1.0, features=features)] * 6
        smaller = self.open_one()
        self.assertLess(smaller['notional_usd'], 150)
        self.assertLess(smaller['learning_size_multiplier'], 1.0)
        self.assertTrue(smaller['learning_evidence'])
        m.STATE.positions = []
        m.STATE.history = [closed(4.0, features=features)] * 8
        bigger = self.open_one()
        self.assertEqual((bigger['learning_size_multiplier'], bigger['notional_usd']), (1.5, 200.0))

    def test_context_that_keeps_losing_is_skipped_and_named(self):
        features = learner.features(self.coin, self.flow, self.context, 'CORE')
        elsewhere = {key: 'elsewhere' for key in features}
        m.STATE.history = [closed(-9.0, features=features)] * 10 + [closed(1.0, features=elsewhere)] * 10
        self.assertIsNone(self.open_one())
        self.assertEqual(self.rejections(), {'learned_avoid': 1})
        example = m.STATE.entry_diagnostics['examples'][0]
        self.assertTrue(example['metrics']['bucket'])
        self.assertIn('learned_avoid', m.STATE.entry_diagnostics['reason_labels'])
        self.mocks['prepare'].assert_not_called()
        self.assertTrue(m.STATE.snapshot()['learning']['avoided'])

    def test_eight_positions_at_once_and_no_daily_gate(self):
        m.STATE.demo_balance_usd, m.STATE.risk_day_start_balance_usd = 2000.0, 5000.0    # far below the day's start
        coins = [{**self.coin, 'address': x * 44} for x in 'ACDEFGHJK']
        for _ in range(3):
            self.monitor.maybe_open(coins)
        self.assertEqual(len(m.STATE.positions), 8)
        self.assertEqual(len({p['address'] for p in m.STATE.positions}), 8)
        self.monitor.maybe_open(coins)
        self.assertEqual(self.rejections(), {'position_open': 1})

    def test_cooldown_is_two_minutes_after_a_win_and_ten_after_a_loss(self):
        for pnl, seconds in ((5.0, 120), (-5.0, 600)):
            with self.subTest(pnl=pnl):
                m.STATE.positions = []
                m.STATE.history = [{'id': 'x', 'address': A, 'pnl_pct': pnl, 'closed_at': NOW - seconds * 1000 + 1}]
                self.assertIsNone(self.open_one())
                self.assertEqual(self.rejections(), {'cooldown': 1})
                m.STATE.history[0]['closed_at'] = NOW - seconds * 1000
                self.assertIsNotNone(self.open_one())

    def test_cost_caps_allow_small_cap_round_trips_but_not_more(self):
        def quotes(expected, floor):
            entry = {'token_raw_expected': 100_000_000, 'token_raw_amount': 100_000_000, 'input_usdc_raw': 200_000_000,
                     'price_impact_pct': .5, 'quoted_at': NOW, 'raw_quote': {}}
            return entry, {'expected_usdc': expected, 'floor_usdc': floor}
        self.mocks['prepare'].side_effect = None
        self.mocks['prepare'].return_value = quotes(193.0, 190.0)       # about -3.7% expected, -5.2% worst case
        self.assertIsNotNone(self.open_one())
        m.STATE.positions = []
        self.mocks['prepare'].return_value = quotes(191.0, 190.0)       # about -4.7% expected
        self.assertIsNone(self.open_one())
        self.assertIn('roundtrip_cost', self.rejections())
        self.mocks['prepare'].return_value = quotes(194.0, 187.0)       # worst case about -6.7%
        self.assertIsNone(self.open_one())
        self.assertIn('worst_case_cost', self.rejections())

    def test_safety_still_fails_closed(self):
        self.safety = {'status': 'pending', 'reasons': ['risk_check_pending']}
        self.assertIsNone(self.open_one())
        self.mocks['prepare'].assert_not_called()
        self.safety = {'status': 'pass', 'metrics': {'decimals': 6, 'token_account_rent_lamports': 1_650_000}}
        self.flow['quality'] = 'UNKNOWN'
        self.assertIsNone(self.open_one())
        self.assertEqual(self.rejections(), {'flow_quality': 1})

    def test_unsellable_position_neither_freezes_the_engine_nor_takes_a_slot(self):
        stuck = [{'id': f'stuck-{i}', 'address': x * 44, 'valuation_status': 'unavailable', 'exit_state': 'UNSELLABLE',
                  'capital_committed_usd': 20.0, 'notional_usd': 20.0} for i, x in enumerate('CDEFGHJK')]
        m.STATE.positions = copy.deepcopy(stuck)
        self.assertEqual(m.slots_in_use(m.STATE.positions), 0)
        pos = self.open_one()
        self.assertEqual(pos['address'], A)
        self.assertEqual(m.STATE.positions[:8], stuck)                  # untouched, capital still reserved
        self.assertEqual(m.STATE.reserved_usd(), 160.0 + pos['capital_committed_usd'])

    def test_briefly_missing_sell_route_still_blocks_new_exposure(self):
        m.STATE.positions = [{'id': 'p', 'address': C, 'valuation_status': 'unavailable', 'exit_state': 'PENDING_EXIT',
                              'capital_committed_usd': 20.0}]
        self.monitor.maybe_open([self.coin])
        self.assertEqual(self.rejections(), {'liquidation_unavailable': 1})
        self.assertEqual(len(m.STATE.positions), 1)


class EngineExitsUnderLearner(EngineHarness):
    def test_learner_position_uses_fixed_minus_4_stop(self):
        self.position(stop_loss_pct=4.0, exit_policy='fixed', exit_policy_version='HONEST_NET_EXIT_V1', take_profit_net_pct=10.0)
        self.mark(1.94, 195.0, conviction=80)        # about -2.6% net
        self.assertFalse(m.STATE.history)
        self.assertEqual(m.STATE.positions[0].get('planned_stop_net_pct'), -4.0)
        self.mark(1.91, 191.0, conviction=80)        # about -4.6% net
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'STOP_LOSS_NET_TARGET')

    def test_positions_opened_under_older_profiles_keep_their_5_percent_stop(self):
        self.position(stop_loss_pct=5.0)             # ORDER_FLOW_ADAPTIVE position
        self.mark(1.88, 189.0, conviction=80)        # about -5.6% net
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'STOP_LOSS')
        m.STATE.history = []
        self.position(exit_policy='fixed', exit_policy_version='HONEST_NET_EXIT_V1', take_profit_net_pct=10.0)
        m.STATE.positions[0].pop('stop_loss_pct', None)                 # V10 positions never recorded one
        self.mark(1.88, 189.0, conviction=80)
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'STOP_LOSS_NET_TARGET')

    def test_learner_position_takes_fixed_plus_10_net(self):
        self.position(stop_loss_pct=4.0, exit_policy='fixed', exit_policy_version='HONEST_NET_EXIT_V1', take_profit_net_pct=10.0)
        self.mark(2.18, 218.0, conviction=90)        # about +8.9% net
        self.assertFalse(m.STATE.history)
        self.mark(2.21, 221.0, conviction=90)        # about +10.4% net
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'TAKE_PROFIT_10_NET')
        self.assertGreater(m.STATE.history[0]['pnl_usd'], 20)


if __name__ == '__main__':
    unittest.main(verbosity=2)

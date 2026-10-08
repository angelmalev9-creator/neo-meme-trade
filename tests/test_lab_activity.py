import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TMP=tempfile.TemporaryDirectory()
os.environ['NEO_STRATEGY_LAB_PATH']=str(Path(TMP.name)/'state.json')
os.environ['NEO_STRATEGY_LAB_RESET_FLAG']=str(Path(TMP.name)/'reset')
import lab_activity as a
import strategy_lab as lab

ADDRESS='So11111111111111111111111111111111111111112'
PAIR='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
NOW=1_800_000_000_000

def coin():
    return {'address':ADDRESS,'pairAddress':PAIR,'symbol':'TEST','name':'Test',
            'priceUsd':.01,'priceNative':.00008,'marketCap':1e6,'liquidityUsd':1e6,
            'dexId':'raydium','updatedAt':NOW,'score':100,'ageMinutes':50,
            'priceChange':{'m5':12,'h1':50},'volume':{'h1':1e6},
            'txns':{'m5':{'buys':60,'sells':20}}}

def flows():
    return {ADDRESS:{'trades':20,'buys':15,'sells':5,'buy_usd':1000,'sell_usd':100,
                     'unique_wallets':10,'ratio':10,'max_sell':30}}

class ActivityTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(lab.price_integrity,'check',return_value={'status':'pass','version':'OFFLINE_FIXTURE'})
        guard.start();self.addCleanup(guard.stop)
        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}

    def test_all_37_rules_exist(self):
        self.assertEqual(len(a.RULES),37)
        self.assertEqual(set(a.RULES),{s['id'] for s in lab.STRATEGIES})
        for rule in a.RULES.values():self.assertGreaterEqual(rule.liquidity,10000)

    def test_momentum_hunter_broad_but_quality_ranked(self):
        broad={'score':80,'liq':12000,'m5':4,'h1':20,'bs':1.0,'lmc':.05,
               'age':600,'vol_liq':.12,
               'flow':{'trades':2,'ratio':1.0,'buy_usd':80,'sell_usd':60,
                       'unique_wallets':1,'max_sell':60}}
        self.assertTrue(a.RULES['MOMENTUM_HUNTER'].matches(broad))
        self.assertFalse(a.RULES['MOMENTUM'].matches(broad))

        strong={**broad,'score':86,'m5':7,'bs':1.4,'vol_liq':.30,
                'flow':{'trades':8,'ratio':2.4,'buy_usd':600,'sell_usd':150,
                        'unique_wallets':5,'max_sell':60}}
        weak={**broad,'score':78,'m5':.5,'h1':-35,'bs':.9,'lmc':.03,'vol_liq':.03,
              'flow':{'trades':0,'ratio':0,'buy_usd':0,'sell_usd':250,
                      'unique_wallets':0,'max_sell':500}}
        self.assertGreater(a.momentum_hunter_rank(strong,-.5),a.MOMENTUM_HUNTER_MIN_RANK)
        self.assertLess(a.momentum_hunter_rank(weak,-.5),a.MOMENTUM_HUNTER_MIN_RANK)

    def test_every_strategy_can_match_its_family(self):
        for key,rule in a.RULES.items():
            f={'score':100,'liq':max(rule.liquidity,100000),'m5':sum(rule.move)/2,
               'bs':max(2,rule.buy_sell),'lmc':max(.3,rule.liquidity_cap),
               'age':max(10,rule.age[0]),'h1':max(20,rule.hour[0]),
               'vol_liq':max(1,rule.volume_liquidity[0]),'mc':1e6,'flow':flows()[ADDRESS]}
            self.assertTrue(rule.matches(f),key)

    def test_no_low_liquidity_or_missing_prices(self):
        c=coin();self.assertTrue(a.usable_feed_coin(c,NOW))
        for k,v in [('liquidityUsd',9999),('priceUsd',0),('priceUsd',float('nan')),('address','https://x.com/foo')]:
            self.assertFalse(a.usable_feed_coin({**c,k:v},NOW),k)
        self.assertFalse(a.usable_feed_coin(c,NOW+20001))

    def test_cooldown_from_exit(self):
        b={'last_entry_by_address':{ADDRESS:1},'history':[{'address':ADDRESS,'closed_at':NOW-59000,'pnl_usd':10}]}
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW),1000)
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW+1000),0)
        b['history'][0]['pnl_usd']=-1
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW),121000)
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW+121000),0)

    def test_budget_includes_network(self):
        for balance in [9.99,10,20,100,500]:
            q=a.affordable_entry(coin(),balance,150,lab.entry_execution,lab.exit_execution)
            if q:
                self.assertLessEqual(q['entry']['capital_committed_usd'],balance+1e-9)
                self.assertLessEqual(q['notional'],150)
                self.assertGreaterEqual(q['initial_pnl_pct'],-a.MAX_ENTRY_COST_PCT)
                self.assertLess(q['initial_pnl_usd'],0)
        self.assertIsNone(a.affordable_entry(coin(),9.99,150,lab.entry_execution,lab.exit_execution))

    def test_downsizes_instead_of_waiving_costs(self):
        c=coin();c['liquidityUsd']=20000
        q=a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution)
        self.assertIsNotNone(q)
        self.assertLess(q['notional'],150)
        self.assertGreaterEqual(q['initial_pnl_pct'],-2.75)

    def test_unaffordable_model_fees_rejected(self):
        c=coin();c.update(dexId='pumpswap',marketCap=10000,liquidityUsd=10000)
        self.assertIsNone(a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution))

    def test_no_history_balance_or_open_position_reset(self):
        for b in lab.STATE['books'].values():
            b['history']=[{'trade_no':7,'address':ADDRESS,'pnl_usd':1,'closed_at':NOW}]
            b['position']={'trade_no':8,'address':ADDRESS,'keep':'original'}
        before=copy.deepcopy(lab.STATE)
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        self.assertEqual(lab.STATE,before)

    def test_entry_records_cost_not_zero_and_preserves_book_balance(self):
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        opened=[b for b in lab.STATE['books'].values() if b['position']]
        self.assertGreater(len(opened),10)
        for b in opened:
            self.assertEqual(b['balance'],b['starting_balance'])
            self.assertEqual(b['position']['entry_policy_version'],a.POLICY_VERSION)
            self.assertLess(b['position']['open_pnl_usd'],0)
            self.assertGreaterEqual(b['position']['entry_roundtrip_pnl_pct'],-2.75)

    def test_net_stop_all_default_books_no_loss_clamping(self):
        c=coin()
        lab.STATE['books'].pop('TIKTOK')   # -12% stop covered below
        lab.STATE['books'].pop('X_SIGNAL') # -12% stop covered by X Signal tests
        lab.STATE['books'].pop('HYPE_RADAR') # -12% stop covered by Hype Radar tests
        for b in lab.STATE['books'].values():
            b['position']={'trade_no':1,'address':ADDRESS,'pairAddress':PAIR,'entry_price':1,
                           'current_price':1,'quantity':100,'original_quantity':100,
                           'notional_usd':100,'remaining_cost_basis_usd':100,
                           'opened_at':NOW-1000,'partial_realized_pnl':0,'entry_network_fee_usd':0}
        quote={'fill_price':.94,'net_proceeds_usd':94,'dex_fee_usd':0,'network_fee_usd':0,
               'impact_pct':0,'slippage_pct':0,'latency_pct':0}
        with patch.object(lab,'dex_position_prices',return_value={(ADDRESS,PAIR):{**c,'priceUsd':1}}),patch.object(lab,'exit_execution',return_value=quote),patch.object(lab,'now_ms',return_value=NOW):
            lab.update_positions({})
        for b in lab.STATE['books'].values():
            self.assertIsNone(b['position'])
            self.assertEqual(b['history'][0]['exit_reason'],'STOP_LOSS_3_NET')
            self.assertEqual(b['history'][0]['pnl_pct'],-6)
            self.assertEqual(b['balance'],b['starting_balance']-6)

    def test_constants_preserved(self):
        self.assertEqual((lab.STOP_LOSS,lab.TAKE_PROFIT,lab.MAX_HOLD_MIN),(3,10,60))
        self.assertEqual(lab.TRADE_NOTIONAL,150)
        self.assertEqual(lab.STRATEGY_START_BALANCES['SCALPER'],100)



class TikTokStrategyTests(unittest.TestCase):
    LIGHT={'score':65,'liq':10000,'mc':30000,'m5':-5,'h1':-50,'bs':.8,'lmc':.02,'age':0,'vol_liq':0,
           'flow':{'trades':0,'ratio':0,'buy_usd':0,'sell_usd':0,'unique_wallets':0,'max_sell':0}}

    def setUp(self):
        guard=patch.object(lab.price_integrity,'check',return_value={'status':'pass','version':'OFFLINE_FIXTURE'})
        guard.start();self.addCleanup(guard.stop)
        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}

    def position(self,book_id,opened_at=NOW-1000):
        lab.STATE['books'][book_id]['position']={'trade_no':1,'address':ADDRESS,'pairAddress':PAIR,
            'entry_price':1,'current_price':1,'quantity':100,'original_quantity':100,'notional_usd':100,
            'remaining_cost_basis_usd':100,'opened_at':opened_at,'partial_realized_pnl':0,'entry_network_fee_usd':0}

    def mark(self,net):
        quote={'fill_price':net/100,'net_proceeds_usd':net,'dex_fee_usd':0,'network_fee_usd':0,
               'impact_pct':0,'slippage_pct':0,'latency_pct':0}
        with patch.object(lab,'dex_position_prices',return_value={(ADDRESS,PAIR):{**coin(),'priceUsd':1}}),\
             patch.object(lab,'exit_execution',return_value=quote),patch.object(lab,'now_ms',return_value=NOW):
            lab.update_positions({})

    def test_named_and_has_the_lightest_filter_in_the_lab(self):
        book=lab.empty_book(next(s for s in lab.STRATEGIES if s['id']=='TIKTOK'))
        self.assertEqual((book['name'],book['starting_balance']),('TikTok Strategy',500))
        self.assertTrue(a.RULES['TIKTOK'].matches(self.LIGHT))
        for key,rule in a.RULES.items():
            if key not in {'TIKTOK','X_SIGNAL','HYPE_RADAR'}:self.assertFalse(rule.matches(self.LIGHT),key)
        # X_SIGNAL has a similarly light market rule, but Strategy Lab separately requires a fresh X address.
        # Hype Radar goes lower still, but it needs a fresh theme match and a rug check on top.
        self.assertEqual(min(r.score for k,r in a.RULES.items() if k!='HYPE_RADAR'),a.RULES['TIKTOK'].score)

    def test_enters_only_from_30k_market_cap(self):
        rule=a.RULES['TIKTOK']
        self.assertFalse(rule.matches({**self.LIGHT,'mc':29999.99}))
        self.assertTrue(rule.matches({**self.LIGHT,'mc':30000}))
        missing={k:v for k,v in self.LIGHT.items() if k!='mc'}
        self.assertFalse(rule.matches(missing))
        self.assertEqual(lab.enrich({**coin(),'marketCap':45000},{})['mc'],45000)
        self.assertEqual(lab.enrich({**coin(),'marketCap':None,'fdv':31000},{})['mc'],31000)

    def test_remaining_light_limits(self):
        rule=a.RULES['TIKTOK']
        for change in ({'score':64.9},{'liq':9999},{'m5':-5.1},{'m5':60.1},{'bs':.79},{'lmc':.019},
                       {'age':1441},{'h1':-50.1}):
            self.assertFalse(rule.matches({**self.LIGHT,**change}),change)

    def test_stop_is_minus_12_net_and_target_plus_17_net(self):
        for net,reason in ((88.01,None),(88,'STOP_LOSS_12_NET'),(116.99,None),(117,'TAKE_PROFIT_17_NET')):
            with self.subTest(net=net):
                lab.STATE['books']['TIKTOK']=lab.empty_book({'id':'TIKTOK','name':'TikTok Strategy'})
                self.position('TIKTOK');self.mark(net)
                book=lab.STATE['books']['TIKTOK']
                if reason is None:
                    self.assertIsNotNone(book['position'])
                else:
                    self.assertEqual(book['history'][0]['exit_reason'],reason)
                    self.assertAlmostEqual(book['balance'],500+net-100)

    def test_gap_through_the_stop_is_booked_in_full(self):
        self.position('TIKTOK');self.mark(95);self.mark(60)
        trade=lab.STATE['books']['TIKTOK']['history'][0]
        self.assertEqual((trade['exit_reason'],trade['pnl_pct']),('STOP_LOSS_12_NET',-40))
        self.assertEqual((trade['exit_fill_model'],trade['observed_exit_pnl_pct']),('MARKET_AT_OBSERVED_MARK',-40))
        # The previous mark is kept so the jump is visible afterwards.
        self.assertEqual((trade['pre_exit_pnl_pct'],trade['pre_exit_gap_seconds']),(-5,0))
        self.assertAlmostEqual(lab.STATE['books']['TIKTOK']['balance'],460)

    def test_take_profit_is_a_limit_booked_at_exactly_17(self):
        self.position('TIKTOK');self.mark(131)
        book=lab.STATE['books']['TIKTOK'];trade=book['history'][0]
        self.assertEqual((trade['exit_reason'],trade['pnl_pct'],trade['pnl_usd']),('TAKE_PROFIT_17_NET',17,17))
        self.assertEqual((trade['exit_fill_model'],trade['observed_exit_pnl_pct']),('LIMIT_AT_TARGET',31))
        self.assertAlmostEqual(book['balance'],517)
        self.assertAlmostEqual(trade['execution_exit_price'],1.17)

    def test_limit_never_improves_a_result(self):
        # Other exits and other books stay at the observed mark.
        self.position('TIKTOK',opened_at=NOW-61*60000);self.mark(110)
        trade=lab.STATE['books']['TIKTOK']['history'][0]
        self.assertEqual((trade['exit_reason'],trade['pnl_pct'],trade['exit_fill_model']),
                         ('ABSOLUTE_MAX_HOLD_60',10,'MARKET_AT_OBSERVED_MARK'))
        self.position('SCALPER');self.mark(125)
        trade=lab.STATE['books']['SCALPER']['history'][0]
        self.assertEqual((trade['exit_reason'],trade['pnl_pct']),('TAKE_PROFIT_10_NET',25))

    def test_other_books_keep_3_10(self):
        self.position('SCALPER');self.position('TIKTOK');self.mark(96)
        self.assertEqual(lab.STATE['books']['SCALPER']['history'][0]['exit_reason'],'STOP_LOSS_3_NET')
        self.assertIsNotNone(lab.STATE['books']['TIKTOK']['position'])
        self.position('SCALPER');self.mark(111)
        self.assertEqual(lab.STATE['books']['SCALPER']['history'][0]['exit_reason'],'TAKE_PROFIT_10_NET')
        self.assertIsNotNone(lab.STATE['books']['TIKTOK']['position'])
        self.assertEqual(lab.book_exit_rules('SCALPER'),{'stop_loss':3,'take_profit':10,'max_hold_minutes':60})
        self.assertEqual(lab.book_exit_rules('TIKTOK'),{'stop_loss':12,'take_profit':17,'max_hold_minutes':60})

    def test_max_hold_still_applies(self):
        self.position('TIKTOK',opened_at=NOW-60*60_000);self.mark(101)
        self.assertEqual(lab.STATE['books']['TIKTOK']['history'][0]['exit_reason'],'ABSOLUTE_MAX_HOLD_60')

    def test_real_pool_fee_is_charged_and_low_cap_entry_fits_only_tiktok(self):
        # PumpSwap near a $30k market cap: 125 bps per side in the lab cost model.
        c={**coin(),'dexId':'pumpswap','marketCap':30000,'liquidityUsd':200000,'score':70,
           'priceChange':{'m5':5,'h1':5}}
        self.assertEqual(lab.pumpswap_fee_bps(c),125.0)
        self.assertIsNone(a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution))
        q=a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution,a.entry_cost_cap('TIKTOK'))
        self.assertIsNotNone(q)
        self.assertEqual(q['entry']['dex_fee_bps'],125.0)
        self.assertEqual(q['entry']['slippage_pct']+q['entry']['latency_pct'],.2)
        self.assertLess(q['initial_pnl_pct'],-2.5)            # both pool fees are in the mark
        self.assertGreaterEqual(q['initial_pnl_pct'],-4.0)
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([c],{})
        opened=[b['id'] for b in lab.STATE['books'].values() if b['position']]
        self.assertEqual(opened,['TIKTOK'])
        pos=lab.STATE['books']['TIKTOK']['position']
        self.assertEqual((pos['stop_loss_net_pct'],pos['take_profit_net_pct']),(12,17))
        self.assertEqual(a.entry_cost_cap('SCALPER'),a.MAX_ENTRY_COST_PCT)

    def test_cost_cap_still_rejects_expensive_entries(self):
        c={**coin(),'dexId':'pumpswap','marketCap':30000,'liquidityUsd':10000,'score':70}
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([c],{})
        book=lab.STATE['books']['TIKTOK']
        if book['position']:   # size was cut until the round trip fits the cap
            self.assertLess(book['position']['notional_usd'],150)
            self.assertGreaterEqual(book['position']['entry_roundtrip_pnl_pct'],-4.0)
        else:
            self.assertEqual(book['entry_diagnostics']['cost_rejected'],1)

    def test_exit_is_rechecked_faster_only_while_the_sniper_holds(self):
        self.assertEqual(lab.poll_interval(),lab.POLL_SECONDS)
        self.position('SCALPER')
        self.assertEqual(lab.poll_interval(),lab.POLL_SECONDS)
        self.position('TIKTOK')
        self.assertEqual(lab.poll_interval(),1.0)

    def test_published_config_and_stats_show_the_overrides(self):
        config=a.policy_config()
        self.assertEqual(config['exit_overrides']['TIKTOK'],{'stop_loss':12.0,'take_profit':17.0})
        self.assertEqual(config['entry_cost_caps'],{'TIKTOK':4.0,'X_SIGNAL':4.0,'HYPE_RADAR':4.0})
        stats=lab.stats(lab.STATE['books']['TIKTOK'])
        self.assertEqual((stats['stop_loss_net_pct'],stats['take_profit_net_pct']),(12,17))
        self.assertEqual(lab.stats(lab.STATE['books']['SCALPER'])['stop_loss_net_pct'],3)



class XSignalStrategyTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(lab.price_integrity,'check',return_value={'status':'pass','version':'OFFLINE_FIXTURE'})
        guard.start();self.addCleanup(guard.stop)
        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}
        self.old_path=lab.X_SIGNAL_PATH
        self.tmp=tempfile.NamedTemporaryFile('w+',delete=False)
        self.tmp.close();lab.X_SIGNAL_PATH=Path(self.tmp.name)
        self.addCleanup(setattr,lab,'X_SIGNAL_PATH',self.old_path)
        self.addCleanup(lambda:Path(self.tmp.name).unlink(missing_ok=True))

    def write_signals(self,signals):
        Path(self.tmp.name).write_text(json.dumps({'max_signal_age_seconds':600,'signals':signals}))

    def test_book_uses_same_paper_risk_shape_as_tiktok(self):
        book=lab.empty_book(next(s for s in lab.STRATEGIES if s['id']=='X_SIGNAL'))
        self.assertEqual((book['name'],book['starting_balance']),('X Signal',500))
        self.assertEqual(a.exit_rules('X_SIGNAL',3,10,60)['stop_loss'],12)
        self.assertEqual(a.exit_rules('X_SIGNAL',3,10,60)['take_profit'],17)
        self.assertIn('X_SIGNAL',a.LIMIT_TAKE_PROFIT_IDS)
        self.assertIn('X_SIGNAL',a.SNIPER_IDS)

    def test_cannot_enter_without_fresh_literal_x_signal(self):
        self.write_signals([])
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        self.assertIsNone(lab.STATE['books']['X_SIGNAL']['position'])

    def test_fresh_address_signal_allows_normal_paper_checks_to_open(self):
        self.write_signals([{'post_id':'123','handle':'elonmusk','seen_at_ms':NOW-1000,
                             'post_created_at_ms':NOW-2000,'addresses':[ADDRESS]}])
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        pos=lab.STATE['books']['X_SIGNAL']['position']
        self.assertIsNotNone(pos)
        self.assertEqual(pos['x_signal']['post_id'],'123')
        self.assertEqual(pos['address'],ADDRESS)

    def test_stale_signal_is_ignored(self):
        self.write_signals([{'post_id':'old','handle':'elonmusk','seen_at_ms':NOW-700_000,
                             'post_created_at_ms':NOW-700_000,'addresses':[ADDRESS]}])
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        self.assertIsNone(lab.STATE['books']['X_SIGNAL']['position'])


if __name__=='__main__':unittest.main()


class WhyQuietTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(lab.price_integrity,'check',return_value={'status':'pass','version':'OFFLINE_FIXTURE'})
        guard.start();self.addCleanup(guard.stop)
        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}

    def test_failures_name_every_condition_that_fails(self):
        rule=a.RULES['ULTRA_PRECISION']
        weak={'score':70,'liq':12000,'m5':40,'bs':.5,'lmc':.01,'age':1,'h1':0,'vol_liq':0,'mc':1e6,
              'flow':{'trades':0,'ratio':0,'buy_usd':0,'unique_wallets':0,'max_sell':0}}
        self.assertEqual(rule.failures(weak),['score','liquidity','move_5m','buy_sell','liquidity_cap','age'])
        self.assertEqual(a.RULES['ORDER_FLOW'].failures({**weak,'score':90,'liq':20000,'m5':5,'bs':1,'lmc':.1,'age':30}),
                         ['flow_trades','flow_ratio','wallets'])
        self.assertEqual(rule.failures({**weak,'score':95,'liq':30000,'m5':10,'bs':1.2,'lmc':.2,'age':30}),[])
        for key in a.REJECTION_LABELS: self.assertTrue(a.REJECTION_LABELS[key])

    def test_rejection_totals_accumulate_across_scans(self):
        feed=[{**coin(),'score':70,'liquidityUsd':12000,'updatedAt':NOW}]
        with patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open(feed,{}); lab.maybe_open(feed,{})
        book=lab.STATE['books']['ULTRA_PRECISION']
        total=book['rejection_totals']
        self.assertEqual((total['scans'],total['candidates']),(2,2))
        self.assertEqual(total['reasons']['score'],2); self.assertEqual(total['reasons']['liquidity'],2)
        self.assertNotIn('last_rule_match_at',total)
        self.assertEqual(book['entry_diagnostics']['scan_rejections']['score'],1)
        view=lab.compact_strategy_lab(lab.STATE)['books']['ULTRA_PRECISION']['why_quiet']
        self.assertEqual(view['candidates'],2)
        self.assertEqual(view['reasons'][0],{'reason':'score','count':2,'share':1.0})
        self.assertNotIn('astra',lab.compact_strategy_lab({**lab.STATE,'astra':{'book':{}}}))
        # The X Signal book counts the missing signal, not the rule.
        self.assertEqual(lab.STATE['books']['X_SIGNAL']['rejection_totals']['reasons'],{'no_x_signal':2})

    def test_totals_reset_when_the_policy_version_changes(self):
        lab.STATE['books']['TIKTOK']['rejection_totals']={'version':'OLD','scans':99,'candidates':99,'reasons':{'score':99}}
        with patch.object(lab,'now_ms',return_value=NOW): lab.maybe_open([],{})
        self.assertEqual(lab.STATE['books']['TIKTOK']['rejection_totals']['scans'],1)


class HypeRadarBookTests(unittest.TestCase):
    THEMES=[{'theme':'Robot dog','keywords':['doge','robot'],'hype':90,'generated_at':NOW-60_000}]

    def setUp(self):
        guard=patch.object(lab.price_integrity,'check',return_value={'status':'pass','version':'OFFLINE_FIXTURE'})
        guard.start();self.addCleanup(guard.stop)
        themes=patch.object(lab,'hype_themes',return_value=self.THEMES)
        themes.start();self.addCleanup(themes.stop)
        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}

    def feed(self,**coin_over):
        return [{**coin(),'symbol':'RDOG','name':'Robot Dog','score':65,'liquidityUsd':12000,'marketCap':45000,'updatedAt':NOW,**coin_over}]

    def test_configured_like_tiktok_with_theme_and_rug_gates(self):
        self.assertEqual(lab.book_exit_rules('HYPE_RADAR'),{'stop_loss':12,'take_profit':17,'max_hold_minutes':60})
        self.assertEqual(a.entry_cost_cap('HYPE_RADAR'),4.0)
        self.assertIn('HYPE_RADAR',a.SNIPER_IDS); self.assertIn('HYPE_RADAR',a.LIMIT_TAKE_PROFIT_IDS)
        self.assertIn('no_hype_match',a.REJECTION_LABELS); self.assertIn('rug_check',a.REJECTION_LABELS)

    def test_enters_only_a_theme_match_that_passes_the_rug_check(self):
        fast={'status':'pass','reasons':[]}; full={'status':'pending','reasons':['risk_check_pending']}
        with patch.object(lab.rug_guard,'fast_chain_check',return_value=fast),patch.object(lab.rug_guard,'check',return_value=full),\
             patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open(self.feed(),{})
        pos=lab.STATE['books']['HYPE_RADAR']['position']
        self.assertIsNotNone(pos)
        self.assertEqual((pos['hype_match']['theme'],pos['hype_match']['keyword'],pos['stop_loss_net_pct'],pos['take_profit_net_pct']),('Robot dog','robot',12,17))
        self.assertEqual(pos['hype_match']['rug_check']['full']['status'],'pending')
        self.assertEqual(lab.STATE['books']['HYPE_RADAR']['entry_diagnostics']['active_hype_themes'],1)

    def test_no_match_and_blocked_rug_are_counted(self):
        with patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open(self.feed(symbol='BNN',name='Banana'),{})
        book=lab.STATE['books']['HYPE_RADAR']
        self.assertIsNone(book['position']); self.assertEqual(book['rejection_totals']['reasons'],{'no_hype_match':1})
        with patch.object(lab.rug_guard,'fast_chain_check',return_value={'status':'pass','reasons':[]}),\
             patch.object(lab.rug_guard,'check',return_value={'status':'blocked','reasons':['rug_report_invalid_or_rugged']}),\
             patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open(self.feed(),{})
        self.assertIsNone(book['position']); self.assertEqual(book['rejection_totals']['reasons']['rug_check'],1)
        with patch.object(lab.rug_guard,'fast_chain_check',return_value={'status':'unavailable','reasons':['rpc']}),\
             patch.object(lab.rug_guard,'check',return_value={'status':'pending','reasons':[]}),\
             patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open(self.feed(),{})
        self.assertIsNone(book['position']); self.assertEqual(book['rejection_totals']['reasons']['rug_check'],2)
        # Other books are untouched by the hype gate.
        self.assertNotIn('no_hype_match',lab.STATE['books']['TIKTOK']['rejection_totals']['reasons'])

"""Synthetic causality tests for independent NEO selective PAPER mark strategy.

No network, keys, executable swap quotes, trading accounts, or real funds.
"""
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"scripts"))
import neo_selective_shadow_paper as shadow

T=1_800_000_000_000
POOL="FAKE_EXACT_POOL"
def row(ts,price,liq=25000,pool=POOL,category="early_activity_unverified",born=T-20000):
    return {
        "pool":pool,"ts":ts,"price_usd":price,"liq":liq,
        "born_ms":born,"baseline_price":1.0,"category":category,"symbol":"SYNTH",
    }

class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=shadow.connect_shadow(self.tmp.name+"/state.sqlite3")
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def step(self,at,price,liq=25000,**kwargs):
        with self.db:
            shadow.process_mark(self.db,row(at,price,liq,**kwargs))
    def test_flat_price_has_negative_net_modeled_value(self):
        self.assertLess(shadow.modeled_exit_cash(1,25000,1,25000),200)
    def test_no_entry_without_two_causal_observations(self):
        self.step(T,1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
        self.step(T+20_000,1.03)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],2)
    def test_two_targets_are_distinct_and_only_use_later_marks(self):
        self.step(T,1)
        self.step(T+20_000,1.03)
        self.step(T+40_000,1.13)
        active=[x["variant"] for x in self.db.execute("SELECT variant FROM positions")]
        self.assertEqual(active,["NET_TARGET_10"])
        settled=self.db.execute("SELECT * FROM closed").fetchall()
        self.assertEqual(len(settled),1)
        self.assertEqual(settled[0]["reason"],"NET_MARK_TARGET")
        self.assertGreater(settled[0]["net_mark_pnl_usd"],0)
        self.step(T+60_000,1.18)
        settled=self.db.execute("SELECT * FROM closed").fetchall()
        self.assertEqual(len(settled),2)
    def test_no_buy_for_low_liquidity_or_old_pool(self):
        self.step(T,1,liq=100)
        self.step(T+20_000,1.03,liq=100)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
        self.step(T+40_000,1.06,liq=25000,born=T-10*60000)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
    def test_spike_and_unknown_wallet_cluster_not_automatically_bought(self):
        self.step(T,1)
        self.step(T+20_000,1.5)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
        other="OTHER"
        self.step(T,1,pool=other,category="sharp_move_review")
        self.step(T+20_000,1.03,pool=other,category="sharp_move_review")
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
    def test_extreme_positive_price_gap_is_unresolved_not_credited_profit(self):
        self.step(T,1)
        self.step(T+20_000,1.03)
        self.step(T+40_000,100)
        rows=self.db.execute("SELECT * FROM closed").fetchall()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r["net_mark_pnl_usd"] is None for r in rows))
        self.assertTrue(all(r["reason"].startswith("UNRESOLVED") for r in rows))

    def test_late_target_mark_cannot_claim_an_early_executable_fill(self):
        self.step(T,1)
        self.step(T+20_000,1.03)
        self.step(T+200_000,1.15)
        rows=self.db.execute("SELECT * FROM closed").fetchall()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r["net_mark_pnl_usd"] is None for r in rows))

    def test_stop_not_clamped_to_nominal_five_percent(self):
        self.step(T,1)
        self.step(T+20_000,1.03)
        self.step(T+40_000,0.80)
        losses=self.db.execute("SELECT net_mark_pct FROM closed").fetchall()
        self.assertEqual(len(losses),2)
        self.assertTrue(all(r[0]<-5 for r in losses))
    def test_missing_sell_liquidity_is_unresolved_never_a_winner(self):
        self.step(T,1)
        self.step(T+20_000,1.03)
        self.step(T+5*60000,1.5,liq=None)
        closed=self.db.execute("SELECT * FROM closed").fetchall()
        self.assertEqual(len(closed),2)
        self.assertTrue(all(r["net_mark_pnl_usd"] is None for r in closed))
        data=shadow.report(self.db,T+6*60000)
        self.assertEqual(data["orders_created"],0)
        self.assertEqual(data["variants"]["NET_TARGET_5"]["unresolved"],1)
        self.assertEqual(data["variants"]["NET_TARGET_5"]["mark_wins"],0)
    def test_disappeared_pool_releases_slot_as_unresolved_not_profit(self):
        self.step(T,1)
        self.step(T+20_000,1.03)
        shadow.expire_unobserved_positions(self.db,T+7*60000)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM closed WHERE net_mark_pnl_usd IS NULL").fetchone()[0],2)
    def test_closed_pool_cannot_reenter(self):
        self.step(T,1)
        self.step(T+20_000,1.03)
        self.step(T+40_000,0.9)
        self.step(T+60_000,0.95)
        self.step(T+80_000,0.98)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
    def test_empty_latest_starts_forward_with_no_retrospective_trades(self):
        path=self.tmp.name+"/source.sqlite3"
        src=sqlite3.connect(path)
        src.executescript("""
        CREATE TABLE pools(pool TEXT PRIMARY KEY,created_ms INTEGER,symbol TEXT,baseline_price REAL,initial_category TEXT);
        CREATE TABLE marks(pool TEXT,received_ms INTEGER,price_usd REAL,liquidity_usd REAL,PRIMARY KEY(pool,received_ms));
        """)
        src.execute("INSERT INTO pools VALUES(?,?,?,?,?)",(POOL,T-20000,"TEST",1,"early_activity_unverified"))
        src.execute("INSERT INTO marks VALUES(?,?,?,?)",(POOL,T,1,25000))
        src.commit()
        shadow.init_shadow(self.db,src)
        self.assertEqual(shadow.replay_batch(src,self.db),0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM positions").fetchone()[0],0)
        src.close()

if __name__=="__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

import wide_discovery


class TestWideDiscovery(unittest.TestCase):
    def test_online_leads_only_when_fresh_and_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'stream.json'
            p.write_text(json.dumps({
                'schema_version':1,'connected':True,'updated_at':999000,
                'unique_mints_since_start':10000,'unique_mints_in_window':6000,
                'listed_mints':3,
                'tokens':[{'address':'AAA'},{'address':'BAD'},{'address':'AAA'},{'address':'BBB'}],
            }))
            tokens,stats=wide_discovery.read_candidates(p,now_ms=1000000,validator=lambda s: s in ('AAA','BBB'))
            self.assertEqual(tokens,['AAA','BBB'])
            self.assertEqual(stats['status'],'online')
            tokens,stats=wide_discovery.read_candidates(p,now_ms=1100000,validator=lambda s:True)
            self.assertEqual(tokens,[])
            self.assertEqual(stats['status'],'stale')

    def test_market_ledger_persists_distinct_counts(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ledger.json'
            now=1793828000000
            a=wide_discovery.DailyMarketLedger(p,clock_ms=lambda:now,interval_ms=0)
            self.assertEqual(a.observe(requested=['a','b','a'],with_market=['a'])['market_checked_unique_today'],2)
            self.assertEqual(a.observe(requested=['b','c'],with_market=['c'])['markets_validated_unique_today'],2)
            b=wide_discovery.DailyMarketLedger(p,clock_ms=lambda:now,interval_ms=0)
            self.assertEqual(b.stats()['market_checked_unique_today'],3)
            self.assertEqual(b.stats()['markets_validated_unique_today'],2)


if __name__=='__main__': unittest.main()

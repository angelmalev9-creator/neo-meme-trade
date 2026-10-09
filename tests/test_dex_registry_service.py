import unittest
from backend.dex_registry_service import SOL_MINT, gecko_records

MINT='6hTLdzZaaCjmqtn7RtaEMQsCbXT5Pg8q76tRViEGpump'
PAIR='2qWF4dn1JnqhCpAYJQnKVaKezzuUiG4xdnbnCSK1Pgcw'


class GeckoRecordsTests(unittest.TestCase):
    def test_tracks_non_pumpswap_without_tracking_sol(self):
        payload={'data':[{'attributes':{'address':PAIR},'relationships':{
            'dex':{'data':{'id':'raydium'}},
            'base_token':{'data':{'id':'solana_'+MINT}},
            'quote_token':{'data':{'id':'solana_'+SOL_MINT}},
        }}]}
        rows=gecko_records(payload,123)
        self.assertEqual(rows,[{'mint':MINT,'pair':PAIR,'dex':'raydium','source':'gecko-new-pools','observed_at':123}])


if __name__ == '__main__':
    unittest.main()

class TapeDbTests(unittest.TestCase):
    def test_reads_verified_pool_from_sqlite(self):
        import json, sqlite3, tempfile
        from pathlib import Path
        from backend.dex_registry_service import tape_db_records
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'tape.sqlite3'
            con=sqlite3.connect(path)
            con.execute('create table events(event_id text primary key, signature text, pair text, event_time integer, available integer, payload text)')
            payload={'address':MINT,'pairAddress':PAIR,'confirmed_swap':True}
            con.execute('insert into events values(?,?,?,?,?,?)',('e1','s1',PAIR,123,124,json.dumps(payload)))
            con.commit(); con.close()
            self.assertEqual(tape_db_records(path),[{'mint':MINT,'pair':PAIR,'dex':'pumpswap','source':'live-tape-sqlite','observed_at':123}])

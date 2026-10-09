import base64
import base64
import unittest

import coin_wallets as cw
import live_tape as tape

NOW = 1_800_000_000_000
MINT, PAIR = cw.b58encode(bytes([7]) * 32), cw.b58encode(bytes([9]) * 32)
W1, W2, W3 = (cw.b58encode(bytes([n]) * 32) for n in (1, 2, 3))


def gecko_trade(ts, kind, wallet, usd, tx, tokens=1000):
    return {'attributes': {'block_timestamp': ts, 'kind': kind, 'tx_from_address': wallet, 'tx_hash': tx,
                           'volume_in_usd': str(usd), 'to_token_address': MINT if kind == 'buy' else 'So111',
                           'from_token_address': 'So111' if kind == 'buy' else MINT,
                           'to_token_amount': str(tokens) if kind == 'buy' else '0.5',
                           'from_token_amount': '0.5' if kind == 'buy' else str(tokens),
                           'price_to_in_usd': '0.001', 'price_from_in_usd': '0.001'}}


def inverted_pumpswap_transaction(direction='SELL'):
    # Pool base is WSOL, selected token is pool quote: raw SELL means BUY of MINT.
    keys = [PAIR, W1, cw.b58encode(bytes([6]) * 32), tape.WSOL, MINT,
            cw.b58encode(bytes([7]) * 32), cw.b58encode(bytes([8]) * 32),
            cw.b58encode(bytes([9]) * 32), cw.b58encode(bytes([10]) * 32)]
    ix_data = next(k for k, v in tape.SWAP_DISCRIMINATORS.items() if v == direction)
    ev_data = next(k for k, v in tape.EVENT_DISCRIMINATORS.items() if v == direction)
    values = [NOW // 1000, 150_000_000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1_234_000_000]
    payload = ev_data + b''.join(int(x).to_bytes(8, 'little') for x in values)
    payload += b''.join(tape._b58decode(x) for x in [PAIR, W1, keys[5], keys[6]])
    logs = [f'Program {tape.PUMP_AMM} invoke [1]', 'Program data: ' + base64.b64encode(payload).decode(),
            f'Program {tape.PUMP_AMM} success']
    return {
        'slot': 55, 'blockTime': NOW // 1000,
        'transaction': {'message': {'accountKeys': [{'pubkey': x, 'signer': x == W1} for x in keys],
            'instructions': [{'programId': tape.PUMP_AMM, 'accounts': keys, 'data': tape._b58encode(ix_data)}]}},
        'meta': {'err': None, 'logMessages': logs,
                 'preTokenBalances': [
                     {'accountIndex': 5, 'mint': tape.WSOL, 'uiTokenAmount': {'amount': '0', 'decimals': 9}},
                     {'accountIndex': 6, 'mint': MINT, 'uiTokenAmount': {'amount': '0', 'decimals': 6}}],
                 'postTokenBalances': []},
    }


class Trades(unittest.TestCase):
    def setUp(self):
        cw._TRADES.clear(); cw._DIRECT.clear(); cw._HOLDERS.clear(); cw._ENTRY.clear()

    def test_parse_and_merge_with_tape_winning_on_the_same_signature(self):
        payload = {'data': [gecko_trade('2027-01-15T10:00:00Z', 'buy', W1, 120, 'sig1', 2000),
                            gecko_trade('2027-01-15T10:00:05Z', 'sell', W2, 40, 'sig2', 500),
                            {'attributes': {'kind': 'weird'}}]}
        rows = cw.parse_gecko_trades(payload, MINT)
        self.assertEqual([r['signature'] for r in rows], ['sig2', 'sig1'])
        self.assertEqual((rows[1]['direction'], rows[1]['token_amount'], rows[1]['usd_amount']), ('BUY', 2000.0, 120.0))
        self.assertEqual(rows[0]['ts'] - rows[1]['ts'], 5000)
        tape = [{'ts': rows[1]['ts'], 'direction': 'BUY', 'wallet': W1, 'signature': 'sig1', 'usd_amount': 118.5, 'token_amount': 2000,
                 'address': MINT, 'pairAddress': PAIR},
                {'ts': rows[1]['ts'] + 9000, 'direction': 'BUY', 'wallet': W3, 'signature': 'sig3', 'usd_amount': 10, 'token_amount': 100,
                 'address': MINT, 'pairAddress': PAIR}]
        merged = cw.merge_trades(cw.tape_rows(tape, MINT, PAIR, now=rows[1]['ts'] + 10_000), rows)
        self.assertEqual([r['signature'] for r in merged], ['sig3', 'sig2', 'sig1'])
        self.assertEqual((merged[2]['source'], merged[2]['usd_amount']), ('tape', 118.5))
        wallets = cw.wallet_activity(merged)
        self.assertEqual([w['wallet'] for w in wallets], [W1, W3, W2])
        self.assertEqual((wallets[0]['buys'], wallets[0]['bought_usd'], wallets[0]['net_usd'], wallets[0]['last_signature']), (1, 118.5, 118.5, 'sig1'))
        self.assertEqual((wallets[2]['sells'], wallets[2]['net_usd']), (1, -40.0))

    def test_trades_cache_keeps_last_good_rows_on_error(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if len(calls) > 1:
                raise RuntimeError('429')
            return {'data': [gecko_trade('2027-01-15T10:00:00Z', 'buy', W1, 5, 'a')]}
        first = cw.gecko_trades(PAIR, MINT, fetch, now=NOW)
        self.assertEqual(len(first['rows']), 1)
        self.assertIs(cw.gecko_trades(PAIR, MINT, fetch, now=NOW + 3000), first)
        failed = cw.gecko_trades(PAIR, MINT, fetch, now=NOW + 20_000)
        self.assertEqual((failed['error'], len(failed['rows'])), ('429', 1))
        self.assertEqual(calls[0], cw.GECKO_TRADES_URL.format(pair=PAIR))


def token_account_data(owner_b58):
    raw = bytearray(165)
    raw[32:64] = cw_b58decode(owner_b58)
    return base64.b64encode(bytes(raw)).decode()


def cw_b58decode(value):
    number = 0
    for ch in value:
        number = number * 58 + cw._B58.index(ch)
    out = number.to_bytes(32, 'big')
    return out


class DirectWalletFeed(unittest.TestCase):
    def setUp(self):
        cw._TRADES.clear(); cw._DIRECT.clear(); cw._HOLDERS.clear(); cw._ENTRY.clear()

    def test_direct_pool_scanner_emits_verified_wallet_rows_and_caches(self):
        calls = []
        def batch_rpc(batch):
            calls.append(batch)
            if batch[0][0] == 'getSignaturesForAddress':
                return [{'result': [
                    {'signature': 'sig-buy', 'blockTime': 1_800_000_000, 'err': None},
                    {'signature': 'sig-bad', 'blockTime': 1_800_000_001, 'err': None},
                ]}]
            return [{'result': {'id': method[1][0]}} for method in batch]

        def classifier(tx, metadata, **kwargs):
            signature = tx['id']
            if signature == 'sig-bad':
                return 'unclassified', [{'ts': NOW, 'direction': 'BUY', 'wallet': W2, 'address': MINT, 'pairAddress': PAIR,
                                         'token_amount': 10, 'usd_amount': 1, 'confirmed_swap': True,
                                         'quality_flags': ['SWAP_ACTOR_NOT_TRANSACTION_SIGNER']}], 'bad_actor'
            return 'unclassified', [{'ts': NOW, 'direction': 'BUY', 'wallet': W1, 'address': MINT, 'pairAddress': PAIR,
                                     'token_amount': 2000, 'usd_amount': 12.5, 'quote_amount': .1, 'quote_asset': 'SOL',
                                     'confirmed_swap': True, 'quality_flags': ['QUOTE_ASSET_USD_REFERENCE_ESTIMATE']}], 'estimated_usd'

        first = cw.direct_pool_trades(PAIR, MINT, coin={'symbol': 'TEST', 'priceUsd': .01, 'priceNative': .0001},
                                      batch_rpc=batch_rpc, classifier=classifier, now=NOW)
        self.assertEqual(len(first['rows']), 1)
        self.assertEqual((first['rows'][0]['wallet'], first['rows'][0]['source'], first['rows'][0]['usd_estimated']),
                         (W1, 'rpc_live', True))
        self.assertEqual(first['attempted'], 2)
        # A second dashboard poll inside the 2.5s cache window performs no RPC.
        second = cw.direct_pool_trades(PAIR, MINT, coin={}, batch_rpc=batch_rpc, classifier=classifier, now=NOW + 1000)
        self.assertIs(second, first)
        self.assertEqual(len(calls), 2)


    def test_onchain_windows_count_direct_rows_even_when_period_is_partial(self):
        rows = [
            {'ts': NOW - 10_000, 'direction': 'BUY', 'wallet': W1, 'signature': 'b1', 'usd_amount': 5, 'source': 'rpc_live'},
            {'ts': NOW - 20_000, 'direction': 'BUY', 'wallet': W2, 'signature': 'b2', 'usd_amount': 7, 'source': 'rpc_live'},
            {'ts': NOW - 30_000, 'direction': 'SELL', 'wallet': W3, 'signature': 's1', 'usd_amount': 3, 'source': 'tape'},
            {'ts': NOW - 40_000, 'direction': 'SELL', 'wallet': W3, 'signature': 'g1', 'usd_amount': 99, 'source': 'geckoterminal'},
        ]
        windows = cw.onchain_windows(rows, now=NOW)
        self.assertEqual((windows['m5']['buys'], windows['m5']['sells'], windows['m5']['buyers'], windows['m5']['sellers']), (2, 1, 2, 1))
        self.assertEqual((windows['m5']['buy_usd'], windows['m5']['sell_usd']), (12.0, 3.0))
        self.assertFalse(windows['m5']['complete'])
        self.assertEqual(windows['m5']['observed_seconds'], 30)
        proven = cw.onchain_windows(rows, now=NOW, coverage_since_ms=NOW - 400_000)
        self.assertTrue(proven['m5']['complete'])
        self.assertEqual(proven['m5']['observed_seconds'], 300)

    def test_quote_mint_pumpswap_is_normalized_to_selected_token_direction(self):
        calls = []
        tx = inverted_pumpswap_transaction('SELL')
        def batch_rpc(batch):
            calls.append(batch)
            if batch[0][0] == 'getSignaturesForAddress':
                return [{'result': [{'signature': 'hook-buy', 'blockTime': NOW // 1000, 'err': None}]}]
            return [{'result': tx}]
        out = cw.direct_pool_trades(
            PAIR, MINT,
            coin={'symbol': 'HOOK', 'dexId': 'pumpswap', 'priceUsd': 0.018, 'priceNative': 0.0001},
            batch_rpc=batch_rpc, now=NOW,
        )
        self.assertEqual(len(out['rows']), 1)
        row = out['rows'][0]
        self.assertEqual((row['direction'], row['wallet'], row['token_amount'], row['quote_asset']),
                         ('BUY', W1, 1234.0, tape.WSOL))
        self.assertAlmostEqual(row['quote_amount'], 0.15)
        self.assertAlmostEqual(row['usd_amount'], 27.0)
        self.assertTrue(row['usd_estimated'])

    def test_rpc_live_beats_gecko_but_verified_tape_beats_rpc_live(self):
        gecko = [{'ts': NOW, 'direction': 'BUY', 'wallet': W1, 'signature': 'same', 'usd_amount': 10,
                  'token_amount': 100, 'price_usd': .1, 'source': 'geckoterminal'}]
        direct = [{**gecko[0], 'usd_amount': 11, 'source': 'rpc_live'}]
        tape = [{**gecko[0], 'usd_amount': 12, 'source': 'tape'}]
        self.assertEqual(cw.merge_trades(gecko, direct)[0]['usd_amount'], 11)
        merged = cw.merge_trades(gecko, direct, tape)
        self.assertEqual((merged[0]['source'], merged[0]['usd_amount']), ('tape', 12))


class Holders(unittest.TestCase):
    def setUp(self):
        cw._TRADES.clear(); cw._DIRECT.clear(); cw._HOLDERS.clear(); cw._ENTRY.clear()

    def test_b58_roundtrip(self):
        for wallet in (W1, PAIR, cw.b58encode(bytes(32))):
            self.assertEqual(cw.b58encode(cw_b58decode(wallet)), wallet)
        self.assertEqual(cw.b58encode(bytes(32)), '1' * 32)

    def test_holders_decode_owner_share_pool_and_entry_time(self):
        acc1, acc2, acc3 = 'T1' + 'x' * 42, 'T2' + 'y' * 42, 'T3' + 'z' * 42
        calls = []
        def rpc(method, params):
            calls.append(method)
            if method == 'getTokenSupply':
                return {'value': {'amount': '1000000000000000', 'decimals': 6, 'uiAmount': 1_000_000_000.0}}
            if method == 'getTokenLargestAccounts':
                return {'value': [{'address': acc1, 'uiAmount': 300_000_000.0}, {'address': acc2, 'uiAmount': 50_000_000.0},
                                  {'address': acc3, 'uiAmount': 10_000_000.0}]}
            if method == 'getMultipleAccounts':
                return {'value': [{'data': [token_account_data(PAIR), 'base64']}, {'data': [token_account_data(W1), 'base64']}, None]}
            if method == 'getSignaturesForAddress':
                account = params[0]
                if account == acc2:
                    return [{'signature': 'newest', 'blockTime': 1_700_000_100}, {'signature': 'oldest', 'blockTime': 1_700_000_000}]
                return [{'signature': f's{i}', 'blockTime': 1} for i in range(cw.SIGNATURE_PAGE)]
            raise AssertionError(method)
        view = cw.holders(MINT, rpc, pool_accounts={PAIR}, now=NOW)
        pool, whale, unknown = view['holders']
        self.assertEqual((pool['wallet'], pool['is_pool'], pool['entry_status'], pool['share_pct']), (PAIR, True, 'pool', 30.0))
        self.assertEqual((whale['wallet'], whale['share_pct'], whale['entered_at'], whale['entry_status'], whale['first_signature']),
                         (W1, 5.0, 1_700_000_000_000, 'ok', 'oldest'))
        self.assertEqual((unknown['wallet'], unknown['entry_status']), (None, 'over_1000_txs'))
        self.assertEqual(calls.count('getSignaturesForAddress'), 2)
        # Second call inside the TTL: holders and entry times come from the cache.
        cw.holders(MINT, rpc, pool_accounts={PAIR}, now=NOW + 5000)
        self.assertEqual(calls.count('getTokenSupply'), 1)
        self.assertEqual(calls.count('getSignaturesForAddress'), 2)

    def test_build_shape_and_rpc_failure_is_reported_not_raised(self):
        def rpc(method, params):
            raise RuntimeError('rpc down')
        out = cw.build(MINT, PAIR, tape={'events': []}, fetch=lambda url: {'data': []}, rpc=rpc, direct={'rows': [{'ts': NOW, 'direction': 'BUY', 'wallet': W1, 'signature': 'live1', 'usd_amount': 7, 'token_amount': 70, 'price_usd': .1, 'source': 'rpc_live'}], 'error': None, 'fetched_at': NOW, 'attempted': 1}, now=NOW)
        self.assertEqual(out['holders'], [])
        self.assertIn('rpc down', out['holders_meta']['error'])
        self.assertEqual(out['trade_sources']['gecko_rows'], 0)
        self.assertEqual(out['trade_sources']['rpc_live_rows'], 1)
        self.assertEqual((out['onchain_windows']['m1']['buys'], out['onchain_windows']['m1']['sells']), (1, 0))
        self.assertEqual(out['trades'][0]['wallet'], W1)
        self.assertEqual(out['links']['token'], f'https://solscan.io/token/{MINT}')


if __name__ == '__main__':
    unittest.main()

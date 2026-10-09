#!/usr/bin/env python3
"""Who is buying, who is selling and who holds one token — dashboard only.

Read-only, no keys, nothing here feeds a trading decision:

* recent trades per wallet: a direct, cached Solana RPC scan of the selected
  pool merged with the verified on-chain tape and GeckoTerminal fallback. The
  direct scan reuses the strict PumpSwap parser, so wallet/direction are not
  guessed from token-balance deltas;
* top holders: Solana RPC ``getTokenLargestAccounts`` (top 20 token accounts),
  owners decoded from the token-account layout, share of ``getTokenSupply``,
  and an "entered at" estimate from the oldest signature of the token account
  (only when the account has fewer than 1000 signatures, otherwise unknown).

Every answer is cached per pool so a dashboard polling every few seconds
stays far under the public rate limits.
"""
import base64
import math
import re
import threading
import time
from typing import Any, Callable

GECKO_TRADES_URL = ('https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/trades'
                    '?trade_volume_in_usd_greater_than=0')
TRADES_TTL_MS = 10_000
HOLDERS_TTL_MS = 20_000
ENTRY_TTL_MS = 600_000
ERROR_TTL_MS = 30_000
DIRECT_TTL_MS = 2_500
DIRECT_ERROR_TTL_MS = 4_000
DIRECT_SIGNATURE_LIMIT = 240
DIRECT_TX_BUDGET = 12
MAX_WALLET_ROWS = 60
MAX_TRADE_ROWS = 120
DIRECT_HISTORY_ROWS = 600
ONCHAIN_WINDOW_SECONDS = {'m1': 60, 'm5': 300, 'm15': 900, 'm30': 1800, 'h1': 3600}
SIGNATURE_PAGE = 1000
_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
# Vault owners that are liquidity, not holders: Raydium AMM authority. PumpSwap
# vaults are owned by the pool account itself, which the caller passes in.
KNOWN_POOL_AUTHORITIES = {'5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1'}
_TRADES: dict[str, dict[str, Any]] = {}
_DIRECT: dict[str, dict[str, Any]] = {}
_HOLDERS: dict[str, dict[str, Any]] = {}
_ENTRY: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError, OverflowError):
        return default


def b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, 'big')
    out = ''
    while number:
        number, rem = divmod(number, 58)
        out = _B58[rem] + out
    pad = len(raw) - len(raw.lstrip(b'\0'))
    return '1' * pad + out


def _iso_ms(value: Any) -> int | None:
    """GeckoTerminal block_timestamp (ISO-8601, UTC) → epoch milliseconds."""
    if not value:
        return None
    text = str(value).strip().replace('Z', '+00:00')
    try:
        import datetime as dt
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return int(parsed.timestamp() * 1000)
    except ValueError:
        return None


# ----------------------------------------------------------------- trades --
def parse_gecko_trades(payload: dict[str, Any], mint: str) -> list[dict[str, Any]]:
    rows = []
    for item in (payload or {}).get('data') or []:
        attrs = (item or {}).get('attributes') or {}
        kind = str(attrs.get('kind') or '').lower()
        if kind not in ('buy', 'sell'):
            continue
        # Token amount is whichever side of the swap is the pool's base token.
        if mint and str(attrs.get('to_token_address') or '') == mint:
            token_amount = _num(attrs.get('to_token_amount'))
        elif mint and str(attrs.get('from_token_address') or '') == mint:
            token_amount = _num(attrs.get('from_token_amount'))
        else:
            token_amount = _num(attrs.get('to_token_amount') if kind == 'buy' else attrs.get('from_token_amount'))
        rows.append({
            'ts': _iso_ms(attrs.get('block_timestamp')), 'direction': kind.upper(),
            'wallet': str(attrs.get('tx_from_address') or ''), 'signature': str(attrs.get('tx_hash') or ''),
            'usd_amount': round(_num(attrs.get('volume_in_usd')), 2), 'token_amount': token_amount,
            'price_usd': _num(attrs.get('price_to_in_usd') if kind == 'buy' else attrs.get('price_from_in_usd')),
            'source': 'geckoterminal',
        })
    rows.sort(key=lambda r: r['ts'] or 0, reverse=True)
    return rows


def gecko_trades(pair: str, mint: str, fetch: Callable[[str], dict[str, Any]], *, now: int) -> dict[str, Any]:
    with _LOCK:
        cached = _TRADES.get(pair)
    if cached and 0 <= now - cached['fetched_at'] <= (ERROR_TTL_MS if cached.get('error') else TRADES_TTL_MS):
        return cached
    try:
        row = {'fetched_at': now, 'error': None, 'rows': parse_gecko_trades(fetch(GECKO_TRADES_URL.format(pair=pair)), mint)}
    except Exception as exc:
        row = {'fetched_at': now, 'error': str(exc)[:160], 'rows': (cached or {}).get('rows') or []}
    with _LOCK:
        _TRADES[pair] = row
    return row


_DASHBOARD_VALUATION_FLAGS = {'QUOTE_ASSET_USD_REFERENCE_ESTIMATE', 'QUOTE_USD_UNKNOWN'}


def _quote_mint_pumpswap_events(tx: dict[str, Any], metadata: dict[str, Any], tape: Any, *,
                                observed_at: int, ingested_at: int) -> list[dict[str, Any]]:
    """Decode PumpSwap when the selected token is the pool's quote mint.

    The engine tape intentionally keeps its stricter historical assumption that
    the tracked meme token is the pool base mint.  The dashboard can open any
    DexScreener pool, including valid PumpSwap pools whose SOL/USDC leg is base
    and the selected token is quote.  For this fallback we still require the
    official Pump AMM instruction discriminator *and* matching emitted
    BuyEvent/SellEvent; no balance-delta guessing is used.
    """
    if not isinstance(tx, dict) or not isinstance(tx.get('meta'), dict) or tx['meta'].get('err') is not None:
        return []
    if observed_at <= 0 or ingested_at <= 0 or observed_at > ingested_at:
        return []
    try:
        keys, instructions = tape._instruction_records(tx)
    except Exception:
        return []
    message = ((tx.get('transaction') or {}).get('message') or {})
    signers = {tape.key_of(key) for key in message.get('accountKeys') or [] if isinstance(key, dict) and key.get('signer')}
    if not signers and type((message.get('header') or {}).get('numRequiredSignatures')) is int:
        signers = set(keys[:message['header']['numRequiredSignatures']])

    swaps = []
    for index, program, accounts, data in instructions:
        if program != tape.PUMP_AMM or metadata.get('pair') not in accounts or data[:8] not in tape.SWAP_DISCRIMINATORS:
            continue
        if len(accounts) < 7:
            continue
        # Inverted orientation: counter asset is pool base, selected meme token
        # is pool quote.  This is the layout seen on valid PumpSwap pools such
        # as Hook; direction must therefore be flipped for the selected token.
        if (accounts[0] == metadata.get('pair') and accounts[4] == metadata.get('address')
                and accounts[3] in (tape.USDC, tape.WSOL)):
            swaps.append((index, tape.SWAP_DISCRIMINATORS[data[:8]], accounts))
    if not swaps:
        return []

    decimals: dict[str, tuple[str, int]] = {}
    meta = tx['meta']
    for balance in (meta.get('preTokenBalances') or []) + (meta.get('postTokenBalances') or []):
        try:
            account = keys[int(balance['accountIndex'])]
            entry = (balance['mint'], int(balance['uiTokenAmount']['decimals']))
            if account in decimals and decimals[account] != entry:
                return []
            decimals[account] = entry
        except (IndexError, KeyError, ValueError, TypeError):
            return []

    # Prefer Anchor emit_cpi when supplied, exactly like the verified tape, so
    # dual log/CPI representations do not double-count one swap.
    cpi = [(index, data[8:]) for index, program, _, data in instructions
           if program == tape.PUMP_AMM and data[:8] == tape.ANCHOR_EVENT_CPI
           and data[8:16] in tape.EVENT_DISCRIMINATORS]
    event_payloads: list[tuple[int, bytes]] = []
    if cpi:
        event_payloads = [(1_000_000 + index, payload) for index, payload in cpi]
    else:
        stack: list[str] = []
        for log_index, line in enumerate(meta.get('logMessages') or []):
            invoke = re.match(r'^Program (\w+) invoke \[(\d+)\]$', line)
            if invoke:
                stack = stack[:int(invoke.group(2)) - 1] + [invoke.group(1)]
                continue
            if re.match(r'^Program \w+ (success|failed)', line):
                if stack:
                    stack.pop()
                continue
            if not line.startswith('Program data: ') or not stack or stack[-1] != tape.PUMP_AMM:
                continue
            try:
                payload = base64.b64decode(line[14:], validate=True)
            except (ValueError, TypeError):
                continue
            if payload[:8] in tape.EVENT_DISCRIMINATORS:
                event_payloads.append((log_index, payload))

    decoded: list[dict[str, Any]] = []
    for event_index, payload in event_payloads:
        raw_direction = tape.EVENT_DISCRIMINATORS.get(payload[:8])
        if not raw_direction or len(payload) < 248:
            continue
        pool, wallet, pool_base_account, pool_quote_account = [tape._b58encode(payload[a:a + 32])
                                                                for a in (120, 152, 184, 216)]
        matches = [item for item in swaps if item[1] == raw_direction and item[2][0] == pool
                   and item[2][1] == wallet and item[2][5] == pool_base_account
                   and item[2][6] == pool_quote_account]
        if not matches or pool != metadata.get('pair'):
            continue
        accounts = matches[0][2]
        counter_mint, selected_mint = accounts[3], accounts[4]
        counter_info, token_info = decimals.get(pool_base_account), decimals.get(pool_quote_account)
        if not token_info or token_info[0] != selected_mint or selected_mint != metadata.get('address'):
            continue
        if counter_mint not in (tape.USDC, tape.WSOL):
            continue
        counter_decimals = 6 if counter_mint == tape.USDC else 9
        if counter_info and counter_info != (counter_mint, counter_decimals):
            continue
        try:
            event_time = int.from_bytes(payload[8:16], 'little', signed=True) * 1000
            block_time = int(tx.get('blockTime') or 0) * 1000
        except (TypeError, ValueError, OverflowError):
            continue
        if not block_time or not 0 < event_time <= ingested_at or abs(event_time - block_time) > 2000:
            continue
        pool_base_raw = int.from_bytes(payload[16:24], 'little')
        pool_quote_raw = int.from_bytes(payload[112:120], 'little')
        if pool_base_raw <= 0 or pool_quote_raw <= 0 or not 0 <= token_info[1] <= 18:
            continue

        token_amount = pool_quote_raw / 10 ** token_info[1]
        counter_amount = pool_base_raw / 10 ** counter_decimals
        flags: list[str] = []
        if wallet not in signers:
            flags.append('SWAP_ACTOR_NOT_TRANSACTION_SIGNER')
        usd, valuation = None, 'UNKNOWN'
        if counter_mint == tape.USDC:
            usd, valuation = counter_amount, 'ACTUAL_USDC_QUOTE_LEG'
        else:
            reference = metadata.get('quote_usd_reference')
            reference_time = int(metadata.get('quote_reference_at') or 0)
            try:
                valid_reference = reference is not None and math.isfinite(float(reference)) and float(reference) > 0
            except (TypeError, ValueError, OverflowError):
                valid_reference = False
            if valid_reference and 0 <= ingested_at - reference_time <= 60_000 and abs(event_time - reference_time) <= 60_000:
                usd = counter_amount * float(reference)
                valuation = metadata.get('quote_reference_source') or 'QUOTE_ASSET_REFERENCE_ESTIMATE'
                if valuation != 'JUPITER_CONVERSION_QUOTE_REFERENCE':
                    flags.append('QUOTE_ASSET_USD_REFERENCE_ESTIMATE')
            else:
                flags.append('QUOTE_USD_UNKNOWN')

        direction = 'BUY' if raw_direction == 'SELL' else 'SELL'
        decoded.append({
            'ts': event_time, 'event_time': event_time, 'observed_at': observed_at, 'ingested_at': ingested_at,
            'available_at': ingested_at, 'event_index': event_index, 'direction': direction, 'wallet': wallet,
            'address': selected_mint, 'pairAddress': pool, 'symbol': metadata.get('symbol', '?'),
            'token_raw_amount': str(pool_quote_raw), 'token_decimals': token_info[1], 'token_amount': token_amount,
            'quote_asset': counter_mint, 'quote_raw_amount': str(pool_base_raw), 'quote_decimals': counter_decimals,
            'quote_amount': counter_amount, 'usd_amount': round(usd, 8) if usd is not None else None,
            'usd_valuation_source': valuation, 'quality_flags': flags, 'program_id': tape.PUMP_AMM,
            'slot': tx.get('slot'), 'usd_valuation_estimated': counter_mint != tape.USDC,
            'quote_reference_at': metadata.get('quote_reference_at') if counter_mint != tape.USDC else None,
            'provider': metadata.get('provider', 'solana-rpc'), 'note': direction, 'confirmed_swap': True,
        })
    return decoded


def direct_pool_trades(pair: str, mint: str, *, coin: dict[str, Any] | None = None,
                       batch_rpc: Callable[[list[tuple[str, list[Any]]]], list[dict[str, Any]]] | None = None,
                       classifier: Callable[..., tuple[str, list[dict[str, Any]], str | None]] | None = None,
                       now: int | None = None) -> dict[str, Any]:
    """Read the selected pool directly from Solana RPC for the dashboard.

    This is deliberately separate from the global tape recorder.  The tape is
    bounded to the busiest pools for engine evidence; the dashboard, however,
    must be able to show wallet activity for whichever pool the user opens.
    Only events produced by the same strict PumpSwap parser are accepted.  A
    scanner-derived SOL/USD conversion may be shown as an estimate, but actor
    or instruction-integrity flags are never tolerated.
    """
    now = now or now_ms()
    if not pair or not mint:
        return {'fetched_at': now, 'error': 'pair_or_mint_missing', 'rows': [], 'attempted': 0}
    with _LOCK:
        cached = _DIRECT.get(pair)
    ttl = DIRECT_ERROR_TTL_MS if cached and cached.get('error') else DIRECT_TTL_MS
    if cached and 0 <= now - int(cached.get('fetched_at') or 0) <= ttl:
        return cached

    verified_tape = None
    if batch_rpc is None or classifier is None:
        import live_tape as verified_tape
        batch_rpc = batch_rpc or verified_tape.rpc_batch
        classifier = classifier or verified_tape.classify_transaction

    previous_rows = list((cached or {}).get('rows') or [])
    seen = set((cached or {}).get('seen') or [])
    try:
        answers = batch_rpc([('getSignaturesForAddress', [pair, {'limit': DIRECT_SIGNATURE_LIMIT, 'commitment': 'confirmed'}])])
        answer = answers[0] if answers else {'error': {'code': 'NO_RPC_RESPONSE'}}
        if answer.get('error') or not isinstance(answer.get('result'), list):
            raise RuntimeError(str(answer.get('error') or 'signature rpc unavailable')[:160])
        signatures = [row for row in answer['result'] if isinstance(row, dict) and isinstance(row.get('signature'), str)]
        candidates = [row for row in signatures if not row.get('err') and row['signature'] not in seen][:DIRECT_TX_BUDGET]
        calls = [('getTransaction', [row['signature'], {'encoding': 'jsonParsed', 'commitment': 'confirmed',
                                                        'maxSupportedTransactionVersion': 0}]) for row in candidates]
        bodies = batch_rpc(calls) if calls else []
        coin = coin or {}
        price_usd, price_native = _num(coin.get('priceUsd')), _num(coin.get('priceNative'))
        quote_reference = price_usd / price_native if price_usd > 0 and price_native > 0 else None
        metadata = {'pair': pair, 'address': mint, 'symbol': coin.get('symbol') or '?', 'dexId': coin.get('dexId'),
                    'provider': 'dashboard-solana-rpc-live', 'quote_usd_reference': quote_reference,
                    'quote_reference_at': now if quote_reference else None,
                    'quote_reference_source': 'SCANNER_QUOTE_ASSET_REFERENCE_ESTIMATE' if quote_reference else None}
        fresh: list[dict[str, Any]] = []
        for row, body in zip(candidates, bodies):
            tx = body.get('result') if isinstance(body, dict) and not body.get('error') else None
            if tx is None:
                # Very fresh signatures can briefly have a null body. Do not
                # mark them seen; the next 3s dashboard poll retries them.
                continue
            signature = row['signature']
            seen.add(signature)
            try:
                _classification, events, _reason = classifier(tx, metadata, observed_at=now, ingested_at=now)
                if not events and _reason == 'UNSUPPORTED_POOL_INSTRUCTION' and verified_tape is not None:
                    events = _quote_mint_pumpswap_events(tx, metadata, verified_tape, observed_at=now, ingested_at=now)
            except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
                continue
            for event in events:
                if not isinstance(event, dict) or event.get('address') != mint or event.get('pairAddress') != pair:
                    continue
                direction, wallet = str(event.get('direction') or '').upper(), str(event.get('wallet') or '')
                flags = set(event.get('quality_flags') or [])
                if direction not in ('BUY', 'SELL') or not wallet or not event.get('confirmed_swap') or (flags - _DASHBOARD_VALUATION_FLAGS):
                    continue
                token_amount = _num(event.get('token_amount'))
                if token_amount <= 0:
                    continue
                usd = _num(event.get('usd_amount'))
                fresh.append({'ts': int(_num(event.get('ts')) or _num(row.get('blockTime')) * 1000),
                              'direction': direction, 'wallet': wallet, 'signature': signature,
                              'usd_amount': round(usd, 2), 'token_amount': token_amount,
                              'price_usd': usd / token_amount if usd > 0 else None,
                              'quote_amount': _num(event.get('quote_amount')),
                              'quote_asset': event.get('quote_asset'), 'usd_estimated': bool(flags),
                              'source': 'rpc_live'})
        rows = merge_trades(fresh, previous_rows)[:DIRECT_HISTORY_ROWS]
        successful = [row for row in signatures if not row.get('err')]
        page_drained = all(row['signature'] in seen for row in successful)
        oldest_signature_ms = min((int(_num(row.get('blockTime')) * 1000) for row in signatures if _num(row.get('blockTime')) > 0), default=0)
        coverage_since_ms = oldest_signature_ms if page_drained and oldest_signature_ms > 0 else None
        # Keep bounded signature memory. Current RPC head comes first, then any
        # still-useful older values so a pool with no new trades stays cheap.
        ordered_seen = [row['signature'] for row in signatures if row['signature'] in seen]
        for signature in (cached or {}).get('seen') or []:
            if signature not in ordered_seen:
                ordered_seen.append(signature)
        result = {'fetched_at': now, 'error': None, 'rows': rows, 'seen': ordered_seen[:300],
                  'attempted': len(candidates), 'signature_rows': len(signatures),
                  'page_drained': page_drained, 'coverage_since_ms': coverage_since_ms}
    except Exception as exc:
        result = {'fetched_at': now, 'error': str(exc)[:160], 'rows': previous_rows,
                  'seen': list(seen)[:300], 'attempted': 0, 'signature_rows': 0}
    with _LOCK:
        _DIRECT[pair] = result
        if len(_DIRECT) > 100:
            stale = sorted(_DIRECT, key=lambda key: int(_DIRECT[key].get('fetched_at') or 0))[:25]
            for key in stale:
                _DIRECT.pop(key, None)
    return result


def tape_rows(events: list[dict[str, Any]], address: str, pair: str, *, now: int) -> list[dict[str, Any]]:
    out = []
    for e in events:
        if not isinstance(e, dict) or e.get('address') != address or (pair and e.get('pairAddress') != pair):
            continue
        if e.get('quality_flags') or _num(e.get('usd_amount')) <= 0 or not 0 < _num(e.get('ts')) <= now:
            continue
        out.append({'ts': int(_num(e.get('ts'))), 'direction': str(e.get('direction') or '').upper(),
                    'wallet': str(e.get('wallet') or ''), 'signature': str(e.get('signature') or ''),
                    'usd_amount': round(_num(e.get('usd_amount')), 2), 'token_amount': _num(e.get('token_amount')),
                    'price_usd': None, 'source': 'tape'})
    return out


def merge_trades(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union by transaction signature; stronger on-chain evidence wins."""
    priority = {'geckoterminal': 0, 'rpc_live': 1, 'tape': 2}
    seen: dict[str, dict[str, Any]] = {}
    order: list[dict[str, Any]] = []
    for rows in sources:
        for row in rows:
            key = row.get('signature') or f"{row.get('ts')}:{row.get('wallet')}:{row.get('usd_amount')}"
            if key in seen:
                if priority.get(str(row.get('source')), -1) > priority.get(str(seen[key].get('source')), -1):
                    seen[key].update(row)
                continue
            seen[key] = dict(row)
            order.append(seen[key])
    order.sort(key=lambda r: r.get('ts') or 0, reverse=True)
    return order


def onchain_windows(trades: list[dict[str, Any]], *, now: int, coverage_since_ms: int | None = None) -> dict[str, dict[str, Any]]:
    """Aggregate direct/tape evidence for the dashboard period cards.

    Counts remain useful while the direct scanner is still warming up, but the
    response says when a window is only partially observed so the UI never
    presents a lower bound as a complete period total.
    """
    rows = [row for row in trades if row.get('source') in ('rpc_live', 'tape')
            and 0 < _num(row.get('ts')) <= now and row.get('direction') in ('BUY', 'SELL')]
    oldest = min((int(_num(row.get('ts'))) for row in rows), default=now)
    observed_since = int(coverage_since_ms or oldest)
    available_seconds = max(0, (now - observed_since) // 1000) if rows else 0
    coverage_proven = bool(coverage_since_ms)
    windows: dict[str, dict[str, Any]] = {}
    for key, seconds in ONCHAIN_WINDOW_SECONDS.items():
        cutoff = now - seconds * 1000
        inside = [row for row in rows if _num(row.get('ts')) >= cutoff]
        buys = [row for row in inside if row.get('direction') == 'BUY']
        sells = [row for row in inside if row.get('direction') == 'SELL']
        windows[key] = {
            'buys': len(buys), 'sells': len(sells),
            'buyers': len({row.get('wallet') for row in buys if row.get('wallet')}),
            'sellers': len({row.get('wallet') for row in sells if row.get('wallet')}),
            'buy_usd': round(sum(_num(row.get('usd_amount')) for row in buys), 2),
            'sell_usd': round(sum(_num(row.get('usd_amount')) for row in sells), 2),
            'observed_seconds': min(seconds, int(available_seconds)),
            'complete': bool(rows) and coverage_proven and available_seconds >= seconds,
            'trades': len(inside),
        }
    return windows


def wallet_activity(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-wallet totals over the trades in hand, biggest net buyers first."""
    by_wallet: dict[str, dict[str, Any]] = {}
    for row in trades:
        wallet = row.get('wallet')
        if not wallet:
            continue
        w = by_wallet.setdefault(wallet, {'wallet': wallet, 'buys': 0, 'sells': 0, 'bought_usd': 0.0, 'sold_usd': 0.0,
                                           'bought_tokens': 0.0, 'sold_tokens': 0.0, 'first_seen': None, 'last_seen': None,
                                           'last_signature': None})
        if row['direction'] == 'BUY':
            w['buys'] += 1; w['bought_usd'] += _num(row.get('usd_amount')); w['bought_tokens'] += _num(row.get('token_amount'))
        else:
            w['sells'] += 1; w['sold_usd'] += _num(row.get('usd_amount')); w['sold_tokens'] += _num(row.get('token_amount'))
        ts = row.get('ts')
        if ts:
            w['first_seen'] = ts if w['first_seen'] is None else min(w['first_seen'], ts)
            if w['last_seen'] is None or ts >= w['last_seen']:
                w['last_seen'] = ts; w['last_signature'] = row.get('signature')
    rows = []
    for w in by_wallet.values():
        w['net_usd'] = round(w['bought_usd'] - w['sold_usd'], 2)
        w['net_tokens'] = w['bought_tokens'] - w['sold_tokens']
        for key in ('bought_usd', 'sold_usd'):
            w[key] = round(w[key], 2)
        rows.append(w)
    rows.sort(key=lambda w: (-w['net_usd'], -(w['bought_usd'] + w['sold_usd'])))
    return rows[:MAX_WALLET_ROWS]


# ---------------------------------------------------------------- holders --
def decode_token_account_owner(data_b64: str) -> str | None:
    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        return None
    if len(raw) < 64:
        return None
    return b58encode(raw[32:64])


def fetch_holders(mint: str, rpc: Callable[[str, list[Any]], Any], *, pool_accounts: set[str]) -> dict[str, Any]:
    supply = rpc('getTokenSupply', [mint]) or {}
    supply_value = (supply.get('value') or {}) if isinstance(supply, dict) else {}
    decimals = int(_num(supply_value.get('decimals')))
    total = _num(supply_value.get('uiAmount')) or _num(supply_value.get('amount')) / (10 ** decimals or 1)
    largest = rpc('getTokenLargestAccounts', [mint]) or {}
    accounts = [a for a in ((largest.get('value') or []) if isinstance(largest, dict) else []) if isinstance(a, dict)]
    addresses = [str(a.get('address')) for a in accounts if a.get('address')]
    owners: dict[str, str | None] = {}
    if addresses:
        multi = rpc('getMultipleAccounts', [addresses, {'encoding': 'base64'}]) or {}
        values = (multi.get('value') or []) if isinstance(multi, dict) else []
        for account, value in zip(addresses, values):
            data = ((value or {}).get('data') or [None])[0] if isinstance(value, dict) else None
            owners[account] = decode_token_account_owner(data) if isinstance(data, str) else None
    holders = []
    for account in accounts:
        token_account = str(account.get('address') or '')
        amount = _num(account.get('uiAmount')) or _num(account.get('amount')) / (10 ** decimals or 1)
        owner = owners.get(token_account)
        holders.append({
            'token_account': token_account, 'wallet': owner, 'amount': amount,
            'share_pct': round(amount / total * 100, 4) if total > 0 else None,
            'is_pool': bool(owner and owner in pool_accounts) or token_account in pool_accounts,
            'entered_at': None, 'entry_status': 'pending',
        })
    holders.sort(key=lambda h: -h['amount'])
    return {'decimals': decimals, 'supply': total, 'holders': holders}


def entry_time(token_account: str, rpc: Callable[[str, list[Any]], Any], *, now: int) -> dict[str, Any]:
    """Oldest signature of the token account ≈ when this holder first received the token."""
    with _LOCK:
        cached = _ENTRY.get(token_account)
    if cached and 0 <= now - cached['checked_at'] <= (ERROR_TTL_MS if cached.get('status') == 'error' else ENTRY_TTL_MS):
        return cached
    try:
        rows = rpc('getSignaturesForAddress', [token_account, {'limit': SIGNATURE_PAGE}]) or []
        rows = [r for r in rows if isinstance(r, dict)]
        if not rows:
            result = {'status': 'unknown', 'entered_at': None}
        elif len(rows) >= SIGNATURE_PAGE:
            result = {'status': 'over_1000_txs', 'entered_at': None}
        else:
            oldest = rows[-1]
            result = {'status': 'ok', 'entered_at': int(_num(oldest.get('blockTime')) * 1000) or None,
                      'first_signature': oldest.get('signature'), 'tx_count': len(rows)}
    except Exception as exc:
        result = {'status': 'error', 'entered_at': None, 'error': str(exc)[:120]}
    result['checked_at'] = now
    with _LOCK:
        _ENTRY[token_account] = result
        if len(_ENTRY) > 2000:
            for stale in sorted(_ENTRY, key=lambda k: _ENTRY[k]['checked_at'])[:500]:
                _ENTRY.pop(stale, None)
    return result


def holders(mint: str, rpc: Callable[[str, list[Any]], Any], *, pool_accounts: set[str], now: int,
            max_entry_lookups: int = 20) -> dict[str, Any]:
    with _LOCK:
        cached = _HOLDERS.get(mint)
    if cached and 0 <= now - cached['fetched_at'] <= (ERROR_TTL_MS if cached.get('error') else HOLDERS_TTL_MS):
        view = cached
    else:
        try:
            view = {**fetch_holders(mint, rpc, pool_accounts=pool_accounts), 'fetched_at': now, 'error': None}
        except Exception as exc:
            view = {'fetched_at': now, 'error': str(exc)[:160], 'holders': (cached or {}).get('holders') or [],
                    'supply': (cached or {}).get('supply'), 'decimals': (cached or {}).get('decimals')}
        with _LOCK:
            _HOLDERS[mint] = view
    looked_up = 0
    for holder in view.get('holders') or []:
        if holder.get('is_pool') or not holder.get('token_account'):
            holder['entry_status'] = 'pool' if holder.get('is_pool') else 'unknown'
            continue
        if looked_up >= max_entry_lookups:
            break
        info = entry_time(holder['token_account'], rpc, now=now)
        looked_up += 1
        holder['entered_at'] = info.get('entered_at')
        holder['entry_status'] = info.get('status')
        holder['first_signature'] = info.get('first_signature')
    return view


# ------------------------------------------------------------------ build --
def build(address: str, pair: str, *, tape: dict[str, Any], fetch: Callable[[str], dict[str, Any]],
          rpc: Callable[[str, list[Any]], Any], pool_accounts: set[str] | None = None,
          direct: dict[str, Any] | None = None, gecko: dict[str, Any] | None = None,
          holder_view: dict[str, Any] | None = None, max_entry_lookups: int = 8,
          now: int | None = None) -> dict[str, Any]:
    now = now or now_ms()
    gecko = gecko if gecko is not None else (gecko_trades(pair, address, fetch, now=now) if pair else {'rows': [], 'error': 'no_pair', 'fetched_at': now})
    tape_list = tape_rows(tape.get('events') or [], address, pair, now=now)
    direct = direct or {'rows': [], 'error': None, 'fetched_at': now, 'attempted': 0}
    direct_rows = list(direct.get('rows') or [])
    onchain_rows = merge_trades(direct_rows, tape_list)
    trades = merge_trades(gecko['rows'], direct_rows, tape_list)
    pools = set(pool_accounts or set()) | KNOWN_POOL_AUTHORITIES | ({pair} if pair else set())
    holder_view = holder_view if holder_view is not None else holders(address, rpc, pool_accounts=pools, now=now, max_entry_lookups=max_entry_lookups)
    return {
        'address': address, 'pairAddress': pair, 'at': now,
        'trades': trades[:MAX_TRADE_ROWS],
        'wallets': wallet_activity(trades),
        'onchain_windows': onchain_windows(onchain_rows, now=now, coverage_since_ms=direct.get('coverage_since_ms')),
        'trade_sources': {'tape_rows': len(tape_list), 'rpc_live_rows': len(direct_rows), 'rpc_live_error': direct.get('error'),
                          'rpc_live_fetched_at': direct.get('fetched_at'), 'rpc_live_attempted': direct.get('attempted', 0),
                          'rpc_live_page_drained': direct.get('page_drained'), 'rpc_live_coverage_since_ms': direct.get('coverage_since_ms'),
                          'gecko_rows': len(gecko['rows']), 'gecko_error': gecko.get('error'),
                          'gecko_fetched_at': gecko.get('fetched_at'), 'tape_coverage': ((tape.get('pair_coverage') or {}).get(pair) or {}).get('status')},
        'holders': holder_view.get('holders') or [],
        'holders_meta': {'supply': holder_view.get('supply'), 'decimals': holder_view.get('decimals'),
                         'fetched_at': holder_view.get('fetched_at'), 'error': holder_view.get('error'),
                         'top_n': len(holder_view.get('holders') or []), 'source': 'solana-rpc getTokenLargestAccounts'},
        'links': {'token': f'https://solscan.io/token/{address}', 'pool': f'https://solscan.io/account/{pair}' if pair else None},
    }

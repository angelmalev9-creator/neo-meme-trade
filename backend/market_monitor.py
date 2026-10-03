#!/usr/bin/env python3
import json, math, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import requests

HOST = os.getenv('NEO_MONITOR_HOST', '127.0.0.1')
PORT = int(os.getenv('NEO_MONITOR_PORT', '8788'))
SCAN_SECONDS = int(os.getenv('NEO_SCAN_SECONDS', '15'))
POSITION_SCAN_SECONDS = float(os.getenv('NEO_POSITION_SCAN_SECONDS', '2'))
STATE_PATH = Path(os.getenv('NEO_MARKET_STATE_PATH', '/var/lib/neo-market/state.json'))
AUDIT_PATH = Path(os.getenv('NEO_MARKET_AUDIT_PATH', '/var/lib/neo-market/audit.jsonl'))
LIVE_TAPE_PATH = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
STRATEGY_LAB_PATH = Path(os.getenv('NEO_STRATEGY_LAB_PATH', '/var/lib/neo-market/strategy_lab.json'))
POSITION_SCAN_SECONDS = float(os.getenv('NEO_POSITION_SCAN_SECONDS', '2'))
DEX_API = 'https://api.dexscreener.com'
MAX_FEED = 70
ENTRY_SCORE = 80.0
MAX_POSITIONS = 1
STOP_LOSS_PCT = 4.0
TAKE_PROFIT_PCT = 18.0
TRAILING_PCT = 4.0
MAX_HOLD_MINUTES = 7
WEAK_CHECK_MINUTES = 5
STALE_EXIT_MINUTES = 7
STALE_MIN_PROFIT_PCT = 3.0
LEARNING_WINDOW = 60
HEALTH_WINDOW = 12
STARTING_BALANCE_USD = 1000.0
TRADE_NOTIONAL_USD = 150.0
MAX_DAILY_LOSS_USD = 30.0
MIN_LIQUIDITY_USD = 10000.0
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NEO-Meme-Market-Monitor/2.0', 'Accept': 'application/json'})

def now_ms() -> int:
    return int(time.time() * 1000)


def num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def clamp(value: float) -> float:
    return max(0.0, min(100.0, value))

def read_strategy_lab() -> dict[str, Any]:
    try:
        data = json.loads(STRATEGY_LAB_PATH.read_text())
        return data if isinstance(data, dict) else {'status': 'offline', 'books': {}, 'stats': {}}
    except Exception:
        return {'status': 'offline', 'books': {}, 'stats': {}}

def read_live_tape() -> dict[str, Any]:
    try:
        data = json.loads(LIVE_TAPE_PATH.read_text())
        return data if isinstance(data, dict) else {'status': 'offline', 'events': []}
    except Exception:
        return {'status': 'offline', 'events': []}


def api(path: str) -> Any:
    response = SESSION.get(f'{DEX_API}{path}', timeout=15)
    response.raise_for_status()
    return response.json()


def signal(kind: str, title: str, detail: str) -> dict[str, str]:
    return {'kind': kind, 'title': title, 'detail': detail}


class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running = True
        self.status = 'starting'
        self.message = 'Starting NEO live market monitor.'
        self.last_scan_at = 0
        self.scan_count = 0
        self.feed: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.price_history: dict[str, list[dict[str, Any]]] = {}
        self.source_status = {'dexscreener': 'starting'}
        self.demo_starting_balance_usd = STARTING_BALANCE_USD
        self.demo_balance_usd = STARTING_BALANCE_USD
        self.demo_started_at = now_ms()
        self.demo_session_id = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
        self.trade_seq = 0
        self.load()

    def load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text())
            self.positions = data.get('positions', [])[-20:]
            self.history = data.get('history', [])[-300:]
            self.events = data.get('events', [])[-100:]
            self.demo_starting_balance_usd = num(data.get('demo_starting_balance_usd'), STARTING_BALANCE_USD)
            self.demo_balance_usd = num(data.get('demo_balance_usd'), self.demo_starting_balance_usd)
            self.demo_started_at = int(data.get('demo_started_at') or self.demo_started_at)
            self.demo_session_id = str(data.get('demo_session_id') or self.demo_session_id)
            self.trade_seq = int(data.get('trade_seq') or 0)
            raw = data.get('price_history', {})
            if isinstance(raw, dict):
                self.price_history = {k: v[-480:] for k, v in raw.items() if isinstance(v, list)}
        except Exception:
            pass

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        keep = {c.get('address') for c in self.feed[:50]}
        keep |= {p.get('address') for p in self.positions}
        keep |= {t.get('address') for t in self.history[:100]}
        price_history = {k: v[-480:] for k, v in self.price_history.items() if k in keep}
        STATE_PATH.write_text(json.dumps({
            'positions': self.positions[-20:],
            'history': self.history[-300:],
            'events': self.events[-100:],
            'demo_starting_balance_usd': self.demo_starting_balance_usd,
            'demo_balance_usd': self.demo_balance_usd,
            'demo_started_at': self.demo_started_at,
            'demo_session_id': self.demo_session_id,
            'trade_seq': self.trade_seq,
            'price_history': price_history,
        }, ensure_ascii=False))

    def event(self, text: str) -> None:
        self.events.insert(0, {'ts': now_ms(), 'text': text[:500]})
        self.events = self.events[:100]
        self.message = text[:500]

    def reserved_usd(self) -> float:
        return sum(num(p.get('notional_usd')) for p in self.positions)

    def unrealized_pnl_usd(self) -> float:
        return sum(num(p.get('pnl_usd')) for p in self.positions)

    def available_balance_usd(self) -> float:
        return max(0.0, self.demo_balance_usd - self.reserved_usd())

    def equity_usd(self) -> float:
        return self.demo_balance_usd + self.unrealized_pnl_usd()

    def realized_today(self) -> float:
        day = time.strftime('%Y-%m-%d', time.gmtime())
        total = 0.0
        for trade in self.history:
            stamp = int(trade.get('closed_at', 0)) / 1000
            if stamp and time.strftime('%Y-%m-%d', time.gmtime(stamp)) == day:
                total += num(trade.get('pnl_usd'))
        return total

    def live_flow(self, address: str, seconds: int = 30) -> dict[str, Any]:
        tape = read_live_tape()
        cutoff = now_ms() - seconds * 1000
        rows = [e for e in tape.get('events', []) if e.get('address') == address and int(e.get('ts', 0)) >= cutoff]
        buys = [e for e in rows if e.get('direction') == 'BUY']
        sells = [e for e in rows if e.get('direction') == 'SELL']
        buy_usd = sum(num(e.get('usd_amount')) for e in buys)
        sell_usd = sum(num(e.get('usd_amount')) for e in sells)
        buy_counts: dict[str, int] = {}
        sell_counts: dict[str, int] = {}
        for e in buys:
            wallet = e.get('wallet')
            if wallet:
                buy_counts[wallet] = buy_counts.get(wallet, 0) + 1
        for e in sells:
            wallet = e.get('wallet')
            if wallet:
                sell_counts[wallet] = sell_counts.get(wallet, 0) + 1
        buyer_wallets = set(buy_counts)
        seller_wallets = set(sell_counts)
        repeat_buy_wallets = sum(1 for count in buy_counts.values() if count >= 2)
        whale_buy_usd = sum(num(e.get('usd_amount')) for e in buys if num(e.get('usd_amount')) >= 750)
        whale_sell_usd = sum(num(e.get('usd_amount')) for e in sells if num(e.get('usd_amount')) >= 750)
        return {
            'seconds': seconds, 'trades': len(rows), 'buys': len(buys), 'sells': len(sells),
            'buy_usd': round(buy_usd, 2), 'sell_usd': round(sell_usd, 2),
            'net_buy_usd': round(buy_usd - sell_usd, 2),
            'buy_sell_usd_ratio': round(buy_usd / max(sell_usd, 1.0), 2),
            'unique_wallets': len(buyer_wallets | seller_wallets),
            'buyer_wallets': len(buyer_wallets), 'seller_wallets': len(seller_wallets),
            'wallet_buy_sell_ratio': round(len(buyer_wallets) / max(len(seller_wallets), 1), 2),
            'repeat_buy_wallets': repeat_buy_wallets,
            'whale_buy_usd': round(whale_buy_usd, 2), 'whale_sell_usd': round(whale_sell_usd, 2),
            'max_buy_usd': round(max([num(e.get('usd_amount')) for e in buys] or [0]), 2),
            'max_sell_usd': round(max([num(e.get('usd_amount')) for e in sells] or [0]), 2),
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            wins = sum(1 for t in self.history if num(t.get('pnl_pct')) > 0)
            closed = len(self.history)
            closed_total = max(self.trade_seq - len(self.positions), closed)
            tape = read_live_tape()
            return {
                'running': self.running,
                'status': self.status,
                'message': self.message,
                'last_scan_at': self.last_scan_at,
                'scan_count': self.scan_count,
                'feed': self.feed,
                'positions': self.positions,
                'history': self.history[:100],
                'events': self.events[:30],
                'source_status': self.source_status,
                'live_tape': tape.get('events', [])[:100],
                'live_tape_status': {k: tape.get(k) for k in ('status','tracked_pairs','updated_at','source','error')},
                'strategy_lab': read_strategy_lab(),
                'stats': {
                    'feed_count': len(self.feed),
                    'open_positions': len(self.positions),
                    'closed_trades': closed_total,
                    'wins': wins,
                    'win_rate': round((wins / closed) * 100, 1) if closed else 0,
                    'realized_today_usd': round(self.realized_today(), 2),
                    'demo_starting_balance_usd': round(self.demo_starting_balance_usd, 2),
                    'demo_balance_usd': round(self.demo_balance_usd, 2),
                    'demo_equity_usd': round(self.equity_usd(), 2),
                    'demo_available_usd': round(self.available_balance_usd(), 2),
                    'demo_reserved_usd': round(self.reserved_usd(), 2),
                    'unrealized_pnl_usd': round(self.unrealized_pnl_usd(), 2),
                    'realized_total_usd': round(self.demo_balance_usd - self.demo_starting_balance_usd, 2),
                    'return_pct': round(((self.equity_usd() - self.demo_starting_balance_usd) / max(self.demo_starting_balance_usd, 1)) * 100, 3),
                    'demo_started_at': self.demo_started_at,
                    'demo_session_id': self.demo_session_id,
                },
                'config': {
                    'scan_seconds': SCAN_SECONDS,
                    'position_scan_seconds': POSITION_SCAN_SECONDS,
                    'entry_score': ENTRY_SCORE,
                    'max_positions': MAX_POSITIONS,
                    'stop_loss_pct': STOP_LOSS_PCT,
                    'take_profit_pct': TAKE_PROFIT_PCT,
                    'trailing_pct': TRAILING_PCT,
                    'max_hold_minutes': MAX_HOLD_MINUTES,
                    'min_liquidity_usd': MIN_LIQUIDITY_USD,
                    'trade_notional_usd': TRADE_NOTIONAL_USD,
                    'max_daily_loss_usd': MAX_DAILY_LOSS_USD,
                    'starting_balance_usd': STARTING_BALANCE_USD,
                },
            }
    def token_snapshot(self, address: str) -> dict[str, Any] | None:
        with self.lock:
            coin = next((c for c in self.feed if c.get('address') == address), None)
            if not coin:
                trade = next((t for t in self.history if t.get('address') == address), None)
                if trade:
                    coin = trade.get('coin_snapshot')
            if not coin:
                return None
            return {
                'coin': coin,
                'history': self.price_history.get(address, [])[-480:],
                'position': next((p for p in self.positions if p.get('address') == address), None),
                'trades': [t for t in self.history if t.get('address') == address][:20],
                'live_tape': [e for e in read_live_tape().get('events', []) if e.get('address') == address][:80],
                'flow': self.live_flow(address, 60),
            }


def append_audit(event: str, payload: dict[str, Any]) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {'ts': now_ms(), 'event': event, 'session_id': STATE.demo_session_id, **payload}
        with AUDIT_PATH.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass


def adaptive_profile(setup: dict[str, Any]) -> dict[str, Any]:
    def age_band(v: float) -> int:
        return 0 if v < 10 else 1 if v < 60 else 2 if v < 240 else 3
    similar = []
    for t in STATE.history[:LEARNING_WINDOW]:
        snap = t.get('coin_snapshot') or {}
        score = num(t.get('score') or snap.get('score'))
        liq = num(t.get('entry_liquidity_usd') or snap.get('liquidityUsd'))
        m5 = num(t.get('entry_change_m5') if t.get('entry_change_m5') is not None else (snap.get('priceChange') or {}).get('m5'))
        age = num(snap.get('ageMinutes'), 999999)
        tx = (snap.get('txns') or {}).get('m5') or {}
        bs = num(t.get('entry_buy_sell_ratio'), num(tx.get('buys')) / max(num(tx.get('sells')), 1.0))
        mc = num(t.get('entry_market_cap') or snap.get('marketCap') or snap.get('fdv'))
        lmc = num(t.get('entry_liquidity_mc_ratio'), liq / max(mc, 1.0))
        sid = None
        if score >= 95 and liq >= 20000 and 3 <= m5 <= 25 and bs >= .9 and lmc >= .10 and 2 <= age <= 480:
            sid = 'PRECISION_V2'
        if sid != setup['strategy_id']:
            continue
        if abs(score - setup['score']) > 10 or abs(m5 - setup['change_m5']) > 12:
            continue
        ratio = liq / max(setup['liquidity'], 1.0)
        if not .45 <= ratio <= 2.2 or age_band(age) != age_band(setup['age']):
            continue
        similar.append(t)
    n = len(similar)
    wins = sum(1 for t in similar if num(t.get('pnl_usd')) > 0)
    pnl = sum(num(t.get('pnl_usd')) for t in similar)
    gw = sum(max(0.0, num(t.get('pnl_usd'))) for t in similar)
    gl = -sum(min(0.0, num(t.get('pnl_usd'))) for t in similar)
    wr = wins / n * 100 if n else 0.0
    pf = gw / gl if gl > 0 else (99.0 if n >= 5 and gw > 0 else (1.0 if gw > 0 else 0.0))
    recent_losses = sum(1 for t in similar[:3] if num(t.get('pnl_usd')) <= 0)
    blocked = (n >= 5 and wr < 55 and pf < 1.2 and pnl < 0) or (n >= 3 and recent_losses == 3)
    bonus = 0.0  # learning may veto entries, never relax PRECISION_V2
    return {'sample': n, 'wins': wins, 'win_rate': round(wr, 1),
            'pnl_usd': round(pnl, 2), 'profit_factor': round(pf, 2),
            'recent_losses': recent_losses, 'blocked': blocked, 'bonus': round(bonus, 2)}

STATE = State()


def discover() -> tuple[list[str], dict[str, dict[str, Any]]]:
    metadata: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    sources = [
        ('latest', '/token-profiles/latest/v1'),
        ('boosted', '/token-boosts/top/v1'),
        ('boosted-latest', '/token-boosts/latest/v1'),
    ]
    for source_name, path in sources:
        try:
            rows = api(path)
        except Exception as exc:
            STATE.event(f'{source_name} discovery warning: {exc}')
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row.get('chainId') != 'solana':
                continue
            address = row.get('tokenAddress')
            if not address:
                continue
            if address not in metadata:
                metadata[address] = {
                    'sources': [], 'icon': row.get('icon') or '',
                    'header': row.get('header') or '',
                    'description': row.get('description') or '',
                    'links': row.get('links') or [], 'boost_amount': 0,
                }
                order.append(address)
            info = metadata[address]
            if source_name not in info['sources']:
                info['sources'].append(source_name)
            info['icon'] = info['icon'] or row.get('icon') or ''
            info['header'] = info['header'] or row.get('header') or ''
            info['description'] = info['description'] or row.get('description') or ''
            info['links'] = info['links'] or row.get('links') or []
            info['boost_amount'] = max(num(info['boost_amount']), num(row.get('amount')), num(row.get('totalAmount')))
    return order[:90], metadata


def fetch_pairs(addresses: list[str]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]
        if not batch:
            continue
        try:
            rows = api('/tokens/v1/solana/' + ','.join(batch))
            if isinstance(rows, list):
                pairs.extend(rows)
        except Exception as exc:
            STATE.event(f'Market batch warning: {exc}')
    return pairs


def best_pairs(pairs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if pair.get('chainId') != 'solana':
            continue
        address = (pair.get('baseToken') or {}).get('address')
        if not address:
            continue
        liquidity = num((pair.get('liquidity') or {}).get('usd'))
        old = best.get(address)
        old_liquidity = num((old.get('liquidity') or {}).get('usd')) if old else -1
        if old is None or liquidity > old_liquidity:
            best[address] = pair
    return best


def score_pair(pair: dict[str, Any], meta: dict[str, Any]):
    liq = num((pair.get('liquidity') or {}).get('usd'))
    volume = pair.get('volume') or {}
    vol_h1 = num(volume.get('h1'))
    mc = num(pair.get('marketCap')) or num(pair.get('fdv'))
    changes = pair.get('priceChange') or {}
    change_m5 = num(changes.get('m5'))
    change_h1 = num(changes.get('h1'))
    tx5 = (pair.get('txns') or {}).get('m5') or {}
    buys, sells = num(tx5.get('buys')), num(tx5.get('sells'))
    tx_count = buys + sells
    created = int(pair.get('pairCreatedAt') or 0)
    age = max(0.0, (now_ms() - created) / 60000) if created else 999999
    buy_sell = buys / max(sells, 1)
    vol_liq = vol_h1 / max(liq, 1)
    liq_mc = liq / max(mc, 1) if mc else 0
    score, signals = 36.0, []

    if liq >= 50000:
        score += 18; signals.append(signal('positive', 'Силна ликвидност', f'${liq:,.0f}'))
    elif liq >= 20000:
        score += 13; signals.append(signal('positive', 'Добра ликвидност', f'${liq:,.0f}'))
    elif liq >= 10000:
        score += 7; signals.append(signal('neutral', 'Приемлива ликвидност', f'${liq:,.0f}'))
    elif liq < 5000:
        score -= 22; signals.append(signal('risk', 'Много ниска ликвидност', f'${liq:,.0f}'))
    else:
        score -= 7; signals.append(signal('risk', 'Тънка ликвидност', f'${liq:,.0f}'))

    if vol_h1 >= 50000:
        score += 12; signals.append(signal('positive', 'Силен 1h volume', f'${vol_h1:,.0f}'))
    elif vol_h1 >= 10000:
        score += 7; signals.append(signal('positive', 'Активен 1h volume', f'${vol_h1:,.0f}'))
    elif vol_h1 < 1000:
        score -= 8; signals.append(signal('risk', 'Слаб volume', f'${vol_h1:,.0f}'))

    if tx_count >= 120:
        score += 10; signals.append(signal('positive', 'Много активни сделки', f'{int(tx_count)} tx / 5m'))
    elif tx_count >= 35:
        score += 6; signals.append(signal('positive', 'Добра активност', f'{int(tx_count)} tx / 5m'))
    elif tx_count < 8:
        score -= 6; signals.append(signal('risk', 'Малко сделки', f'{int(tx_count)} tx / 5m'))

    if 1.05 <= buy_sell <= 2.8:
        score += 8; signals.append(signal('positive', 'Купувачите водят', f'Buy/Sell {buy_sell:.2f}x'))
    elif buy_sell > 5:
        score -= 6; signals.append(signal('risk', 'Неестествен buy imbalance', f'{buy_sell:.2f}x'))
    elif buy_sell < 0.65:
        score -= 8; signals.append(signal('risk', 'Продавачите доминират', f'{buy_sell:.2f}x'))

    if 1.5 <= change_m5 <= 18:
        score += 8; signals.append(signal('positive', 'Здрав кратък momentum', f'{change_m5:+.1f}% / 5m'))
    elif 18 < change_m5 <= 45:
        score += 3; signals.append(signal('neutral', 'Бърз pump', f'{change_m5:+.1f}% / 5m'))
    elif change_m5 > 60:
        score -= 12; signals.append(signal('risk', 'Вертикален pump', f'{change_m5:+.1f}% / 5m'))
    elif change_m5 < -20:
        score -= 12; signals.append(signal('risk', 'Силен спад', f'{change_m5:+.1f}% / 5m'))

    if 3 <= age <= 360:
        score += 8; signals.append(signal('positive', 'Ранен етап', f'{age:.0f} мин.'))
    elif age < 2:
        score -= 7; signals.append(signal('risk', 'Твърде нов pair', f'{age:.1f} мин.'))
    elif age > 4320:
        score -= 4
    if mc > 0:
        if liq_mc >= 0.15:
            score += 9; signals.append(signal('positive', 'Добро liquidity/MC', f'{liq_mc * 100:.1f}%'))
        elif liq_mc < 0.03:
            score -= 10; signals.append(signal('risk', 'Слаб liquidity/MC', f'{liq_mc * 100:.1f}%'))

    if 0.10 <= vol_liq <= 4.0:
        score += 6
    elif vol_liq > 7:
        score -= 12; signals.append(signal('risk', 'Volume/liquidity extreme', f'{vol_liq:.1f}x'))

    if any('boost' in source for source in meta.get('sources', [])):
        score += 4; signals.append(signal('neutral', 'Boosted discovery', 'Повишена market видимост'))
    if abs(change_h1) > 250:
        score -= 8; signals.append(signal('risk', 'Екстремен 1h move', f'{change_h1:+.0f}%'))

    score = round(clamp(score), 1)
    risk = round(100 - score, 1)
    posture = 'SETUP' if score >= ENTRY_SCORE else 'WATCH' if score >= 60 else 'WAIT' if score >= 45 else 'SKIP'
    return score, risk, posture, signals[:8]


def make_coin(address: str, pair: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    score, risk, posture, signals = score_pair(pair, meta)
    base, info = pair.get('baseToken') or {}, pair.get('info') or {}
    volume, changes, txns = pair.get('volume') or {}, pair.get('priceChange') or {}, pair.get('txns') or {}
    created = int(pair.get('pairCreatedAt') or 0)
    return {
        'address': address,
        'pairAddress': pair.get('pairAddress') or '',
        'dexId': pair.get('dexId') or '',
        'dexUrl': pair.get('url') or '',
        'name': base.get('name') or 'Unknown token',
        'symbol': base.get('symbol') or 'TOKEN',
        'imageUrl': info.get('imageUrl') or meta.get('icon') or '',
        'headerUrl': info.get('header') or meta.get('header') or '',
        'description': meta.get('description') or '',
        'priceUsd': num(pair.get('priceUsd')),
        'priceNative': num(pair.get('priceNative')),
        'marketCap': num(pair.get('marketCap')),
        'fdv': num(pair.get('fdv')),
        'liquidityUsd': num((pair.get('liquidity') or {}).get('usd')),
        'volume': {k: num(volume.get(k)) for k in ('m5', 'h1', 'h6', 'h24')},
        'priceChange': {k: num(changes.get(k)) for k in ('m5', 'h1', 'h6', 'h24')},
        'txns': {
            k: {'buys': int(num((txns.get(k) or {}).get('buys'))), 'sells': int(num((txns.get(k) or {}).get('sells')))}
            for k in ('m5', 'h1', 'h6', 'h24')
        },
        'pairCreatedAt': created,
        'ageMinutes': round(max(0, (now_ms() - created) / 60000), 1) if created else None,
        'sources': meta.get('sources', []),
        'boostAmount': num(meta.get('boost_amount')),
        'websites': (info.get('websites') or [])[:3],
        'socials': (info.get('socials') or [])[:5],
        'score': score, 'riskScore': risk, 'posture': posture,
        'signals': signals, 'updatedAt': now_ms(),
    }

class Monitor:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.scan_lock = threading.Lock()
        self.position_lock = threading.Lock()
        self.position_poll_lock = threading.Lock()

    def update_price_history(self, feed: list[dict[str, Any]]) -> None:
        stamp = now_ms()
        for coin in feed[:50]:
            price = num(coin.get('priceUsd'))
            if price <= 0:
                continue
            address = coin['address']
            points = STATE.price_history.setdefault(address, [])
            points.append({
                'ts': stamp,
                'price': price,
                'liquidity': round(num(coin.get('liquidityUsd')), 2),
                'volumeH1': round(num((coin.get('volume') or {}).get('h1')), 2),
                'score': num(coin.get('score')),
            })
            STATE.price_history[address] = points[-480:]

    def market_context(self, coin: dict[str, Any], position: dict[str, Any] | None = None) -> dict[str, Any]:
        address = coin.get('address') or (position or {}).get('address')
        fast = STATE.live_flow(address, 30)
        slow = STATE.live_flow(address, 300)
        changes = coin.get('priceChange') or {}
        tx_m5 = (coin.get('txns') or {}).get('m5') or {}
        m5 = num(changes.get('m5'))
        h1 = num(changes.get('h1'))
        market_ratio = num(tx_m5.get('buys')) / max(num(tx_m5.get('sells')), 1.0)
        liquidity = num(coin.get('liquidityUsd'))
        entry_liquidity = num((position or {}).get('entry_liquidity_usd'), liquidity)
        liquidity_ratio = liquidity / max(entry_liquidity, 1.0)
        score = 50.0

        fast_ratio = num(fast.get('buy_sell_usd_ratio'))
        slow_ratio = num(slow.get('buy_sell_usd_ratio'))
        if fast_ratio >= 3.0:
            score += 18
        elif fast_ratio >= 2.0:
            score += 12
        elif fast_ratio >= 1.4:
            score += 6
        elif fast_ratio < 0.8:
            score -= 20
        elif fast_ratio < 1.0:
            score -= 10

        if slow_ratio >= 2.0:
            score += 12
        elif slow_ratio >= 1.4:
            score += 7
        elif slow_ratio < 0.8:
            score -= 15
        elif slow_ratio < 1.0:
            score -= 7

        if num(slow.get('unique_wallets')) >= 10:
            score += 6
        elif num(slow.get('unique_wallets')) >= 5:
            score += 3
        elif num(slow.get('unique_wallets')) <= 2:
            score -= 5

        if num(slow.get('repeat_buy_wallets')) >= 3:
            score += 6
        elif num(slow.get('repeat_buy_wallets')) >= 1:
            score += 3

        whale_buy = num(slow.get('whale_buy_usd'))
        whale_sell = num(slow.get('whale_sell_usd'))
        if whale_buy > 0 and whale_buy >= whale_sell * 1.3:
            score += 6
        elif whale_sell > 0 and whale_sell >= max(whale_buy * 1.3, 750.0):
            score -= 8

        if 0 <= m5 <= 10:
            score += 8
        elif -2 <= m5 < 0:
            score += 2
        elif m5 > 20:
            score -= 8
        elif m5 < -5:
            score -= 15

        if 0 <= h1 <= 120:
            score += 4
        elif h1 < -15:
            score -= 8
        elif h1 > 250:
            score -= 5

        if market_ratio >= 1.4:
            score += 8
        elif market_ratio >= 1.1:
            score += 4
        elif market_ratio < 0.8:
            score -= 8

        if liquidity_ratio >= 0.95:
            score += 3
        elif liquidity_ratio < 0.80:
            score -= 15
        elif liquidity_ratio < 0.90:
            score -= 7

        neo_score = num(coin.get('score'))
        if neo_score >= 95:
            score += 4
        elif neo_score < 85:
            score -= 4

        conviction = round(clamp(score), 1)
        if conviction >= 85:
            mode, max_hold, target, trail_arm, trail = 'RUNNER', 60.0, None, 15.0, 7.0
        elif conviction >= 72:
            mode, max_hold, target, trail_arm, trail = 'STRONG', 30.0, None, 12.0, 6.0
        elif conviction >= 58:
            mode, max_hold, target, trail_arm, trail = 'NORMAL', 15.0, 20.0, 9.0, 5.0
        elif conviction >= 45:
            mode, max_hold, target, trail_arm, trail = 'CAUTIOUS', 8.0, 14.0, 7.0, 4.0
        else:
            mode, max_hold, target, trail_arm, trail = 'WEAK', 4.0, 8.0, 5.0, 3.0

        return {
            'conviction': conviction, 'mode': mode, 'max_hold_minutes': max_hold,
            'target_pct': target, 'trail_arm_pct': trail_arm, 'trail_pct': trail,
            'm5': round(m5, 3), 'h1': round(h1, 3), 'market_buy_sell_ratio': round(market_ratio, 3),
            'liquidity_ratio_vs_entry': round(liquidity_ratio, 3),
            'fast_flow': fast, 'slow_flow': slow,
            'holder_proxy': {
                'unique_wallets_5m': slow.get('unique_wallets', 0),
                'repeat_buy_wallets_5m': slow.get('repeat_buy_wallets', 0),
                'wallet_buy_sell_ratio_5m': slow.get('wallet_buy_sell_ratio', 0),
                'whale_buy_usd_5m': slow.get('whale_buy_usd', 0),
                'whale_sell_usd_5m': slow.get('whale_sell_usd', 0),
            },
        }

    def update_positions(self, by_address: dict[str, dict[str, Any]]) -> None:
        next_positions = []
        for position in STATE.positions:
            coin = by_address.get(position.get('address'))
            if not coin:
                next_positions.append(position)
                continue
            price, entry = num(coin.get('priceUsd')), num(position.get('entry_price'))
            if price <= 0 or entry <= 0:
                next_positions.append(position)
                continue
            peak = max(num(position.get('peak_price'), entry), price)
            pnl_pct = round(((price - entry) / entry) * 100, 6)
            quantity = num(position.get('quantity')) or (num(position.get('notional_usd'), TRADE_NOTIONAL_USD) / entry)
            pnl_usd = quantity * (price - entry)
            hold_min = (now_ms() - int(position.get('opened_at', now_ms()))) / 60000
            context = self.market_context(coin, position)
            conviction = num(context.get('conviction'))
            target_pct = context.get('target_pct')
            max_hold = num(context.get('max_hold_minutes'), MAX_HOLD_MINUTES)
            trail_arm_pct = num(context.get('trail_arm_pct'), 8.0)
            trail_pct = num(context.get('trail_pct'), TRAILING_PCT)
            fast_flow = context.get('fast_flow') or {}
            slow_flow = context.get('slow_flow') or {}
            trailing_armed = peak >= entry * (1 + trail_arm_pct / 100)
            trailing_floor = peak * (1 - trail_pct / 100)
            exit_reason = None

            # The hard stop is always respected. Everything else can only exit earlier
            # or let a strong winner run longer while conviction remains high.
            if pnl_pct <= -STOP_LOSS_PCT:
                exit_reason = 'STOP_LOSS'
            elif conviction < 35 and pnl_pct < 0:
                exit_reason = 'CONVICTION_EXIT'
            elif (
                num(fast_flow.get('trades')) >= 4
                and num(fast_flow.get('sells')) >= 3
                and num(fast_flow.get('sell_usd')) >= max(200.0, num(fast_flow.get('buy_usd')) * 2.0)
                and pnl_pct < 3.0
            ):
                exit_reason = 'ORDERFLOW_EXIT'
            elif target_pct is not None and pnl_pct >= num(target_pct):
                exit_reason = f'ADAPTIVE_TP_{num(target_pct):.0f}'
            elif peak >= entry * 1.10 and conviction < 50 and pnl_pct > 2.0:
                exit_reason = 'CONVICTION_PROFIT_LOCK'
            elif trailing_armed and price <= trailing_floor:
                exit_reason = 'ADAPTIVE_TRAILING'
            elif hold_min >= max_hold and conviction < 72:
                exit_reason = 'ADAPTIVE_MAX_HOLD'
            elif hold_min >= 120:
                exit_reason = 'ABSOLUTE_MAX_HOLD'
            updated = {
                **position, 'current_price': price, 'peak_price': peak,
                'pnl_pct': round(pnl_pct, 3), 'pnl_usd': round(pnl_usd, 3),
                'conviction': conviction, 'hold_mode': context.get('mode'),
                'adaptive_target_pct': target_pct, 'adaptive_max_hold_minutes': max_hold,
                'adaptive_trail_arm_pct': trail_arm_pct, 'adaptive_trail_pct': trail_pct,
                'market_context': context,
                'updated_at': now_ms(), 'current_score': coin.get('score'),
            }
            if exit_reason:
                balance_before = STATE.demo_balance_usd
                STATE.demo_balance_usd = round(STATE.demo_balance_usd + pnl_usd, 8)
                closed = {
                    **updated, 'closed_at': now_ms(), 'exit_price': price, 'exit_reason': exit_reason,
                    'quantity': quantity, 'balance_before': round(balance_before, 8),
                    'balance_after': round(STATE.demo_balance_usd, 8),
                    'exit_score': coin.get('score'), 'exit_liquidity_usd': coin.get('liquidityUsd'),
                    'exit_volume_h1': (coin.get('volume') or {}).get('h1'),
                    'exit_market_cap': coin.get('marketCap') or coin.get('fdv'),
                    'exit_change_m5': (coin.get('priceChange') or {}).get('m5'),
                    'exit_scan_count': STATE.scan_count,
                }
                STATE.history.insert(0, closed)
                STATE.history = STATE.history[:300]
                append_audit('EXIT', closed)
                STATE.event(f"PAPER EXIT #{closed.get('trade_no')} ${closed['symbol']} {exit_reason} · {pnl_pct:+.2f}% · balance ${STATE.demo_balance_usd:.2f}")
            else:
                next_positions.append(updated)
        STATE.positions = next_positions

    def fast_position_check(self) -> None:
        if not STATE.running or not self.position_lock.acquire(blocking=False):
            return
        try:
            with STATE.lock:
                positions = list(STATE.positions)
            addresses = [p.get('address') for p in positions if p.get('address')]
            if not addresses:
                return
            pairs = fetch_pairs(addresses)
            chosen = best_pairs(pairs)
            by_address = {}
            for position in positions:
                address = position.get('address')
                pair = chosen.get(address)
                if not pair:
                    continue
                snap = position.get('coin_snapshot') or {}
                meta = {'sources': ['position-guard'], 'icon': snap.get('imageUrl') or '',
                        'header': '', 'description': '', 'links': [], 'boost_amount': 0}
                by_address[address] = make_coin(address, pair, meta)
            if by_address:
                with STATE.lock:
                    self.update_positions(by_address)
                    STATE.save()
        except Exception as exc:
            with STATE.lock:
                STATE.event(f'Fast position guard warning: {exc}')
        finally:
            self.position_lock.release()

    def run_position_guard(self) -> None:
        while not self.stop_event.is_set():
            if STATE.positions:
                self.fast_position_check()
            self.stop_event.wait(POSITION_SCAN_SECONDS)

    def maybe_open(self, feed: list[dict[str, Any]]) -> None:
        if len(STATE.positions) >= MAX_POSITIONS:
            return
        if STATE.available_balance_usd() < min(TRADE_NOTIONAL_USD, 10.0):
            return
        open_addresses = {p.get('address') for p in STATE.positions}
        now = now_ms()
        cutoff = now - 20 * 60 * 1000
        recent = {t.get('address') for t in STATE.history if int(t.get('closed_at', 0)) >= cutoff}
        for coin in feed:
            if len(STATE.positions) >= MAX_POSITIONS:
                break
            address = coin.get('address')
            if not address or address in open_addresses or address in recent:
                continue
            score = num(coin.get('score'))
            liquidity = num(coin.get('liquidityUsd'))
            age = num(coin.get('ageMinutes'), 999999)
            change_m5 = num((coin.get('priceChange') or {}).get('m5'))
            tx_m5 = (coin.get('txns') or {}).get('m5') or {}
            buys_m5 = num(tx_m5.get('buys'))
            sells_m5 = num(tx_m5.get('sells'))
            buy_sell_ratio = buys_m5 / max(sells_m5, 1.0)
            market_cap = num(coin.get('marketCap') or coin.get('fdv'))
            liquidity_mc_ratio = liquidity / max(market_cap, 1.0)

            flow = STATE.live_flow(address, 60)
            order_flow_core = (
                score >= 85 and liquidity >= 15000 and -5 <= change_m5 <= 25
                and flow['trades'] >= 3 and flow['buy_sell_usd_ratio'] >= 1.3
                and flow['unique_wallets'] >= 1
                and flow['max_sell_usd'] < max(750.0, flow['buy_usd'] * 0.8)
            )
            if not order_flow_core:
                continue
            context = self.market_context(coin)
            if num(context.get('conviction')) < 75:
                continue
            strategy_id = 'ORDER_FLOW_ADAPTIVE'
            learning = {'sample': 0, 'win_rate': 0, 'profit_factor': 0, 'recent_losses': 0, 'bonus': 0, 'blocked': False}
            recovery = False
            price = num(coin.get('priceUsd'))
            if price <= 0:
                continue
            positive = [s['title'] for s in coin.get('signals', []) if s.get('kind') == 'positive'][:4]
            risks = [s['title'] for s in coin.get('signals', []) if s.get('kind') == 'risk'][:4]
            notional = min(TRADE_NOTIONAL_USD, STATE.available_balance_usd())
            if notional < 10:
                continue
            quantity = notional / price
            available_before = STATE.available_balance_usd()
            STATE.trade_seq += 1
            position = {
                'id': f'{address}:{now_ms()}', 'address': address,
                'pairAddress': coin.get('pairAddress'), 'name': coin.get('name'),
                'symbol': coin.get('symbol'), 'imageUrl': coin.get('imageUrl'),
                'entry_price': price, 'current_price': price, 'peak_price': price,
                'trade_no': STATE.trade_seq, 'session_id': STATE.demo_session_id, 'strategy_id': strategy_id,
                'learning_mode': 'ADAPTIVE_CONTEXT_HOLD', 'entry_flow': flow,
                'entry_context': context, 'entry_conviction': context.get('conviction'),
                'entry_hold_mode': context.get('mode'), 'learning_sample': learning['sample'],
                'learning_win_rate': learning['win_rate'], 'learning_profit_factor': learning['profit_factor'],
                'learning_recent_losses': learning['recent_losses'], 'learning_bonus': learning['bonus'],
                'notional_usd': round(notional, 8), 'quantity': quantity, 'score': coin.get('score'),
                'current_score': coin.get('score'), 'opened_at': now_ms(),
                'updated_at': now_ms(), 'pnl_pct': 0, 'pnl_usd': 0,
                'why_entry': positive, 'risks_at_entry': risks,
                'balance_at_entry': round(STATE.demo_balance_usd, 8),
                'available_before_entry': round(available_before, 8),
                'available_after_entry': round(max(0.0, available_before - notional), 8),
                'entry_liquidity_usd': coin.get('liquidityUsd'),
                'entry_volume_h1': (coin.get('volume') or {}).get('h1'),
                'entry_market_cap': coin.get('marketCap') or coin.get('fdv'),
                'entry_change_m5': (coin.get('priceChange') or {}).get('m5'),
                'entry_buy_sell_ratio': round(buy_sell_ratio, 4),
                'entry_liquidity_mc_ratio': round(liquidity_mc_ratio, 4),
                'entry_scan_count': STATE.scan_count, 'dex_url': coin.get('dexUrl'),
                'coin_snapshot': coin,
            }
            STATE.positions.append(position)
            append_audit('ENTRY', position)
            open_addresses.add(address)
            STATE.event(f"PAPER ENTRY #{position['trade_no']} ${coin.get('symbol')} @ ${price:.10g} · ${notional:.2f} · {strategy_id} · learn {learning['sample']} / WR {learning['win_rate']:.0f}% · NEO {coin.get('score'):.0f}/100")

    def scan_once(self) -> None:
        if not STATE.running or not self.scan_lock.acquire(blocking=False):
            return
        try:
            addresses, metadata = discover()
            for position in STATE.positions:
                address = position.get('address')
                if address and address not in addresses:
                    addresses.append(address)
                    metadata[address] = metadata.get(address) or {
                        'sources': ['open-position'], 'icon': position.get('imageUrl') or '',
                        'header': '', 'description': '', 'links': [], 'boost_amount': 0,
                    }
            if not addresses:
                raise RuntimeError('No Solana tokens returned by discovery sources.')
            pairs = fetch_pairs(addresses)
            chosen = best_pairs(pairs)
            feed = []
            for address in addresses:
                pair = chosen.get(address)
                if not pair:
                    continue
                coin = make_coin(address, pair, metadata.get(address, {}))
                if coin['priceUsd'] > 0:
                    feed.append(coin)
            feed.sort(key=lambda c: (num(c.get('score')), num((c.get('volume') or {}).get('h1'))), reverse=True)
            feed = feed[:MAX_FEED]
            by_address = {c['address']: c for c in feed}
            with STATE.lock:
                STATE.feed = feed
                STATE.last_scan_at = now_ms()
                STATE.scan_count += 1
                STATE.status = 'monitoring'
                STATE.source_status = {'dexscreener': 'online'}
                self.update_price_history(feed)
                self.update_positions(by_address)
                self.maybe_open(feed)
                setups = sum(1 for c in feed if c.get('posture') == 'SETUP')
                STATE.message = f'Live market scan · {len(feed)} coins · {setups} SETUP candidates · {len(STATE.positions)} paper positions.'
                STATE.save()
        except Exception as exc:
            with STATE.lock:
                STATE.status = 'error'
                STATE.source_status = {'dexscreener': 'error'}
                STATE.event(f'Market monitor error: {exc}')
                STATE.save()
        finally:
            self.scan_lock.release()

    def run(self) -> None:
        with STATE.lock:
            STATE.status = 'starting'
            STATE.event('NEO public market monitor started.')
        while not self.stop_event.is_set():
            self.scan_once()
            self.stop_event.wait(SCAN_SECONDS)

    def stop(self) -> None:
        self.stop_event.set()


MONITOR = Monitor()

class ApiHandler(BaseHTTPRequestHandler):
    server_version = 'NEOMarketMonitor/2.0'

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            self.send_json({'ok': True, 'status': STATE.status, 'running': STATE.running, 'feed_count': len(STATE.feed)})
            return
        if parsed.path == '/state':
            self.send_json(STATE.snapshot())
            return
        if parsed.path == '/token':
            address = (parse_qs(parsed.query).get('address') or [''])[0]
            result = STATE.token_snapshot(address)
            self.send_json(result if result else {'error': 'token_not_found'}, 200 if result else 404)
            return
        self.send_json({'error': 'not_found'}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == '/control/start':
            with STATE.lock:
                STATE.running = True
                STATE.status = 'starting'
                STATE.event('Monitoring enabled.')
            threading.Thread(target=MONITOR.scan_once, daemon=True).start()
            self.send_json(STATE.snapshot())
            return
        if path == '/control/stop':
            with STATE.lock:
                STATE.running = False
                STATE.status = 'paused'
                STATE.event('Monitoring paused.')
            self.send_json(STATE.snapshot())
            return
        if path == '/control/rescan':
            threading.Thread(target=MONITOR.scan_once, daemon=True).start()
            self.send_json({'ok': True})
            return
        if path == '/control/reset':
            with STATE.lock:
                STATE.positions = []
                STATE.history = []
                STATE.events = []
                STATE.price_history = {}
                STATE.demo_starting_balance_usd = STARTING_BALANCE_USD
                STATE.demo_balance_usd = STARTING_BALANCE_USD
                STATE.demo_started_at = now_ms()
                STATE.demo_session_id = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
                STATE.trade_seq = 0
                append_audit('RESET', {'starting_balance_usd': STARTING_BALANCE_USD})
                STATE.event(f'New demo session started with ${STARTING_BALANCE_USD:.2f}.')
                STATE.save()
            self.send_json(STATE.snapshot())
            return
        self.send_json({'error': 'not_found'}, 404)


def main() -> None:
    thread = threading.Thread(target=MONITOR.run, name='neo-market-monitor', daemon=True)
    thread.start()
    guard = threading.Thread(target=MONITOR.run_position_guard, name='neo-position-guard', daemon=True)
    guard.start()
    server = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    print(f'NEO market monitor API listening on http://{HOST}:{PORT}', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        MONITOR.stop()
        server.shutdown()


if __name__ == '__main__':
    main()

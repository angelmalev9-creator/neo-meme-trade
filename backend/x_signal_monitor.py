#!/usr/bin/env python3
"""Budget-capped X signal collector for the PAPER Strategy Lab.

Uses xAI's server-side X Search. It never places orders. It writes a small
state file consumed by strategy_lab.py. A signal is accepted only when the
model returns a literal Solana-looking address that also appears verbatim in
its quoted post text; Strategy Lab still applies its normal feed, liquidity,
price-integrity, cost and cooldown checks before a PAPER entry.
"""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import requests

API_URL = os.getenv('XAI_API_URL', 'https://api.x.ai/v1/responses')
API_KEY = os.getenv('XAI_API_KEY', '').strip()
MODEL = os.getenv('NEO_X_SIGNAL_MODEL', 'grok-4.3').strip() or 'grok-4.3'
HANDLES = [h.strip().lstrip('@') for h in os.getenv('NEO_X_SIGNAL_HANDLES', 'elonmusk').split(',') if h.strip()][:20]
POLL_SECONDS = max(30.0, float(os.getenv('NEO_X_SIGNAL_POLL_SECONDS', '120')))
DAILY_BUDGET_USD = max(0.0, float(os.getenv('NEO_X_SIGNAL_DAILY_BUDGET_USD', '2.00')))
MAX_AGE_SECONDS = max(60, int(os.getenv('NEO_X_SIGNAL_MAX_AGE_SECONDS', '600')))
TIMEOUT = max(5.0, float(os.getenv('NEO_X_SIGNAL_REQUEST_TIMEOUT_SECONDS', '30')))
STATE_PATH = Path(os.getenv('NEO_X_SIGNAL_STATE_PATH', '/var/lib/neo-market/x_signal.json'))
POST_COST_USD = float(os.getenv('NEO_X_SIGNAL_POST_COST_USD', '0.005'))
USER_COST_USD = float(os.getenv('NEO_X_SIGNAL_USER_COST_USD', '0.010'))
INPUT_MTOKEN_USD = float(os.getenv('NEO_X_SIGNAL_INPUT_MTOKEN_USD', '1.25'))
OUTPUT_MTOKEN_USD = float(os.getenv('NEO_X_SIGNAL_OUTPUT_MTOKEN_USD', '2.50'))
ADDRESS = re.compile(r'(?<![1-9A-HJ-NP-Za-km-z])([1-9A-HJ-NP-Za-km-z]{32,44})(?![1-9A-HJ-NP-Za-km-z])')
SESSION = requests.Session()
SESSION.headers.update({'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json', 'User-Agent': 'NEO-X-Signal/1.0'})


def now_ms():
    return int(time.time() * 1000)


def utc_day():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def load_state():
    try:
        value = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def atomic_write(data):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(STATE_PATH)


def output_text(payload):
    chunks = []
    for item in payload.get('output') or []:
        if not isinstance(item, dict) or item.get('type') != 'message':
            continue
        for part in item.get('content') or []:
            if isinstance(part, dict) and part.get('type') in ('output_text', 'text') and part.get('text'):
                chunks.append(str(part['text']))
    return '\n'.join(chunks).strip()


def parse_json_text(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def usage_cost(usage):
    usage = usage or {}
    detail = usage.get('server_side_tool_usage_details') or {}
    posts = int(detail.get('x_posts_fetched') or 0)
    users = int(detail.get('x_users_fetched') or 0)
    input_tokens = int(usage.get('input_tokens') or usage.get('prompt_tokens') or 0)
    output_tokens = int(usage.get('output_tokens') or usage.get('completion_tokens') or 0)
    cost = posts * POST_COST_USD + users * USER_COST_USD
    cost += input_tokens / 1_000_000 * INPUT_MTOKEN_USD
    cost += output_tokens / 1_000_000 * OUTPUT_MTOKEN_USD
    return cost, posts, users, input_tokens, output_tokens


def normalize_signal(row, seen_at):
    if not isinstance(row, dict):
        return None
    handle = str(row.get('handle') or '').strip().lstrip('@')[:64]
    post_id = str(row.get('post_id') or '').strip()[:64]
    url = str(row.get('post_url') or '').strip()[:300]
    text = str(row.get('text') or row.get('text_excerpt') or '').strip()[:1200]
    if handle.lower() not in {h.lower() for h in HANDLES} or not post_id or not text:
        return None
    literal = set(ADDRESS.findall(text))
    requested = row.get('addresses') or []
    if isinstance(requested, str):
        requested = [requested]
    addresses = [a for a in requested if isinstance(a, str) and a in literal and ADDRESS.fullmatch(a)]
    addresses = list(dict.fromkeys(addresses))[:4]
    if not addresses:
        return None
    created_ms = 0
    raw_created = str(row.get('created_at') or '').strip()
    if raw_created:
        try:
            created = dt.datetime.fromisoformat(raw_created.replace('Z', '+00:00'))
            if created.tzinfo is None:
                created = created.replace(tzinfo=dt.timezone.utc)
            created_ms = int(created.timestamp() * 1000)
        except Exception:
            created_ms = 0
    # Reject clearly stale content. When the timestamp is unavailable, seen_at
    # is used so PAPER can still measure the collector delay honestly.
    basis = created_ms or seen_at
    if seen_at - basis > MAX_AGE_SECONDS * 1000 or basis > seen_at + 60_000:
        return None
    return {
        'handle': handle,
        'post_id': post_id,
        'post_url': url,
        'created_at': raw_created or None,
        'post_created_at_ms': created_ms or None,
        'seen_at_ms': seen_at,
        'collector_delay_seconds': round(max(0, seen_at - basis) / 1000, 3),
        'text_excerpt': text[:500],
        'addresses': addresses,
    }


def build_request(last_success_ms):
    now = dt.datetime.now(dt.timezone.utc)
    from_date = (now - dt.timedelta(days=1 if now.hour == 0 else 0)).date().isoformat()
    since = dt.datetime.fromtimestamp(max(0, last_success_ms) / 1000, tz=dt.timezone.utc).isoformat() if last_success_ms else 'none'
    prompt = (
        'Search X only within the allowed handles. Find NEW posts relevant to this poll, preferably newer than '
        f'{since}, and never older than {MAX_AGE_SECONDS} seconds from the current time {now.isoformat()}. '
        'Return ONLY a JSON array, maximum 5 items. Include an item only when the literal post text itself contains '
        'one or more complete Solana base58 token/mint/contract addresses (32-44 characters). Do not infer an address '
        'from a ticker, image, reply context, linked webpage, profile, or another post. Do not invent addresses. '
        'Each item must have exactly: handle, post_id, post_url, created_at (ISO 8601), text, addresses. '
        'If there are no qualifying new posts return [].'
    )
    return {
        'model': MODEL,
        'input': prompt,
        'reasoning': {'effort': 'none'},
        'max_output_tokens': 900,
        'tools': [{'type': 'x_search', 'allowed_x_handles': HANDLES, 'from_date': from_date, 'to_date': now.date().isoformat()}],
        'store': False,
    }


def poll_once(state):
    day = utc_day()
    budget = state.get('budget') if isinstance(state.get('budget'), dict) else {}
    if budget.get('day') != day:
        budget = {'day': day, 'spent_usd': 0.0, 'x_posts_fetched': 0, 'x_users_fetched': 0,
                  'input_tokens': 0, 'output_tokens': 0, 'calls': 0}
    spent = float(budget.get('spent_usd') or 0.0)
    if DAILY_BUDGET_USD and spent >= max(0.0, DAILY_BUDGET_USD - 0.10):
        state.update({'status': 'budget_paused', 'updated_at': now_ms(), 'handles': HANDLES,
                      'poll_seconds': POLL_SECONDS, 'daily_budget_usd': DAILY_BUDGET_USD, 'budget': budget})
        return state
    if not API_KEY:
        state.update({'status': 'missing_api_key', 'updated_at': now_ms(), 'handles': HANDLES, 'budget': budget})
        return state
    if not HANDLES:
        state.update({'status': 'no_handles', 'updated_at': now_ms(), 'handles': [], 'budget': budget})
        return state

    seen_at = now_ms()
    payload = build_request(int(state.get('last_success_at') or 0))
    response = SESSION.post(API_URL, json=payload, timeout=TIMEOUT)
    response.raise_for_status()
    body = response.json()
    cost, posts, users, input_tokens, output_tokens = usage_cost(body.get('usage'))
    budget['spent_usd'] = round(spent + cost, 6)
    budget['x_posts_fetched'] = int(budget.get('x_posts_fetched') or 0) + posts
    budget['x_users_fetched'] = int(budget.get('x_users_fetched') or 0) + users
    budget['input_tokens'] = int(budget.get('input_tokens') or 0) + input_tokens
    budget['output_tokens'] = int(budget.get('output_tokens') or 0) + output_tokens
    budget['calls'] = int(budget.get('calls') or 0) + 1

    rows = parse_json_text(output_text(body))
    incoming = [normalize_signal(row, seen_at) for row in rows]
    incoming = [row for row in incoming if row]
    existing = [s for s in (state.get('signals') or []) if isinstance(s, dict)]
    keep_after = seen_at - 24 * 3600 * 1000
    merged, seen = [], set()
    for signal in incoming + existing:
        key = (signal.get('post_id'), tuple(signal.get('addresses') or []))
        stamp = int(signal.get('seen_at_ms') or 0)
        if key in seen or stamp < keep_after:
            continue
        seen.add(key); merged.append(signal)
    merged.sort(key=lambda s: int(s.get('seen_at_ms') or 0), reverse=True)

    state.update({
        'status': 'online', 'updated_at': seen_at, 'last_success_at': seen_at,
        'handles': HANDLES, 'model': MODEL, 'poll_seconds': POLL_SECONDS,
        'daily_budget_usd': DAILY_BUDGET_USD, 'max_signal_age_seconds': MAX_AGE_SECONDS,
        'budget': budget, 'signals': merged[:200], 'last_response_id': body.get('id'),
        'last_result_count': len(incoming),
    })
    state.pop('error', None)
    return state


def main():
    state = load_state()
    while True:
        started = time.time()
        try:
            state = poll_once(state)
        except Exception as exc:
            state.update({'status': 'degraded', 'updated_at': now_ms(), 'handles': HANDLES,
                          'error': f'{type(exc).__name__}: {exc}'[:300]})
        atomic_write(state)
        time.sleep(max(1.0, POLL_SECONDS - (time.time() - started)))


if __name__ == '__main__':
    main()

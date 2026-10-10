#!/usr/bin/env python3
"""Hype Radar: world news + viral memes → themes → meme-coin candidates (PAPER).

A small collector that:

1. reads FREE public sources (Google News RSS, Reddit top posts, CoinGecko
   trending, DexScreener boosts) and, when the X Signal collector is running,
   its recent posts;
2. asks one cheap LLM call (OpenAI-compatible chat endpoint; xAI's
   grok-4.1-fast by default, any provider via env) to turn those headlines
   into themes with keywords people would name a meme coin after, each with a
   0–100 hype score and the source lines it came from;
3. writes ``hype_radar.json`` for the Strategy Lab's HYPE_RADAR book and the
   dashboard.

It never places orders and never touches the main engine. Matching a token
to a theme is a pure function here (``match_token``) so the lab and the
dashboard agree. Spend is capped per UTC day from the model's token usage.
"""
import datetime as dt
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

import requests

LLM_URL = os.getenv('NEO_HYPE_LLM_URL', 'https://api.x.ai/v1/chat/completions')
LLM_KEY = (os.getenv('NEO_HYPE_LLM_KEY') or os.getenv('XAI_API_KEY') or '').strip()
LLM_MODEL = os.getenv('NEO_HYPE_LLM_MODEL', 'grok-4.20-0309-non-reasoning').strip() or 'grok-4.20-0309-non-reasoning'
POLL_SECONDS = max(120.0, float(os.getenv('NEO_HYPE_POLL_SECONDS', '900')))
DAILY_BUDGET_USD = max(0.0, float(os.getenv('NEO_HYPE_DAILY_BUDGET_USD', '1.00')))
INPUT_MTOKEN_USD = float(os.getenv('NEO_HYPE_INPUT_MTOKEN_USD', '1.25'))
OUTPUT_MTOKEN_USD = float(os.getenv('NEO_HYPE_OUTPUT_MTOKEN_USD', '2.50'))
TIMEOUT = max(5.0, float(os.getenv('NEO_HYPE_REQUEST_TIMEOUT_SECONDS', '40')))
STATE_PATH = Path(os.getenv('NEO_HYPE_STATE_PATH', '/var/lib/neo-market/hype_radar.json'))
X_SIGNAL_PATH = Path(os.getenv('NEO_X_SIGNAL_STATE_PATH', '/var/lib/neo-market/x_signal.json'))
THEME_TTL_SECONDS = max(600, int(os.getenv('NEO_HYPE_THEME_TTL_SECONDS', '10800')))
MIN_HYPE = max(0.0, float(os.getenv('NEO_HYPE_MIN_HYPE', '40')))
MAX_THEMES = 12
MAX_SOURCE_LINES = 90

SOURCES = (
    ('google-news-top', 'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en', 'rss'),
    ('google-news-crypto', 'https://news.google.com/rss/search?q=crypto+OR+memecoin+OR+solana&hl=en-US&gl=US&ceid=US:en', 'rss'),
    ('reddit-memes', 'https://www.reddit.com/r/memes/top.json?t=day&limit=25', 'reddit'),
    ('reddit-crypto', 'https://www.reddit.com/r/CryptoCurrency/hot.json?limit=25', 'reddit'),
    ('reddit-solana-memes', 'https://www.reddit.com/r/SolanaMemeCoins/new.json?limit=25', 'reddit'),
    ('coingecko-trending', 'https://api.coingecko.com/api/v3/search/trending', 'coingecko'),
    ('dexscreener-boosts', 'https://api.dexscreener.com/token-boosts/top/v1', 'dexscreener'),
)
STOP_WORDS = {'the', 'and', 'for', 'with', 'coin', 'token', 'meme', 'memecoin', 'crypto', 'solana', 'pump', 'fun',
              'new', 'this', 'that', 'from', 'what', 'about', 'after', 'over', 'into', 'your', 'you', 'are', 'was',
              'has', 'have', 'will', 'its', 'their', 'they', 'his', 'her', 'who', 'how', 'why', 'all', 'one', 'two'}
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NEO-Hype-Radar/1.0 (paper research bot)', 'Accept': 'application/json, application/rss+xml, text/xml;q=0.9, */*;q=0.5'})


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_day() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def load_json(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default


def atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


# ------------------------------------------------------------- sources ----
def parse_rss_titles(text: str) -> list[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    titles = []
    for item in root.iter('item'):
        title = (item.findtext('title') or '').strip()
        if title:
            titles.append(html.unescape(title))
    return titles


def parse_reddit_titles(payload: dict[str, Any]) -> list[str]:
    out = []
    for child in ((payload or {}).get('data') or {}).get('children') or []:
        data = (child or {}).get('data') or {}
        title = str(data.get('title') or '').strip()
        if title:
            score = int(data.get('ups') or data.get('score') or 0)
            out.append(f'{title} (↑{score})' if score else title)
    return out


def parse_coingecko_trending(payload: dict[str, Any]) -> list[str]:
    out = []
    for row in (payload or {}).get('coins') or []:
        item = (row or {}).get('item') or {}
        name, symbol = str(item.get('name') or '').strip(), str(item.get('symbol') or '').strip()
        if name:
            out.append(f'trending: {name} ({symbol})' if symbol else f'trending: {name}')
    return out


def parse_dexscreener_boosts(payload: Any) -> list[str]:
    out = []
    for row in payload if isinstance(payload, list) else []:
        if str((row or {}).get('chainId') or '') != 'solana':
            continue
        desc = str(row.get('description') or '').strip().replace('\n', ' ')[:120]
        out.append(f'boosted solana token {row.get("tokenAddress", "")[:8]}…: {desc}' if desc else f'boosted solana token {row.get("tokenAddress", "")}')
    return out[:15]


def x_signal_lines(path: Path = X_SIGNAL_PATH, *, now: int | None = None) -> list[str]:
    now = now or now_ms()
    data = load_json(path, {})
    lines = []
    for signal in data.get('signals') or []:
        if not isinstance(signal, dict):
            continue
        stamp = int(signal.get('post_created_at_ms') or signal.get('seen_at_ms') or 0)
        if stamp and now - stamp <= 6 * 3600 * 1000:
            lines.append(f"@{signal.get('handle')}: {str(signal.get('text_excerpt') or '')[:200]}")
    return lines[:15]


def collect_sources(fetch: Callable[[str, str], Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Returns numbered source lines and a per-source status map."""
    lines, status = [], {}
    for name, url, kind in SOURCES:
        try:
            payload = fetch(url, kind)
            if kind == 'rss':
                titles = parse_rss_titles(payload)
            elif kind == 'reddit':
                titles = parse_reddit_titles(payload)
            elif kind == 'coingecko':
                titles = parse_coingecko_trending(payload)
            else:
                titles = parse_dexscreener_boosts(payload)
            titles = titles[:25]
            status[name] = f'ok:{len(titles)}'
            lines.extend({'source': name, 'text': t[:200]} for t in titles)
        except Exception as exc:
            status[name] = f'error:{str(exc)[:80]}'
    try:
        x_lines = x_signal_lines()
        status['x-signal'] = f'ok:{len(x_lines)}' if x_lines else 'none'
        lines.extend({'source': 'x-signal', 'text': t} for t in x_lines)
    except Exception as exc:
        status['x-signal'] = f'error:{str(exc)[:80]}'
    return lines[:MAX_SOURCE_LINES], status


# ---------------------------------------------------------------- LLM -----
def build_prompt(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
    numbered = '\n'.join(f'[{i}] ({row["source"]}) {row["text"]}' for i, row in enumerate(lines))
    system = (
        'You rank what is viral RIGHT NOW for meme-coin naming on Solana. Input: numbered headlines, top memes, '
        'trending coins and boosted tokens. Output ONLY a JSON array (no prose) of at most 12 themes, strongest first. '
        'Each item: {"theme": short name, "keywords": 3-8 lowercase single words or short tokens people would put in '
        'a coin NAME or TICKER for this theme (names of people, animals, slogans, objects; no generic words like '
        'coin, token, meme, crypto, new), "hype": 0-100 how viral and fresh it is today, "category": "news"|"meme"|'
        '"crypto"|"celebrity"|"politics"|"sport"|"other", "why": one sentence, "sources": [indices of input lines '
        'that support it]}. Prefer concrete, specific, nameable things. Never invent events not in the input.'
    )
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': numbered or '(no input lines)'}]


def parse_themes_text(text: str, *, generated_at: int, line_count: int) -> list[dict[str, Any]]:
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
    text = re.sub(r'\s*```$', '', text)
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end < start:
        return []
    try:
        rows = json.loads(text[start:end + 1])
    except Exception:
        return []
    themes = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        theme = str(row.get('theme') or '').strip()[:80]
        keywords = normalize_keywords(row.get('keywords') or [])
        if not theme or not keywords:
            continue
        try:
            hype = max(0.0, min(100.0, float(row.get('hype') or 0)))
        except (TypeError, ValueError):
            hype = 0.0
        sources = [int(i) for i in (row.get('sources') or []) if isinstance(i, (int, float)) and 0 <= int(i) < line_count][:8]
        themes.append({'theme': theme, 'keywords': keywords, 'hype': round(hype, 1),
                       'category': str(row.get('category') or 'other')[:20], 'why': str(row.get('why') or '')[:240],
                       'sources': sources, 'generated_at': generated_at})
    themes.sort(key=lambda t: -t['hype'])
    return themes[:MAX_THEMES]


def normalize_keywords(values) -> list[str]:
    out = []
    for value in values if isinstance(values, list) else []:
        word = re.sub(r'[^a-z0-9]', '', str(value).lower())
        if 3 <= len(word) <= 24 and word not in STOP_WORDS and word not in out:
            out.append(word)
    return out[:8]


FALLBACK_STOP_WORDS = STOP_WORDS | {
    'says', 'said', 'amid', 'could', 'would', 'should', 'today', 'live', 'latest', 'update', 'updates',
    'market', 'markets', 'price', 'prices', 'world', 'news', 'report', 'reports', 'top', 'day', 'week',
    'trending', 'boosted', 'address', 'official', 'launch', 'launches', 'based', 'first', 'more', 'most',
}


def fallback_themes(lines: list[dict[str, Any]], *, generated_at: int) -> list[dict[str, Any]]:
    """Build fresh, source-grounded themes when the optional LLM is unavailable.

    The fallback never invents events: every theme and keyword comes directly from
    a current source line. It deliberately favors explicit CoinGecko trends and
    concrete proper nouns in news/X text over generic headline words.
    """
    weighted: dict[str, dict[str, Any]] = {}
    source_weight = {
        'coingecko-trending': 92.0, 'x-signal': 88.0, 'google-news-top': 76.0,
        'google-news-crypto': 72.0, 'reddit-memes': 78.0, 'reddit-crypto': 68.0,
        'reddit-solana-memes': 74.0, 'dexscreener-boosts': 58.0,
    }

    def add(theme: str, keywords: list[str], index: int, source: str, base: float) -> None:
        clean = normalize_keywords(keywords)
        if not clean:
            return
        key = clean[0]
        row = weighted.setdefault(key, {
            'theme': theme.strip()[:80] or clean[0], 'keywords': [], 'hype': base,
            'category': 'crypto' if source in {'coingecko-trending', 'dexscreener-boosts', 'reddit-crypto', 'reddit-solana-memes'} else
                        'meme' if source == 'reddit-memes' else 'news',
            'why': f'Current source: {source}', 'sources': [], 'generated_at': generated_at, '_hits': 0,
        })
        row['_hits'] += 1
        row['hype'] = max(float(row['hype']), base)
        if index not in row['sources']:
            row['sources'].append(index)
        for word in clean:
            if word not in row['keywords'] and len(row['keywords']) < 8:
                row['keywords'].append(word)

    for index, row in enumerate(lines):
        source = str((row or {}).get('source') or '')
        text = str((row or {}).get('text') or '').strip()
        if not text:
            continue
        base = source_weight.get(source, 60.0)
        trending = re.match(r'^trending:\s*(.+?)(?:\s*\(([^)]+)\))?$', text, flags=re.I)
        if trending:
            name = trending.group(1).strip()
            symbol = (trending.group(2) or '').strip()
            words = re.findall(r'[A-Za-z0-9]{3,24}', name)
            add(name, ([symbol] if symbol else []) + words, index, source, base)
            continue

        # Google News commonly appends " - Publisher"; the publisher itself is
        # not a hype theme, so only inspect the headline side.
        headline = text.rsplit(' - ', 1)[0].strip()
        proper = re.findall(r'(?<![A-Za-z0-9])(?:[A-Z][A-Za-z0-9]{2,})(?:\s+[A-Z][A-Za-z0-9]{2,}){0,2}', headline)
        tokens = [w for w in re.findall(r'[A-Za-z0-9]{3,24}', headline) if w.lower() not in FALLBACK_STOP_WORDS]
        if proper:
            phrase = max(proper, key=lambda value: (len(value.split()), len(value)))
            add(phrase, phrase.split() + tokens[:4], index, source, base)
        elif tokens:
            add(' '.join(tokens[:3]), tokens[:6], index, source, base - 8.0)

    themes = []
    for row in weighted.values():
        hits = int(row.pop('_hits', 0))
        row['hype'] = round(min(96.0, float(row['hype']) + min(12.0, max(0, hits - 1) * 4.0)), 1)
        row['sources'] = row['sources'][:8]
        themes.append(row)
    themes.sort(key=lambda row: (-float(row['hype']), -len(row['sources']), row['theme'].lower()))
    return themes[:MAX_THEMES]


def usage_cost(usage: dict[str, Any] | None) -> tuple[float, int, int]:
    usage = usage or {}
    input_tokens = int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
    output_tokens = int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
    return (input_tokens / 1e6 * INPUT_MTOKEN_USD + output_tokens / 1e6 * OUTPUT_MTOKEN_USD, input_tokens, output_tokens)


# ------------------------------------------------------------ matching ----
def _norm(text: Any) -> str:
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


def match_token(coin: dict[str, Any], themes: list[dict[str, Any]], *, now: int | None = None,
                min_hype: float = MIN_HYPE, ttl_seconds: int = THEME_TTL_SECONDS) -> dict[str, Any] | None:
    """Best fresh theme whose keyword appears in the token's name or symbol."""
    now = now or now_ms()
    name, symbol = _norm(coin.get('name')), _norm(coin.get('symbol'))
    if not name and not symbol:
        return None
    best = None
    for theme in themes:
        if not isinstance(theme, dict) or float(theme.get('hype') or 0) < min_hype:
            continue
        generated = int(theme.get('generated_at') or 0)
        if generated and now - generated > ttl_seconds * 1000:
            continue
        for keyword in theme.get('keywords') or []:
            # Short keywords must equal the ticker; longer ones may sit inside the name.
            hit = symbol == keyword or (len(keyword) >= 4 and (keyword in name or keyword in symbol))
            if not hit:
                continue
            score = float(theme['hype']) * (1.0 if symbol == keyword else 0.85) * min(1.0, 0.6 + len(keyword) / 10)
            if best is None or score > best['score']:
                best = {'theme': theme['theme'], 'keyword': keyword, 'hype': float(theme['hype']),
                        'score': round(score, 2), 'category': theme.get('category'), 'generated_at': generated}
    return best


def active_themes(data: dict[str, Any], *, now: int | None = None) -> list[dict[str, Any]]:
    now = now or now_ms()
    out = []
    for theme in (data or {}).get('themes') or []:
        generated = int((theme or {}).get('generated_at') or 0)
        if isinstance(theme, dict) and theme.get('keywords') and generated and now - generated <= THEME_TTL_SECONDS * 1000:
            out.append(theme)
    return out


# -------------------------------------------------------------- service ---
def default_fetch(url: str, kind: str):
    response = SESSION.get(url, timeout=(3, 12))
    response.raise_for_status()
    return response.text if kind == 'rss' else response.json()


def default_llm(messages: list[dict[str, str]]) -> dict[str, Any]:
    response = SESSION.post(LLM_URL, headers={'Authorization': f'Bearer {LLM_KEY}', 'Content-Type': 'application/json'},
                            json={'model': LLM_MODEL, 'messages': messages, 'temperature': 0.2, 'max_tokens': 1400},
                            timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def poll_once(state: dict[str, Any], *, fetch=default_fetch, llm=default_llm, now: int | None = None) -> dict[str, Any]:
    now = now or now_ms()
    day = utc_day()
    budget = state.get('budget') if isinstance(state.get('budget'), dict) else {}
    if budget.get('day') != day:
        budget = {'day': day, 'spent_usd': 0.0, 'input_tokens': 0, 'output_tokens': 0, 'calls': 0}
    base = {'updated_at': now, 'model': LLM_MODEL, 'llm_url': LLM_URL, 'poll_seconds': POLL_SECONDS,
            'daily_budget_usd': DAILY_BUDGET_USD, 'theme_ttl_seconds': THEME_TTL_SECONDS, 'min_hype': MIN_HYPE, 'budget': budget}
    if not LLM_KEY:
        state.update(base, status='missing_api_key')
        return state
    if DAILY_BUDGET_USD and float(budget.get('spent_usd') or 0) >= DAILY_BUDGET_USD:
        state.update(base, status='budget_paused')
        return state
    lines, source_status = collect_sources(fetch)
    if not lines:
        state.update(base, status='no_sources', source_status=source_status)
        return state
    llm_error = None
    text = ''
    cost = 0.0
    try:
        body = llm(build_prompt(lines))
        cost, input_tokens, output_tokens = usage_cost(body.get('usage'))
        budget.update(spent_usd=round(float(budget.get('spent_usd') or 0) + cost, 6),
                      input_tokens=int(budget.get('input_tokens') or 0) + input_tokens,
                      output_tokens=int(budget.get('output_tokens') or 0) + output_tokens,
                      calls=int(budget.get('calls') or 0) + 1)
        for choice in body.get('choices') or []:
            message = (choice or {}).get('message') or {}
            if message.get('content'):
                text = str(message['content']); break
        themes = parse_themes_text(text, generated_at=now, line_count=len(lines))
    except Exception as exc:
        llm_error = f'{type(exc).__name__}: {exc}'[:300]
        themes = []

    generation = 'llm'
    if not themes:
        themes = fallback_themes(lines, generated_at=now)
        generation = 'source_fallback'

    state.update(base, status='online' if themes else 'empty_answer',
                 last_success_at=now if themes else state.get('last_success_at'),
                 themes=themes if themes else active_themes(state, now=now), source_lines=lines, source_status=source_status,
                 theme_generation=generation, llm_error=llm_error,
                 last_call_cost_usd=round(cost, 6), last_raw_excerpt=text[:300])
    # A provider failure is diagnostic when the source-grounded fallback succeeds,
    # not a collector outage. Keep it in llm_error without painting Hype red.
    state.pop('error', None)
    return state


def main() -> None:
    state = load_json(STATE_PATH, {})
    while True:
        started = time.time()
        try:
            state = poll_once(state)
        except Exception as exc:
            state.update({'status': 'degraded', 'updated_at': now_ms(), 'error': f'{type(exc).__name__}: {exc}'[:300]})
        atomic_write(STATE_PATH, state)
        time.sleep(max(5.0, POLL_SECONDS - (time.time() - started)))


if __name__ == '__main__':
    main()

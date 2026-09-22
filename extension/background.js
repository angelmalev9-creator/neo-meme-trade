import { analyze } from './engine.js';
import { radarAnalyze } from './radar.js';

const RADAR_CACHE_MS = 20_000;
const RESOLVE_CACHE_MS = 30_000;
const radarCache = new Map();
const resolveCache = new Map();

function n(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function closenessScore(observed, actual, maxPoints) {
  const a = n(observed);
  const b = n(actual);
  if (!(a > 0) || !(b > 0)) return 0;
  const rel = Math.abs(a - b) / Math.max(a, b);
  if (rel <= 0.12) return maxPoints;
  if (rel <= 0.25) return Math.round(maxPoints * 0.8);
  if (rel <= 0.45) return Math.round(maxPoints * 0.55);
  if (rel <= 0.7) return Math.round(maxPoints * 0.25);
  return -Math.round(maxPoints * 0.6);
}

async function resolveVisibleItem(item) {
  const query = String(item?.query || item?.symbol || '').trim();
  if (!query) return null;

  const cacheKey = `${query.toLowerCase()}|${Math.round(n(item?.marketCapUsd) / 1000)}|${n(item?.priceUsd).toPrecision(5)}`;
  const cached = resolveCache.get(cacheKey);
  if (cached && Date.now() - cached.at < RESOLVE_CACHE_MS) return cached.result;

  const response = await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(query)}`);
  if (!response.ok) throw new Error(`DEX search HTTP ${response.status}`);
  const payload = await response.json();
  const pairs = Array.isArray(payload?.pairs) ? payload.pairs : [];
  const q = query.toLowerCase();

  const candidates = pairs
    .filter((pair) => pair?.chainId === 'solana' && pair?.baseToken?.address)
    .map((pair) => {
      const symbol = String(pair?.baseToken?.symbol || '');
      const name = String(pair?.baseToken?.name || '');
      const exactSymbol = symbol.toLowerCase() === q;
      const exactName = name.toLowerCase() === q;
      if (!exactSymbol && !exactName) return null;

      const marketCap = n(pair?.marketCap || pair?.fdv);
      const price = n(pair?.priceUsd);
      const liquidity = n(pair?.liquidity?.usd);
      let score = exactSymbol ? 46 : 36;
      score += closenessScore(item?.marketCapUsd, marketCap, 38);
      score += closenessScore(item?.priceUsd, price, 14);
      score += Math.min(8, Math.max(0, Math.log10(liquidity + 1) - 2));

      return { pair, score, marketCap, price, liquidity, exactSymbol, exactName };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);

  if (!candidates.length) {
    resolveCache.set(cacheKey, { at: Date.now(), result: null });
    return null;
  }

  const best = candidates[0];
  const second = candidates[1];
  const hasAnchor = n(item?.marketCapUsd) > 0 || n(item?.priceUsd) > 0;
  const ambiguous = second && best.score - second.score < 7;
  if ((best.score < 35) || (!hasAnchor && ambiguous)) {
    resolveCache.set(cacheKey, { at: Date.now(), result: null });
    return null;
  }

  const result = {
    token: String(best.pair.baseToken.address),
    query,
    symbol: String(best.pair.baseToken.symbol || query),
    name: String(best.pair.baseToken.name || query),
    pairAddress: String(best.pair.pairAddress || ''),
    marketCapUsd: best.marketCap,
    priceUsd: best.price,
    liquidityUsd: best.liquidity,
    resolutionScore: Math.round(best.score),
    context: String(item?.context || '').slice(0, 500),
  };
  resolveCache.set(cacheKey, { at: Date.now(), result });
  return result;
}

async function resolveVisibleMany(items) {
  const queue = Array.isArray(items) ? items.slice(0, 16) : [];
  const results = [];
  let cursor = 0;

  async function worker() {
    while (cursor < queue.length) {
      const index = cursor++;
      try {
        const resolved = await resolveVisibleItem(queue[index]);
        if (resolved) results[index] = resolved;
      } catch {
        // Skip unresolved/temporary network failures; the next Sentinel cycle retries.
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(3, queue.length || 1) }, worker));
  return results.filter(Boolean);
}

async function radarOne(item) {
  const token = String(item?.token || item?.tokenAddress || '').trim();
  if (!token) throw new Error('Missing radar token.');

  const cached = radarCache.get(token);
  if (cached && Date.now() - cached.at < RADAR_CACHE_MS) return cached.result;

  const result = await radarAnalyze(token, item || {});
  radarCache.set(token, { at: Date.now(), result });
  return result;
}

async function radarMany(items) {
  const queue = Array.isArray(items) ? items.slice(0, 12) : [];
  const results = [];
  let cursor = 0;

  async function worker() {
    while (cursor < queue.length) {
      const index = cursor++;
      try {
        results[index] = await radarOne(queue[index]);
      } catch (error) {
        results[index] = {
          tokenAddress: queue[index]?.token || '',
          error: error instanceof Error ? error.message : String(error),
          priority: 0,
          risk: 100,
          status: 'UNAVAILABLE',
        };
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(3, queue.length || 1) }, worker));
  return results
    .filter(Boolean)
    .sort((a, b) => (b.priority || 0) - (a.priority || 0));
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'NEO_ANALYZE') {
    analyze(message.input, message.pageContext || {})
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      }));
    return true;
  }

  if (message?.type === 'NEO_RADAR_SCAN') {
    radarMany(message.items)
      .then((results) => sendResponse({ ok: true, results }))
      .catch((error) => sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      }));
    return true;
  }

  if (message?.type === 'NEO_RESOLVE_VISIBLE_TOKENS') {
    resolveVisibleMany(message.items)
      .then((results) => sendResponse({ ok: true, results }))
      .catch((error) => sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      }));
    return true;
  }

  return false;
});

import { analyze } from './engine.js';
import { radarAnalyze } from './radar.js';

const RADAR_CACHE_MS = 20_000;
const radarCache = new Map();

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

  return false;
});

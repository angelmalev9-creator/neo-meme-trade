(() => {
  const host = location.hostname.toLowerCase();
  if (!(host === 'fomo.family' || host.endsWith('.fomo.family'))) return;

  const ROOT_ID = 'neo-fomo-resolved-candidates';
  const SCAN_MS = 10_000;
  let running = false;
  let lastFingerprint = '';

  function parseCompactMoney(raw) {
    const match = String(raw || '').replace(/,/g, '').match(/([0-9]*\.?[0-9]+)\s*([KMB])?/i);
    if (!match) return 0;
    const value = Number(match[1]) || 0;
    const unit = String(match[2] || '').toUpperCase();
    const mult = unit === 'B' ? 1e9 : unit === 'M' ? 1e6 : unit === 'K' ? 1e3 : 1;
    return value * mult;
  }

  function cleanLabel(raw) {
    return String(raw || '').replace(/\s+/g, ' ').trim();
  }

  function usableLabel(line) {
    const value = cleanLabel(line);
    if (!value || value.length > 42) return false;
    if (/^\$?[0-9.,]+(?:[KMB])?(?:\s*MC)?$/i.test(value)) return false;
    if (/^[+\-▲▼]?\s*[0-9.,]+%$/.test(value)) return false;
    if (/^(buy|sell|buys|sells|all swaps|action|amount|mcap|time|tokens?|trending|watchlist|crypto|most held|alerts|leaderboard|feed)$/i.test(value)) return false;
    if (/^(h|d|w|m|min|hr|hrs|day|days|week|weeks)$/i.test(value)) return false;
    return /[A-Za-z\u0080-\uFFFF]/.test(value);
  }

  function parseBlock(text) {
    const raw = String(text || '');
    if (!/\bMC\b/i.test(raw)) return null;
    const lines = raw.split(/\n+/).map(cleanLabel).filter(Boolean);
    if (!lines.length) return null;

    const mcIndex = lines.findIndex((line) => /\$\s*[0-9.,]+\s*[KMB]?\s*MC\b/i.test(line));
    if (mcIndex < 0) return null;
    const mcMatch = lines[mcIndex].match(/\$\s*([0-9.,]+\s*[KMB]?)\s*MC\b/i);
    const marketCapUsd = mcMatch ? parseCompactMoney(mcMatch[1]) : 0;
    if (!marketCapUsd) return null;

    let query = '';
    for (let i = mcIndex - 1; i >= Math.max(0, mcIndex - 5); i -= 1) {
      if (usableLabel(lines[i])) { query = lines[i]; break; }
    }
    if (!query) {
      for (let i = 0; i < Math.min(lines.length, 5); i += 1) {
        if (usableLabel(lines[i])) { query = lines[i]; break; }
      }
    }
    if (!query) return null;

    let priceUsd = 0;
    const priceCandidates = [];
    for (const line of lines) {
      if (/\bMC\b/i.test(line)) continue;
      const m = line.match(/^\$\s*([0-9][0-9.,]*(?:\.[0-9]+)?|0?\.[0-9]+)/);
      if (m) {
        const v = Number(String(m[1]).replace(/,/g, ''));
        if (Number.isFinite(v) && v > 0) priceCandidates.push(v);
      }
    }
    if (priceCandidates.length) priceUsd = Math.min(...priceCandidates);

    return {
      query: query.replace(/^\$/, '').trim(),
      marketCapUsd,
      priceUsd,
      context: lines.slice(0, 10).join(' · ').slice(0, 420),
    };
  }

  function collectVisibleCandidates() {
    const found = new Map();
    const add = (item) => {
      if (!item?.query || !item.marketCapUsd) return;
      const key = `${item.query.toLowerCase()}|${Math.round(item.marketCapUsd / 1000)}`;
      if (!found.has(key)) found.set(key, item);
    };

    const selectors = 'tr,[role="row"],li,a,button,[class*="token"],[class*="coin"],[class*="card"]';
    for (const node of [...document.querySelectorAll(selectors)].slice(0, 4500)) {
      if (!(node instanceof HTMLElement)) continue;
      const rect = node.getBoundingClientRect();
      if (rect.width < 30 || rect.height < 12 || rect.bottom < 0 || rect.top > innerHeight) continue;
      const text = node.innerText || '';
      if (text.length < 4 || text.length > 520 || !/\bMC\b/i.test(text)) continue;
      add(parseBlock(text));
      if (found.size >= 24) break;
    }

    if (found.size < 8) {
      const lines = String(document.body?.innerText || '').split(/\n+/).map(cleanLabel).filter(Boolean);
      for (let i = 0; i < lines.length; i += 1) {
        if (!/\$\s*[0-9.,]+\s*[KMB]?\s*MC\b/i.test(lines[i])) continue;
        const block = lines.slice(Math.max(0, i - 4), Math.min(lines.length, i + 2)).join('\n');
        add(parseBlock(block));
        if (found.size >= 24) break;
      }
    }

    return [...found.values()].slice(0, 16);
  }

  function injectResolved(results) {
    let root = document.getElementById(ROOT_ID);
    if (!root) {
      root = document.createElement('div');
      root.id = ROOT_ID;
      root.style.display = 'none';
      root.setAttribute('aria-hidden', 'true');
      document.documentElement.appendChild(root);
    }
    root.replaceChildren();

    for (const item of results || []) {
      if (!item?.token) continue;
      const row = document.createElement('div');
      row.className = 'neo-token-discovery';
      row.setAttribute('data-token-address', item.token);
      row.setAttribute('data-coin-address', item.token);
      row.textContent = `${item.symbol || item.query || 'TOKEN'} ${item.name || ''} ${item.context || ''}`;
      root.appendChild(row);
    }
  }

  async function scan() {
    if (running) return;
    const items = collectVisibleCandidates();
    const fingerprint = items.map((x) => `${x.query}:${Math.round(x.marketCapUsd / 1000)}`).join('|');
    if (!items.length) return;
    if (fingerprint === lastFingerprint && document.getElementById(ROOT_ID)?.children.length) return;

    running = true;
    try {
      const response = await chrome.runtime.sendMessage({ type: 'NEO_RESOLVE_VISIBLE_TOKENS', items });
      if (response?.ok) {
        injectResolved(response.results || []);
        lastFingerprint = fingerprint;
      }
    } catch {
      // The main Sentinel remains active even if symbol resolution temporarily fails.
    } finally {
      running = false;
    }
  }

  setTimeout(scan, 800);
  setInterval(scan, SCAN_MS);
  document.addEventListener('click', () => setTimeout(scan, 500), true);
})();

const DEFAULT_RPC = 'https://api.mainnet.solana.com';
let rpcId = 1;

async function rpcCall(rpcUrl, method, params) {
  const response = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: rpcId++, method, params }),
  });
  if (!response.ok) throw new Error(`RPC HTTP ${response.status}`);
  const payload = await response.json();
  if (payload.error) throw new Error(`RPC ${payload.error.code}: ${payload.error.message}`);
  return payload.result;
}

function numberOrZero(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

async function resolveTokenAddress(input) {
  const clean = String(input || '').trim();
  if (!clean) throw new Error('Missing token address.');

  try {
    const url = new URL(clean);
    if (url.hostname.includes('dexscreener.com')) {
      const parts = url.pathname.split('/').filter(Boolean);
      const solanaIndex = parts.indexOf('solana');
      const pairId = solanaIndex >= 0 ? parts[solanaIndex + 1] : null;
      if (pairId) {
        const response = await fetch(`https://api.dexscreener.com/latest/dex/pairs/solana/${encodeURIComponent(pairId)}`);
        if (response.ok) {
          const data = await response.json();
          const pair = Array.isArray(data?.pairs) ? data.pairs[0] : null;
          if (pair?.baseToken?.address) return pair.baseToken.address;
        }
      }
    }
  } catch {
    // Input is a normal token address, not a URL.
  }

  return clean;
}

async function fetchMarket(tokenAddress) {
  const response = await fetch(`https://api.dexscreener.com/token-pairs/v1/solana/${encodeURIComponent(tokenAddress)}`);
  if (!response.ok) throw new Error(`DEX Screener HTTP ${response.status}`);
  const payload = await response.json();
  const pairs = Array.isArray(payload) ? payload : Array.isArray(payload?.pairs) ? payload.pairs : [];
  const pair = pairs
    .filter((item) => item?.chainId === 'solana')
    .sort((a, b) => numberOrZero(b?.liquidity?.usd) - numberOrZero(a?.liquidity?.usd))[0];
  if (!pair) throw new Error('No active Solana pair found.');

  return {
    name: pair.baseToken?.name || 'Unknown token',
    symbol: pair.baseToken?.symbol || tokenAddress.slice(0, 5).toUpperCase(),
    tokenAddress,
    pairAddress: pair.pairAddress,
    liquidityUsd: numberOrZero(pair.liquidity?.usd),
    marketCapUsd: numberOrZero(pair.marketCap || pair.fdv),
    volume1hUsd: numberOrZero(pair.volume?.h1),
    buys1h: numberOrZero(pair.txns?.h1?.buys),
    sells1h: numberOrZero(pair.txns?.h1?.sells),
    pairCreatedAt: numberOrZero(pair.pairCreatedAt),
  };
}

async function fetchHolders(tokenAddress, rpcUrl) {
  const [supplyResult, largestResult] = await Promise.all([
    rpcCall(rpcUrl, 'getTokenSupply', [tokenAddress, { commitment: 'confirmed' }]),
    rpcCall(rpcUrl, 'getTokenLargestAccounts', [tokenAddress, { commitment: 'confirmed' }]),
  ]);

  const supply = numberOrZero(supplyResult?.value?.uiAmountString ?? supplyResult?.value?.uiAmount);
  const largest = Array.isArray(largestResult?.value) ? largestResult.value.slice(0, 10) : [];
  const percentages = largest.map((entry) => {
    const amount = numberOrZero(entry.uiAmountString ?? entry.uiAmount);
    return supply > 0 ? (amount / supply) * 100 : 0;
  });
  const sum = (count) => percentages.slice(0, count).reduce((a, b) => a + b, 0);

  return {
    top1Pct: sum(1),
    top5Pct: sum(5),
    top10Pct: sum(10),
  };
}

function score(market, holders) {
  let risk = 8;
  const signals = [];
  const cap = market.marketCapUsd;
  const liquidityRatio = cap > 0 ? (market.liquidityUsd / cap) * 100 : 0;

  const add = (label, detail, points, severity) => {
    risk += points;
    signals.push({ label, detail, points, severity });
  };

  if (market.liquidityUsd < 5000) add('Very thin liquidity', `$${market.liquidityUsd.toFixed(0)} visible liquidity`, 24, 'critical');
  else if (market.liquidityUsd < 15000) add('Low liquidity', `$${market.liquidityUsd.toFixed(0)} visible liquidity`, 13, 'warning');
  else if (market.liquidityUsd >= 50000) add('Meaningful liquidity', `$${market.liquidityUsd.toFixed(0)} visible liquidity`, -4, 'positive');

  if (cap > 0) {
    if (liquidityRatio < 2) add('Liquidity tiny vs cap', `${liquidityRatio.toFixed(2)}% liquidity / market cap`, 24, 'critical');
    else if (liquidityRatio < 5) add('Weak liquidity ratio', `${liquidityRatio.toFixed(2)}% liquidity / market cap`, 14, 'warning');
    else if (liquidityRatio < 10) add('Liquidity ratio needs caution', `${liquidityRatio.toFixed(2)}% liquidity / market cap`, 7, 'warning');
    else add('Visible liquidity ratio is supportive', `${liquidityRatio.toFixed(1)}% liquidity / market cap`, -5, 'positive');
  }

  if (holders) {
    if (holders.top1Pct >= 20) add('Single-account concentration', `Top raw token account: ${holders.top1Pct.toFixed(1)}%`, 22, 'critical');
    else if (holders.top1Pct >= 10) add('Large top holder', `Top raw token account: ${holders.top1Pct.toFixed(1)}%`, 12, 'warning');

    if (holders.top5Pct >= 55) add('Top 5 control most supply', `${holders.top5Pct.toFixed(1)}% combined`, 20, 'critical');
    else if (holders.top5Pct >= 35) add('Concentrated top 5', `${holders.top5Pct.toFixed(1)}% combined`, 10, 'warning');
    else add('Top accounts relatively distributed', `${holders.top5Pct.toFixed(1)}% combined`, -4, 'positive');
  }

  const txns = market.buys1h + market.sells1h;
  if (txns > 0) {
    const buyShare = market.buys1h / txns;
    if (buyShare > 0.92 || buyShare < 0.08) add('Extreme transaction imbalance', `${(buyShare * 100).toFixed(0)}% buys in 1h`, 8, 'warning');
  }

  if (market.liquidityUsd > 0 && market.volume1hUsd / market.liquidityUsd > 15) {
    add('Extreme volume / liquidity', `${(market.volume1hUsd / market.liquidityUsd).toFixed(1)}x in 1h`, 10, 'warning');
  }

  risk = Math.max(0, Math.min(100, Math.round(risk)));
  const posture = risk >= 65 ? 'SKIP' : risk >= 45 ? 'WAIT' : risk >= 25 ? 'WATCH' : 'SETUP';
  return { risk, posture, liquidityRatio, signals };
}

async function analyze(input) {
  const stored = await chrome.storage.local.get(['rpcUrl']);
  const rpcUrl = stored.rpcUrl || DEFAULT_RPC;
  const tokenAddress = await resolveTokenAddress(input);
  const market = await fetchMarket(tokenAddress);

  let holders = null;
  let holderError = null;
  try {
    holders = await fetchHolders(tokenAddress, rpcUrl);
  } catch (error) {
    holderError = error instanceof Error ? error.message : String(error);
  }

  const scored = score(market, holders);
  return {
    tokenAddress,
    market,
    holders,
    holderError,
    ...scored,
    generatedAt: Date.now(),
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'NEO_ANALYZE') return false;
  analyze(message.input)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
  return true;
});

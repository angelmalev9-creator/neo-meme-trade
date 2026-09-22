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
    .filter((item) => item?.chainId === 'solana' && item?.baseToken?.address === tokenAddress)
    .sort((a, b) => numberOrZero(b?.liquidity?.usd) - numberOrZero(a?.liquidity?.usd))[0];
  if (!pair) throw new Error('No base-token Solana pair found. Quote-side pairs are ignored to avoid mixing assets.');

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
  if (!supply || !largest.length) throw new Error('Holder distribution unavailable.');

  const addresses = largest.map((entry) => String(entry.address));
  let parsedAccounts = [];
  try {
    const parsed = await rpcCall(rpcUrl, 'getMultipleAccounts', [
      addresses,
      { encoding: 'jsonParsed', commitment: 'confirmed' },
    ]);
    parsedAccounts = Array.isArray(parsed?.value) ? parsed.value : [];
  } catch {
    parsedAccounts = [];
  }

  const holderRows = largest.map((entry, index) => {
    const amount = numberOrZero(entry.uiAmountString ?? entry.uiAmount);
    return {
      tokenAccount: String(entry.address),
      owner: parsedAccounts[index]?.data?.parsed?.info?.owner || null,
      percentage: supply > 0 ? (amount / supply) * 100 : 0,
    };
  });

  const percentages = holderRows.map((entry) => entry.percentage);
  const sum = (count) => percentages.slice(0, count).reduce((a, b) => a + b, 0);

  return {
    top1Pct: sum(1),
    top5Pct: sum(5),
    top10Pct: sum(10),
    holderRows,
  };
}

function extractIncomingTransfers(transaction, wallet) {
  const transfers = [];
  const inspect = (instruction) => {
    const parsed = instruction?.parsed;
    const info = parsed?.info;
    if (!parsed || !info) return;
    if (String(instruction?.program || '').toLowerCase() !== 'system') return;
    if (!String(parsed.type || '').toLowerCase().includes('transfer')) return;

    const destination = String(info.destination || info.to || '');
    const source = String(info.source || info.from || '');
    const lamports = numberOrZero(info.lamports);
    if (destination === wallet && source && source !== wallet && lamports > 0) {
      transfers.push({ source, lamports });
    }
  };

  const outer = transaction?.transaction?.message?.instructions;
  if (Array.isArray(outer)) outer.forEach(inspect);
  const inner = transaction?.meta?.innerInstructions;
  if (Array.isArray(inner)) {
    for (const group of inner) {
      if (Array.isArray(group?.instructions)) group.instructions.forEach(inspect);
    }
  }
  return transfers;
}

async function inspectWalletFunding(wallet, holderPercentage, rpcUrl) {
  const signatures = await rpcCall(rpcUrl, 'getSignaturesForAddress', [
    wallet,
    { limit: 41, commitment: 'confirmed' },
  ]);
  const valid = Array.isArray(signatures)
    ? signatures.filter((entry) => entry?.signature && !entry.err)
    : [];
  const times = valid.map((entry) => numberOrZero(entry.blockTime)).filter((value) => value > 0);
  const oldestAt = times.length ? Math.min(...times) * 1000 : null;
  const likelyFresh = valid.length <= 40 && Boolean(oldestAt && oldestAt >= Date.now() - 24 * 60 * 60 * 1000);

  const oldestCandidates = [...valid]
    .sort((a, b) => numberOrZero(a.blockTime) - numberOrZero(b.blockTime))
    .slice(0, 4);

  let funding = null;
  for (const entry of oldestCandidates) {
    try {
      const tx = await rpcCall(rpcUrl, 'getTransaction', [
        entry.signature,
        { encoding: 'jsonParsed', commitment: 'confirmed', maxSupportedTransactionVersion: 0 },
      ]);
      if (!tx) continue;
      const at = numberOrZero(tx?.blockTime || entry.blockTime) * 1000;
      const incoming = extractIncomingTransfers(tx, wallet);
      for (const transfer of incoming) {
        if (!funding || (at > 0 && at < funding.at)) {
          funding = { source: transfer.source, at, lamports: transfer.lamports };
        }
      }
    } catch {
      // Public RPC history can be pruned/rate-limited. Keep the rest of the scan alive.
    }
  }

  return {
    wallet,
    holderPercentage,
    likelyFresh,
    signatureCount: valid.length,
    oldestAt,
    fundingSource: funding?.source || null,
    fundingAt: funding?.at || null,
    fundingLamports: funding?.lamports || null,
  };
}

async function fetchFundingForensics(holders, rpcUrl) {
  const owners = holders.holderRows
    .filter((holder) => holder.owner)
    .filter((holder, index, all) => all.findIndex((item) => item.owner === holder.owner) === index)
    .slice(0, 5);

  const evidence = [];
  for (const holder of owners) {
    try {
      evidence.push(await inspectWalletFunding(holder.owner, holder.percentage, rpcUrl));
    } catch {
      // Best-effort evidence only.
    }
  }

  const bySource = new Map();
  for (const item of evidence) {
    if (!item.fundingSource) continue;
    const group = bySource.get(item.fundingSource) || [];
    group.push(item);
    bySource.set(item.fundingSource, group);
  }

  const commonFundingClusters = [...bySource.entries()]
    .filter(([, group]) => group.length >= 2)
    .map(([source, group]) => ({
      source,
      wallets: group.map((item) => item.wallet),
      holderSupplyPct: group.reduce((sum, item) => sum + item.holderPercentage, 0),
    }))
    .sort((a, b) => b.wallets.length - a.wallets.length || b.holderSupplyPct - a.holderSupplyPct);

  const timed = evidence.filter((item) => item.fundingAt).sort((a, b) => a.fundingAt - b.fundingAt);
  let synchronizedCluster = null;
  for (let start = 0; start < timed.length; start += 1) {
    const group = timed.filter((item) => item.fundingAt >= timed[start].fundingAt && item.fundingAt - timed[start].fundingAt <= 10 * 60 * 1000);
    if (group.length >= 2 && (!synchronizedCluster || group.length > synchronizedCluster.wallets.length)) {
      const startAt = Math.min(...group.map((item) => item.fundingAt));
      const endAt = Math.max(...group.map((item) => item.fundingAt));
      synchronizedCluster = {
        wallets: group.map((item) => item.wallet),
        holderSupplyPct: group.reduce((sum, item) => sum + item.holderPercentage, 0),
        spreadMinutes: (endAt - startAt) / 60000,
      };
    }
  }

  const linked = new Set(commonFundingClusters.flatMap((cluster) => cluster.wallets));
  return {
    sampledWallets: evidence.length,
    evidence,
    commonFundingClusters,
    synchronizedCluster,
    linkedWalletPct: evidence.length ? (linked.size / evidence.length) * 100 : 0,
    linkedHolderSupplyPct: evidence.filter((item) => linked.has(item.wallet)).reduce((sum, item) => sum + item.holderPercentage, 0),
  };
}

function score(market, holders, forensics, holderError) {
  let risk = 8;
  let confidence = 35;
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
    confidence += 8;
    if (liquidityRatio < 2) add('Liquidity tiny vs cap', `${liquidityRatio.toFixed(2)}% liquidity / market cap`, 24, 'critical');
    else if (liquidityRatio < 5) add('Weak liquidity ratio', `${liquidityRatio.toFixed(2)}% liquidity / market cap`, 14, 'warning');
    else if (liquidityRatio < 10) add('Liquidity ratio needs caution', `${liquidityRatio.toFixed(2)}% liquidity / market cap`, 7, 'warning');
    else add('Visible liquidity ratio is supportive', `${liquidityRatio.toFixed(1)}% liquidity / market cap`, -5, 'positive');
  }

  if (holders) {
    confidence += 24;
    if (holders.top1Pct >= 20) add('Single-account concentration', `Top raw token account: ${holders.top1Pct.toFixed(1)}%`, 22, 'critical');
    else if (holders.top1Pct >= 10) add('Large top holder', `Top raw token account: ${holders.top1Pct.toFixed(1)}%`, 12, 'warning');

    if (holders.top5Pct >= 55) add('Top 5 control most supply', `${holders.top5Pct.toFixed(1)}% combined`, 20, 'critical');
    else if (holders.top5Pct >= 35) add('Concentrated top 5', `${holders.top5Pct.toFixed(1)}% combined`, 10, 'warning');
    else add('Top accounts relatively distributed', `${holders.top5Pct.toFixed(1)}% combined`, -4, 'positive');
  } else {
    add('Holder evidence unavailable', holderError || 'Core holder concentration checks did not run.', 0, 'warning');
  }

  if (forensics?.evidence?.length) confidence += Math.min(10, forensics.evidence.length * 2);

  if (forensics?.commonFundingClusters?.length) {
    const cluster = forensics.commonFundingClusters[0];
    if (cluster.wallets.length >= 3) {
      add('Shared funding cluster', `${cluster.wallets.length} sampled top wallets share one visible first funder; ~${cluster.holderSupplyPct.toFixed(1)}% supply`, 22, 'critical');
    } else {
      add('Possible wallet link', `2 sampled top wallets share one visible first funder; ~${cluster.holderSupplyPct.toFixed(1)}% supply`, 9, 'warning');
    }
  }

  if (forensics?.synchronizedCluster?.wallets?.length >= 3) {
    add('Synchronized funding', `${forensics.synchronizedCluster.wallets.length} sampled wallets funded within ${forensics.synchronizedCluster.spreadMinutes.toFixed(1)}m`, 14, 'warning');
  }

  if (forensics?.linkedWalletPct >= 70) {
    add('High linked-wallet share', `${forensics.linkedWalletPct.toFixed(0)}% of sampled wallets are common-funder linked`, 12, 'critical');
  } else if (forensics?.linkedWalletPct >= 50) {
    add('Linked-wallet share', `${forensics.linkedWalletPct.toFixed(0)}% of sampled wallets are common-funder linked`, 7, 'warning');
  }

  const txns = market.buys1h + market.sells1h;
  if (txns > 0) {
    confidence += 5;
    const buyShare = market.buys1h / txns;
    if (buyShare > 0.92 || buyShare < 0.08) add('Extreme transaction imbalance', `${(buyShare * 100).toFixed(0)}% buys in 1h`, 8, 'warning');
  }

  if (market.liquidityUsd > 0 && market.volume1hUsd / market.liquidityUsd > 15) {
    add('Extreme volume / liquidity', `${(market.volume1hUsd / market.liquidityUsd).toFixed(1)}x in 1h`, 10, 'warning');
  }

  risk = Math.max(0, Math.min(100, Math.round(risk)));
  confidence = Math.max(0, Math.min(100, Math.round(confidence)));
  const posture = risk >= 65
    ? 'SKIP'
    : risk >= 45
      ? 'WAIT'
      : risk >= 25 || confidence < 60
        ? 'WATCH'
        : 'SETUP';
  return { risk, posture, liquidityRatio, confidence, signals };
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

  let forensics = null;
  if (holders) {
    try {
      forensics = await fetchFundingForensics(holders, rpcUrl);
    } catch {
      forensics = null;
    }
  }

  const scored = score(market, holders, forensics, holderError);
  return {
    tokenAddress,
    market,
    holders,
    forensics,
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

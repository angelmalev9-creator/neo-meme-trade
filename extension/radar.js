function n(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function clamp(value, min = 0, max = 100) {
  return Math.min(max, Math.max(min, value));
}

function classifyNarrative(name, symbol, text = '') {
  const source = `${name || ''} ${symbol || ''} ${text || ''}`.toLowerCase();
  const groups = [
    ['celebrity/news', ['trump', 'elon', 'president', 'celeb', 'breaking', 'news', 'viral', 'tweet']],
    ['tech/ai', [' ai ', 'agent', 'robot', 'tech', 'protocol', 'software', 'app', 'gpu']],
    ['animal/meme', ['dog', 'cat', 'frog', 'pepe', 'doge', 'monkey', 'goat', 'meme']],
    ['culture/community', ['cult', 'community', 'culture', 'movement', 'army', 'club']],
    ['art/nft', ['art', 'nft', 'artist', 'pfp', 'collectible']],
  ];
  let best = { category: 'unknown', hits: [] };
  for (const [category, words] of groups) {
    const hits = words.filter((word) => source.includes(word));
    if (hits.length > best.hits.length) best = { category, hits };
  }
  return { category: best.category, matched: best.hits.slice(0, 4) };
}

function socialCount(pair) {
  const socials = Array.isArray(pair?.info?.socials) ? pair.info.socials.length : 0;
  const websites = Array.isArray(pair?.info?.websites) ? pair.info.websites.length : 0;
  return socials + websites;
}

export async function radarAnalyze(tokenAddress, context = {}) {
  const response = await fetch(`https://api.dexscreener.com/token-pairs/v1/solana/${encodeURIComponent(tokenAddress)}`);
  if (!response.ok) throw new Error(`DEX Screener HTTP ${response.status}`);
  const payload = await response.json();
  const pairs = Array.isArray(payload) ? payload : Array.isArray(payload?.pairs) ? payload.pairs : [];
  const pair = pairs
    .filter((item) => item?.chainId === 'solana' && item?.baseToken?.address === tokenAddress)
    .sort((a, b) => n(b?.liquidity?.usd) - n(a?.liquidity?.usd))[0];

  if (!pair) throw new Error('No active Solana base-token pair found.');

  const marketCap = n(pair.marketCap || pair.fdv);
  const liquidity = n(pair?.liquidity?.usd);
  const volume5m = n(pair?.volume?.m5);
  const volume1h = n(pair?.volume?.h1);
  const buys5m = n(pair?.txns?.m5?.buys);
  const sells5m = n(pair?.txns?.m5?.sells);
  const buys1h = n(pair?.txns?.h1?.buys);
  const sells1h = n(pair?.txns?.h1?.sells);
  const price5m = n(pair?.priceChange?.m5);
  const price1h = n(pair?.priceChange?.h1);
  const createdAt = n(pair?.pairCreatedAt);
  const ageMinutes = createdAt ? Math.max(0, (Date.now() - createdAt) / 60000) : 0;
  const liqRatio = marketCap > 0 ? (liquidity / marketCap) * 100 : 0;
  const tx5m = buys5m + sells5m;
  const tx1h = buys1h + sells1h;
  const buyShare5m = tx5m ? buys5m / tx5m : 0.5;
  const buyShare1h = tx1h ? buys1h / tx1h : 0.5;
  const volumeLiq1h = liquidity > 0 ? volume1h / liquidity : 0;
  const links = socialCount(pair);
  const narrative = classifyNarrative(pair?.baseToken?.name, pair?.baseToken?.symbol, context?.text || context?.context || '');

  let risk = 8;
  const reasons = [];
  const positives = [];

  if (liquidity < 5_000) { risk += 27; reasons.push('very thin liquidity'); }
  else if (liquidity < 15_000) { risk += 15; reasons.push('low liquidity'); }
  else if (liquidity >= 50_000) { risk -= 4; positives.push('meaningful visible liquidity'); }

  if (marketCap > 0) {
    if (liqRatio < 2) { risk += 24; reasons.push('liquidity tiny vs market cap'); }
    else if (liqRatio < 5) { risk += 14; reasons.push('weak liquidity ratio'); }
    else if (liqRatio < 10) { risk += 7; reasons.push('liquidity ratio below 10%'); }
    else { risk -= 5; positives.push('supportive liquidity/cap ratio'); }
  }

  if (tx1h > 10 && (buyShare1h > 0.92 || buyShare1h < 0.08)) {
    risk += 9;
    reasons.push('extreme 1h transaction imbalance');
  }
  if (volumeLiq1h > 15) {
    risk += 10;
    reasons.push('extreme 1h volume/liquidity');
  }
  if (ageMinutes > 0 && ageMinutes < 3) {
    risk += 6;
    reasons.push('extremely new pair');
  }

  risk = clamp(Math.round(risk));

  let priority = 42;
  if (liquidity >= 15_000) priority += 8;
  if (liqRatio >= 10) priority += 8;
  if (tx5m >= 20) priority += 8;
  else if (tx5m >= 8) priority += 4;
  if (buyShare5m >= 0.55 && buyShare5m <= 0.78) priority += 10;
  else if (buyShare5m > 0.9 || buyShare5m < 0.25) priority -= 8;
  if (price5m >= 2 && price5m <= 25) priority += 8;
  else if (price5m <= -12) priority -= 10;
  else if (price5m >= 70) priority -= 7;
  if (price1h > -10 && price1h < 80) priority += 4;
  if (volumeLiq1h >= 0.2 && volumeLiq1h <= 8) priority += 6;
  if (links >= 2) priority += 6;
  if (ageMinutes >= 5 && ageMinutes <= 240) priority += 5;
  if (narrative.category !== 'unknown') priority += 3;
  priority -= Math.max(0, risk - 25) * 0.45;
  priority = clamp(Math.round(priority));

  let status = 'IGNORE';
  if (risk >= 70) status = 'HIGH RISK';
  else if (priority >= 70 && risk <= 48) status = 'DEEP CHECK';
  else if (priority >= 56) status = 'WATCH';
  else if (risk >= 50) status = 'CAUTION';

  if (price5m > 2) positives.push(`5m price +${price5m.toFixed(1)}%`);
  if (buyShare5m >= 0.55 && buyShare5m <= 0.8) positives.push(`${Math.round(buyShare5m * 100)}% buys in 5m`);
  if (links >= 2) positives.push(`${links} public project/social links`);

  return {
    tokenAddress,
    name: pair?.baseToken?.name || 'Unknown',
    symbol: pair?.baseToken?.symbol || tokenAddress.slice(0, 5),
    pairAddress: pair?.pairAddress || '',
    priority,
    risk,
    status,
    liquidityUsd: liquidity,
    marketCapUsd: marketCap,
    liquidityRatio: liqRatio,
    volume5mUsd: volume5m,
    volume1hUsd: volume1h,
    tx5m,
    tx1h,
    buyShare5m: buyShare5m * 100,
    buyShare1h: buyShare1h * 100,
    price5m,
    price1h,
    ageMinutes,
    socialCount: links,
    narrative,
    reasons: reasons.slice(0, 4),
    positives: positives.slice(0, 4),
    context: context?.context || '',
    observedAt: Date.now(),
  };
}

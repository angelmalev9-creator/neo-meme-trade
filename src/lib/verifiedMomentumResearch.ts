/**
 * NEO Verified Momentum V1 — evidence-only PAPER research.
 *
 * Deliberately NOT an entry policy, wallet executor, price predictor, or
 * ranking of investment opportunities. Missing checks remain unknown.
 * All fields are derived from the already-loaded read-only market feed.
 */
export const VERIFIED_MOMENTUM_VERSION = 'VERIFIED_MOMENTUM_RESEARCH_V1' as const;

export type ResearchCoin = {
  address: string;
  pairAddress: string;
  symbol: string;
  name: string;
  priceUsd: number;
  liquidityUsd: number;
  marketCap: number;
  fdv: number;
  ageMinutes: number | null;
  updatedAt: number;
  priceChange: { m5?: number; h1?: number };
  txns: { m5?: { buys?: number; sells?: number } };
  sources?: string[];
};

export type ResearchCheckStatus = 'observed' | 'attention' | 'unknown';
export type ResearchCheck = {
  id: 'identity' | 'freshness' | 'market' | 'flow' | 'movement' | 'organic' | 'token_rights' | 'execution';
  title: string;
  status: ResearchCheckStatus;
  description: string;
  evidence: string;
};
export type ResearchFinding = {
  mint: string;
  pair: string;
  symbol: string;
  checks: ResearchCheck[];
  observed: number;
  warnings: number;
  unknown: number;
  state: 'invalid_or_stale' | 'incomplete' | 'requires_independent_verification';
  statusText: string;
};
const ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
function goodNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}
function money(value: number): string {
  return '$' + value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

/**
 * Evaluates evidence availability, NOT the likelihood of an upcoming gain.
 * Checks that require on-chain authority, wallet clustering or an executable
 * two-sided quote CANNOT be inferred from a DexScreener market snapshot.
 */
export function evaluateVerifiedMomentum(coin: ResearchCoin, now = Date.now()): ResearchFinding {
  const identityOk = ADDRESS.test(coin.address) && ADDRESS.test(coin.pairAddress)
    && coin.address !== coin.pairAddress;
  const timeDelta = now - coin.updatedAt;
  const fresh = goodNumber(coin.updatedAt) && coin.updatedAt > 0
    && timeDelta >= -10_000 && timeDelta <= 90_000;
  const priceOk = goodNumber(coin.priceUsd) && coin.priceUsd > 0;
  const liquidityOk = goodNumber(coin.liquidityUsd) && coin.liquidityUsd > 0;
  const buys = coin.txns?.m5?.buys;
  const sells = coin.txns?.m5?.sells;
  const validCounts = goodNumber(buys) && goodNumber(sells) && buys >= 0 && sells >= 0
    && Number.isInteger(buys) && Number.isInteger(sells);
  const flowObserved = validCounts && buys + sells > 0;
  const motion5 = coin.priceChange?.m5;
  const motion60 = coin.priceChange?.h1;
  const motionKnown = goodNumber(motion5) && goodNumber(motion60);
  // Large price swings increase the need for scrutiny; they do NOT
  // establish a trade direction or predict a future return.
  const extremeMotion = motionKnown && (Math.abs(motion5) > 40 || Math.abs(motion60) > 150);
  const checks: ResearchCheck[] = [
    {
      id: 'identity', title: 'Идентичност на пула',
      status: identityOk ? 'observed' : 'attention',
      description: identityOk
        ? 'Налични са синтактично валидни mint и pair адреси. Това не е потвърждение за собственост.'
        : 'Mint/pair идентичността е непълна или невалидна.',
      evidence: 'Адреси от market feed; не е независима on-chain проверка',
    },
    {
      id: 'freshness', title: 'Свежест на snapshot',
      status: fresh ? 'observed' : 'attention',
      description: fresh
        ? 'Пазарният snapshot е от последните 90 секунди.'
        : 'Пазарният snapshot е остарял, липсва или времето е несъгласувано.',
      evidence: 'Timestamp на подадените market данни',
    },
    {
      id: 'market', title: 'Наблюдавана цена и ликвидност',
      status: priceOk && liquidityOk ? 'observed' : 'attention',
      description: priceOk && liquidityOk
        ? 'Наблюдавана ликвидност ' + money(coin.liquidityUsd) + '; LP lock и изпълнима цена не са доказани.'
        : 'Липсва положителна цена или ликвидност в текущия snapshot.',
      evidence: 'Market feed; не е доказателство за заключена ликвидност',
    },
    {
      id: 'flow', title: 'Покупки и продажби за 5 мин.',
      status: flowObserved ? 'observed' : 'unknown',
      description: flowObserved
        ? String(buys) + ' покупки / ' + String(sells) + ' продажби по агрегатора. Уникалността на портфейлите не е проверена.'
        : 'Не е наличен достатъчен запис за реални покупки и продажби.',
      evidence: 'Агрегирани транзакции; възможен wash trading',
    },
    {
      id: 'movement', title: 'Движение на цената',
      status: !motionKnown ? 'unknown' : extremeMotion ? 'attention' : 'observed',
      description: !motionKnown ? 'Липсва сравнима цена за двата периода.'
        : extremeMotion ? 'Наблюдавана е необичайно висока промяна; изисква отделен преглед.'
          : 'Налични са 5-минутна и 1-часова промяна. Не е доказателство за устойчив импулс.',
      evidence: 'Агрегирана пазарна статистика; без прогноза',
    },
    {
      id: 'organic', title: 'Независимост на портфейлите',
      status: 'unknown',
      description: 'Няма потвърден граф на свързани портфейли или независими купувачи. Висок обем не заменя тази проверка.',
      evidence: 'Нужни са on-chain wallet relationships и независим източник',
    },
    {
      id: 'token_rights', title: 'Mint/freeze, създател и LP права',
      status: 'unknown',
      description: 'Тези права и зависимостите между собственици не могат да се потвърдят от текущия market feed.',
      evidence: 'Нужни са Solana RPC и проверен LP/control одит',
    },
    {
      id: 'execution', title: 'Проверима изпълнима котировка',
      status: 'unknown',
      description: 'Няма пресен двупосочен quote и потвърдена себестойност на изпълнението. Market price не е fill price.',
      evidence: 'Нужни са quote данни с точен пул и timestamp',
    },
  ];
  const observed = checks.filter(c => c.status === 'observed').length;
  const warnings = checks.filter(c => c.status === 'attention').length;
  const unknown = checks.filter(c => c.status === 'unknown').length;
  const invalid = !identityOk || !fresh || !priceOk || !liquidityOk;
  return {
    mint: coin.address, pair: coin.pairAddress, symbol: coin.symbol,
    checks, observed, warnings, unknown,
    state: invalid ? 'invalid_or_stale' : unknown > 0 ? 'incomplete' : 'requires_independent_verification',
    statusText: invalid ? 'Сигналът е невалиден или остарял'
      : unknown > 0 ? 'Нужни са независими проверки'
        : 'Изисква проверка на доказателствата',
  };
}

export function summarizeVerifiedMomentum(coins: ResearchCoin[], now = Date.now()) {
  const results = coins.map(coin => evaluateVerifiedMomentum(coin, now));
  const invalidOrStale = results.filter(row => row.state === 'invalid_or_stale').length;
  const missingCritical = results.filter(row =>
    row.checks.some(c => ['organic','token_rights','execution'].includes(c.id) && c.status === 'unknown')).length;
  const activeObservations = results.filter(row =>
    row.checks.find(c => c.id === 'freshness')?.status === 'observed').length;
  return { results, count: results.length, invalidOrStale, missingCritical, activeObservations };
}

/**
 * Early-token PAPER observation cohorts, version 1.
 *
 * Research-only. The tracked result is an observed market-quote PRICE MOVE,
 * not a realizable trade return. No buy/sell or automated strategy rules.
 *
 * Data are sampled strictly after the first fresh observation, without
 * backfilling snapshots, retroactively changing the cohort or filling gaps.
 */
export const EARLY_COHORT_VERSION = 'NEO_EARLY_TOKEN_COHORT_PAPER_V1';
export const EARLY_COHORT_STORAGE_KEY = 'neo-early-paper-research-v1';
export const EARLY_COHORT_CAPACITY = 1000;
export const EARLY_COHORT_WINDOWS = [1, 5, 15] as const;
export type Horizon = '1' | '5' | '15';
export type Category = 'early_activity_unverified' | 'sharp_move_review' | 'insufficient_evidence';

export type CohortCoin = {
  address: string;
  pairAddress: string;
  symbol: string;
  priceUsd: number;
  liquidityUsd: number;
  updatedAt: number;
  ageMinutes: number | null;
  priceChange?: { m5?: number };
  txns?: { m5?: { buys?: number; sells?: number } };
};
export type Outcome =
  | { status: 'observed'; sampleAt: number; sourceAt: number; priceUsd: number; changePct: number }
  | { status: 'missed'; reason: 'no_fresh_snapshot_in_window' | 'baseline_unavailable' };
export type CohortRecord = {
  mint: string; pool: string; key: string; symbol: string;
  firstSeenAt: number; sourceAt: number; sourceAgeSeconds: number;
  ageAtFirstMinutes: number;
  category: Category; categoryEvidence: string[];
  baselinePriceUsd: number | null; baselineLiquidityUsd: number | null;
  baselineBuysM5: number | null; baselineSellsM5: number | null;
  baselineM5ChangePct: number | null;
  outcomes: Partial<Record<Horizon, Outcome>>;
};
export type CohortState = {
  version: typeof EARLY_COHORT_VERSION; startedAt: number;
  records: CohortRecord[]; ignoredDueToCapacity: number;
};
export type HorizonStats = {
  due: number; observed: number; increased: number; unchangedOrDown: number;
  missing: number; pending: number; increasedSharePct: number | null;
  medianObservedChangePct: number | null;
};
export type GroupStats = {
  category: Category; total: number; windows: Record<Horizon,HorizonStats>;
};

const MAX_FIRST_AGE_MINS = 15;
const SOURCE_FRESH_MS = 90_000;
const CAPTURE_GRACE_MS = 90_000;
const TIME_SKEW_MS = 10_000;
const BASE58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const finite = (n:unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const validPrice = (n:unknown): n is number => finite(n) && n > 0;
const validCount = (n:unknown): n is number => finite(n) && n >= 0 && Number.isInteger(n);

function exactKey(c: CohortCoin): string | null {
  return BASE58.test(c.address) && BASE58.test(c.pairAddress) && c.address !== c.pairAddress
    ? c.address + ':' + c.pairAddress : null;
}
function sourceIsFresh(c:CohortCoin, now:number) {
  return finite(c.updatedAt) && c.updatedAt > 0 && c.updatedAt <= now + TIME_SKEW_MS
    && c.updatedAt >= now - SOURCE_FRESH_MS;
}

/** Categories are frozen at the first encounter: no use of later outcomes. */
export function classifyEarlyCohort(c:CohortCoin, now:number): {category:Category,evidence:string[]} {
  const buys=c.txns?.m5?.buys; const sells=c.txns?.m5?.sells;
  const countsKnown=validCount(buys) && validCount(sells);
  const fresh=sourceIsFresh(c,now);
  const validMarket=fresh && validPrice(c.priceUsd) && validPrice(c.liquidityUsd);
  const movement=c.priceChange?.m5;
  const hasSpike=finite(movement) && (movement >= 25 || movement <= -25);
  // A count imbalance is *not* proof of coordinated wallets; this is solely a warning flag.
  const verySkewed=countsKnown && buys+sells >= 20
    && (buys > 5*Math.max(sells,1) || sells > 5*Math.max(buys,1));
  if (!validMarket) return {
    category:'insufficient_evidence',
    evidence:['Няма достатъчно свежа положителна пазарна цена и ликвидност.'],
  };
  if (hasSpike || verySkewed) return {
    category:'sharp_move_review',
    evidence:[
      ...(hasSpike ? ['Рязко 5-минутно движение: '+movement!.toFixed(1)+'%.'] : []),
      ...(verySkewed ? ['Крайно неравномерни агрегирани транзакции.'] : []),
      'Това НЕ доказва измама или невъзможна продажба.',
    ],
  };
  if (countsKnown && buys+sells >= 20) return {
    category:'early_activity_unverified',
    evidence:[
      String(buys)+' покупки и '+String(sells)+' продажби за 5 мин. (агрегирани).',
      'Независимостта на портфейлите и LP контролът НЕ са потвърдени.',
    ],
  };
  return {
    category:'insufficient_evidence',
    evidence:['Не са потвърдени достатъчно пазарни наблюдения за изследователските групи.'],
  };
}
export function newEarlyCohortState(now:number): CohortState {
  return {version:EARLY_COHORT_VERSION,startedAt:now,records:[],ignoredDueToCapacity:0};
}
function validPrior(v:unknown): v is CohortState {
  if (!v || typeof v!=='object') return false;
  const s=v as Partial<CohortState>;
  return s.version===EARLY_COHORT_VERSION && finite(s.startedAt)
    && Array.isArray(s.records) && s.records.length <= EARLY_COHORT_CAPACITY
    && s.records.every(r=>r && typeof r.key==='string'
      && finite(r.firstSeenAt) && r.firstSeenAt > 0
      && ['early_activity_unverified','sharp_move_review','insufficient_evidence'].includes(r.category)
      && r.outcomes && typeof r.outcomes === 'object');
}
export function deserializeEarlyCohorts(raw:string|null,now:number):CohortState {
  if (!raw) return newEarlyCohortState(now);
  try {
    const data=JSON.parse(raw);
    if (!validPrior(data)) return newEarlyCohortState(now);
    // Strip any user-modified unexpected fields and keep the typed record data.
    return {
      version:EARLY_COHORT_VERSION, startedAt:data.startedAt,
      ignoredDueToCapacity:finite(data.ignoredDueToCapacity) ? data.ignoredDueToCapacity : 0,
      records:data.records,
    };
  } catch { return newEarlyCohortState(now); }
}

/**
 * Ingest one feed snapshot. Only samples if:
 *   - the exact mint+pair is still present,
 *   - timestamp is fresh and >= target time,
 *   - arrival is within 90s after the 1/5/15min target.
 * No retroactive reconstruction from 5m change percentages.
 */
export function advanceEarlyCohorts(current:CohortState, feed:CohortCoin[], now:number):CohortState {
  if (!finite(now) || !Array.isArray(feed)) return current;
  const byPool = new Map<string,CohortCoin>();
  for (const coin of feed) {
    if (!coin || typeof coin !== 'object') continue;
    const key=exactKey(coin);
    if (!key) continue;
    const prev=byPool.get(key);
    if (!prev || (coin.updatedAt||0) > (prev.updatedAt||0)) byPool.set(key,coin);
  }
  let changed=false;
  let records=current.records;
  let capacityOverflow=current.ignoredDueToCapacity;
  const known=new Set(current.records.map(r=>r.key));
  // Ingest ALL eligible newborn pairs, never sort by returns, rank or apparent opportunity.
  for (const [key,coin] of byPool) {
    if (known.has(key)) continue;
    if (!finite(coin.ageMinutes) || coin.ageMinutes < 0 || coin.ageMinutes > MAX_FIRST_AGE_MINS) continue;
    known.add(key);
    if (records.length >= EARLY_COHORT_CAPACITY) {
      // Freeze at capacity; do not evict low-return cohorts (survivorship bias).
      capacityOverflow++; changed=true; continue;
    }
    const group=classifyEarlyCohort(coin,now);
    const baseValid=sourceIsFresh(coin,now) && validPrice(coin.priceUsd);
    const buys=coin.txns?.m5?.buys; const sells=coin.txns?.m5?.sells;
    const record:CohortRecord = {
      mint:coin.address,pool:coin.pairAddress,key,symbol:coin.symbol||'—',
      firstSeenAt:now,sourceAt:finite(coin.updatedAt)?coin.updatedAt:0,
      sourceAgeSeconds:finite(coin.updatedAt)?Math.max(0,(now-coin.updatedAt)/1000):0,
      ageAtFirstMinutes:coin.ageMinutes,category:group.category,categoryEvidence:group.evidence,
      baselinePriceUsd:baseValid?coin.priceUsd:null,
      baselineLiquidityUsd:validPrice(coin.liquidityUsd)?coin.liquidityUsd:null,
      baselineBuysM5:validCount(buys)?buys:null,
      baselineSellsM5:validCount(sells)?sells:null,
      baselineM5ChangePct:finite(coin.priceChange?.m5)?coin.priceChange!.m5!:null,
      outcomes:{},
    };
    if(records===current.records)records=[...records];
    records.push(record);changed=true;
  }
  const advanced=records.map(record=>{
    const coin=byPool.get(record.key);
    let outcomes=record.outcomes;
    for (const horizon of EARLY_COHORT_WINDOWS) {
      const h=String(horizon) as Horizon;
      if (outcomes[h]) continue;
      const target=record.firstSeenAt+horizon*60_000;
      if (now<target) continue;
      let result:Outcome|null=null;
      if (!validPrice(record.baselinePriceUsd)) {
        result={status:'missed',reason:'baseline_unavailable'};
      } else if (now <= target + CAPTURE_GRACE_MS && coin && sourceIsFresh(coin,now)
        && validPrice(coin.priceUsd) && coin.updatedAt >= target
        && coin.updatedAt <= target + CAPTURE_GRACE_MS) {
        const change=(coin.priceUsd/record.baselinePriceUsd-1)*100;
        if (Number.isFinite(change)) result={status:'observed',
          sampleAt:now,sourceAt:coin.updatedAt,priceUsd:coin.priceUsd,
          changePct:Math.round(change*10000)/10000};
      } else if (now > target + CAPTURE_GRACE_MS) {
        result={status:'missed',reason:'no_fresh_snapshot_in_window'};
      }
      if (result) {
        if(outcomes===record.outcomes)outcomes={...outcomes};
        outcomes[h]=result;changed=true;
      }
    }
    return outcomes===record.outcomes?record:{...record,outcomes};
  });
  return changed ? {...current,records:advanced,ignoredDueToCapacity:capacityOverflow} : current;
}
function median(values:number[]):number|null {
  if(!values.length)return null;
  const sorted=[...values].sort((a,b)=>a-b),m=Math.floor(sorted.length/2);
  return Math.round((sorted.length%2 ? sorted[m] : (sorted[m-1]+sorted[m])/2)*100)/100;
}
export const CATEGORY_LABELS:Record<Category,string>={
  early_activity_unverified:'Ранна активност · независимостта не е проверена',
  sharp_move_review:'Рязко движение · нужда от проверка',
  insufficient_evidence:'Недостатъчни доказателства',
};
export function summarizeEarlyCohorts(state:CohortState,now:number):GroupStats[] {
  const categories=Object.keys(CATEGORY_LABELS) as Category[];
  return categories.map(category=>{
    const records=state.records.filter(r=>r.category===category);
    const windows={} as Record<Horizon,HorizonStats>;
    for(const minutes of EARLY_COHORT_WINDOWS){
      const h=String(minutes) as Horizon;
      const due=records.filter(r=>now>=r.firstSeenAt+minutes*60_000);
      const recorded=due.map(r=>r.outcomes[h]).filter((o):o is Outcome=>!!o);
      const observed=recorded.filter((o):o is Extract<Outcome,{status:'observed'}>=>o.status==='observed');
      const increased=observed.filter(o=>o.changePct>0).length;
      windows[h]={
        due:due.length,observed:observed.length,increased,
        unchangedOrDown:observed.length-increased,
        missing:recorded.filter(o=>o.status==='missed').length,
        pending:due.length-recorded.length,
        increasedSharePct:observed.length ? Math.round(10000*increased/observed.length)/100 : null,
        medianObservedChangePct:median(observed.map(o=>o.changePct)),
      };
    }
    return {category,total:records.length,windows};
  });
}

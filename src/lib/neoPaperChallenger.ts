/**
 * NEO PAPER Challenger / historical counterfactual evidence audit.
 *
 * Inspired by open-source patterns from Freqtrade (separated backtesting),
 * Solana screener designs (independent risk gates) and quote realism reviews.
 * Original implementation. NO purchase/sale recommendation, orders, keys,
 * wallet access, live execution or claim of backtest profitability.
 *
 * Important: these are FILTERED OUTCOMES FROM ALREADY OBSERVED PAPER TRADES,
 * not realizable counterfactual fills, a new strategy, or a forward test.
 */
export const PAPER_CHALLENGER_VERSION = 'PAPER_REPO_PATTERN_AUDIT_V1';

export type PaperAuditTrade = {
  id?: string; symbol?: string; address?: string; pairAddress?: string;
  session_id?: string; opened_at?: number; closed_at?: number;
  pnl_usd?: number; pnl_pct?: number; technical_voided?: boolean;
  entry_roundtrip_pnl_pct?: number;
  entry_worst_case_roundtrip_pnl_pct?: number;
  entry_price_impact_pct?: number;
  price_crosscheck?: { status?: string } | null;
};
export type Mode = 'recorded' | 'cost_evidence' | 'cost_and_pool';
export type Snapshot = {
  mode: Mode; considered: number; accepted: number; rejected: number;
  removed_wins: number; removed_losses: number; accepted_wins: number;
  accepted_losses: number; accepted_net_usd: number;
  accepted_win_pct: number | null; average_win_usd: number | null;
  average_loss_usd: number | null; reasons: Record<string, number>;
  warnings: string[];
};
export type PaperComparison = {
  version: string; mode: 'READ_ONLY_PAPER';
  sample_total: number; evidence_missing: number;
  unverified_price_crosschecks: number;
  results: Snapshot[];
  caveat: string;
};

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}
function valid(t: PaperAuditTrade): boolean {
  return !!t && !t.technical_voided && finite(t.opened_at) && finite(t.closed_at)
    && t.opened_at > 0 && t.closed_at >= t.opened_at
    && finite(t.pnl_usd) && finite(t.pnl_pct);
}
function stamp(t: PaperAuditTrade): number {
  return t.opened_at || 0;
}
function close(t: PaperAuditTrade): number {
  return t.closed_at || 0;
}
function exactPool(t: PaperAuditTrade): string | null {
  const mint = t.address?.trim();
  const pool = t.pairAddress?.trim();
  return mint && pool ? mint + ':' + pool : null;
}
const HOUR = 3_600_000;
const LOOKBACK = 6 * HOUR;
const POOL_PAUSE = 3 * HOUR;

/** Ex ante evidence attached to the candidate at its original PAPER entry. */
export function entryEvidenceReasons(t: PaperAuditTrade): string[] {
  const reasons: string[] = [];
  const cost = t.entry_roundtrip_pnl_pct;
  const worst = t.entry_worst_case_roundtrip_pnl_pct;
  const impact = t.entry_price_impact_pct;
  if (!finite(cost) || !finite(worst) || !finite(impact)) {
    reasons.push('missing_entry_quote_evidence');
  } else {
    // Thresholds are research hypotheses, not recommended order parameters.
    if (cost > 0 || cost < -1.5) reasons.push('modeled_roundtrip_cost');
    if (worst > 0 || worst < -3.0) reasons.push('modeled_worst_case_cost');
    if (impact < 0 || impact > 1.0) reasons.push('modeled_entry_impact');
  }
  if (t.price_crosscheck?.status === 'blocked') {
    reasons.push('independent_price_disagreement');
  }
  return reasons;
}

type ClosedPoolState = { streak: number; lastLossClosedAt: number; blockedUntil: number };
export function comparePaperChallenger(history: PaperAuditTrade[], maxCount = 100): PaperComparison {
  if (!Array.isArray(history)) throw new TypeError('history must be an array');
  if (!Number.isSafeInteger(maxCount) || maxCount < 1 || maxCount > 5000)
    throw new RangeError('maxCount must be 1..5000');
  const latest = history.filter(valid).sort((a,b) => close(b) - close(a))
    .slice(0,maxCount);
  // Sorting by entry rather than exit prevents future trade outcomes from
  // influencing a past candidate's eligibility.
  const chronological = [...latest].sort((a,b) => stamp(a) - stamp(b)
    || close(a) - close(b) || (a.id || '').localeCompare(b.id || ''));
  const evidence_missing = latest.filter(t => {
    return !finite(t.entry_roundtrip_pnl_pct) ||
      !finite(t.entry_price_impact_pct) ||
      !finite(t.entry_worst_case_roundtrip_pnl_pct);
  }).length;
  const unverified_price_crosschecks = latest.filter(
    t => t.price_crosscheck?.status !== 'pass',
  ).length;
  const modes: Mode[] = ['recorded','cost_evidence','cost_and_pool'];
  const results = modes.map(mode => {
    const reasons: Record<string, number> = {};
    const accepted: PaperAuditTrade[] = [];
    const removed: PaperAuditTrade[] = [];
    const pending: PaperAuditTrade[] = [];
    const pools = new Map<string, ClosedPoolState>();

    function settle(upTo: number) {
      const ready = pending.filter(t => close(t) <= upTo)
        .sort((a,b) => close(a) - close(b) || (a.id || '').localeCompare(b.id || ''));
      for (const row of ready) {
        const pool = exactPool(row);
        if (!pool) continue;
        const existing = pools.get(pool) ||
          { streak: 0, lastLossClosedAt: 0, blockedUntil: 0 };
        if ((row.pnl_usd || 0) < 0) {
          const inWindow = existing.lastLossClosedAt > 0
            && close(row) - existing.lastLossClosedAt <= LOOKBACK;
          existing.streak = inWindow ? existing.streak + 1 : 1;
          existing.lastLossClosedAt = close(row);
          if (existing.streak >= 2) {
            // Once activated, expiry is durable even when the first loss
            // ages out of the rolling observation window.
            existing.blockedUntil = Math.max(existing.blockedUntil,
              close(row) + POOL_PAUSE);
          }
        } else if ((row.pnl_usd || 0) > 0) {
          existing.streak = 0;
          existing.lastLossClosedAt = 0;
        }
        pools.set(pool,existing);
      }
      for (const row of ready) {
        const i = pending.indexOf(row);
        if (i >= 0) pending.splice(i,1);
      }
    }
    for (const t of chronological) {
      settle(stamp(t));
      const vetoes = mode === 'recorded' ? [] : entryEvidenceReasons(t);
      if (mode === 'cost_and_pool') {
        const pool = exactPool(t);
        if (!pool) vetoes.push('missing_exact_pool');
        else if ((pools.get(pool)?.blockedUntil || 0) > stamp(t)) {
          vetoes.push('previous_verified_paper_pool_losses');
        }
      }
      if (vetoes.length) {
        removed.push(t);
        for (const code of vetoes) reasons[code] = (reasons[code] || 0) + 1;
      } else {
        accepted.push(t);
        pending.push(t);
      }
    }
    const winners = accepted.filter(t => (t.pnl_usd || 0) > 0);
    const losers = accepted.filter(t => (t.pnl_usd || 0) < 0);
    const lostWinners = removed.filter(t => (t.pnl_usd || 0) > 0);
    const lostLosers = removed.filter(t => (t.pnl_usd || 0) < 0);
    const mean = (items:PaperAuditTrade[]) => items.length
      ? items.reduce((total,t) => total + (t.pnl_usd || 0),0) / items.length : null;
    return {
      mode, considered: chronological.length, accepted: accepted.length,
      rejected: removed.length, removed_wins: lostWinners.length,
      removed_losses: lostLosers.length, accepted_wins: winners.length,
      accepted_losses: losers.length,
      accepted_net_usd: Math.round(accepted.reduce((sum,t) =>
        sum + (t.pnl_usd || 0),0) * 100) / 100,
      accepted_win_pct: accepted.length
        ? Math.round(10000 * winners.length / accepted.length) / 100 : null,
      average_win_usd: mean(winners) === null ? null
        : Math.round((mean(winners) as number) * 100) / 100,
      average_loss_usd: mean(losers) === null ? null
        : Math.round((mean(losers) as number) * 100) / 100,
      reasons,
      warnings: mode === 'recorded' ? ['Original closed-trade sample.']
        : ['Subset of recorded trades only. Skipped orders change later opportunities, so filtered PnL is NOT a valid forward profit forecast.'],
    };
  });
  return {
    version:PAPER_CHALLENGER_VERSION, mode:'READ_ONLY_PAPER',
    sample_total:latest.length, evidence_missing,
    unverified_price_crosschecks,
    results,
    caveat:'Historical selection analysis, not an executable strategy, walk-forward backtest, live-fill simulation or profit guarantee. No orders are created.',
  };
}

/**
 * EARLY_OBSERVED_TARGETS_PAPER_V1
 *
 * Comparison of two hypothetical percentage targets against genuinely observed
 * 1/5/15 minute price snapshots from the existing NEO Early Cohort research.
 * Read-only research, not an entry strategy, fill, sell signal or financial return.
 * The candidate is NEVER marked as active by this module.
 */
import { EARLY_COHORT_WINDOWS, type CohortState, type Horizon } from './earlyCohortResearch';

export const EARLY_PAPER_CANDIDATE_VERSION = 'EARLY_OBSERVED_TARGETS_PAPER_V1';
export const OBSERVED_MARK_TARGETS = [5, 10] as const;
export type MarkTarget = typeof OBSERVED_MARK_TARGETS[number];

export type MarkMetric = {
  horizon: Horizon;
  targetPct: MarkTarget;
  due: number;
  observed: number;
  observedAtOrAbove: number;
  observedBelow: number;
  missing: number;
  pending: number;
  observedSharePct: number | null;
  // Counts are historical *observed chart marks*, not paper or live trade wins.
};
export type CandidateEvaluation = {
  version: typeof EARLY_PAPER_CANDIDATE_VERSION;
  active: false;
  execution: 'NONE';
  assumptions: 'MARK_PRICE_ONLY_NO_EXIT_QUOTE';
  newPoolsObserved: number;
  eligibleFirstMarks: number;
  missingFirstMarks: number;
  metrics: MarkMetric[];
};

export function evaluateEarlyPaperCandidate(study:CohortState,now=Date.now()):CandidateEvaluation {
  const records = Array.isArray(study?.records) ? study.records : [];
  const eligible = records.filter(r=>typeof r.baselinePriceUsd==='number'
    && Number.isFinite(r.baselinePriceUsd) && r.baselinePriceUsd>0);
  const metrics:MarkMetric[] = [];
  for(const min of EARLY_COHORT_WINDOWS) {
    const horizon=String(min) as Horizon;
    const due=eligible.filter(r=>r.firstSeenAt+min*60_000<=now);
    const observed=due
      .map(r=>r.outcomes[horizon])
      .filter((r):r is Extract<NonNullable<typeof r>,{status:'observed'}>=>
        r?.status==='observed' && typeof r.changePct==='number' && Number.isFinite(r.changePct));
    const missing=due.filter(r=>r.outcomes[horizon]?.status==='missed').length;
    for(const targetPct of OBSERVED_MARK_TARGETS) {
      const hit=observed.filter(r=>r.changePct>=targetPct).length;
      metrics.push({
        horizon,targetPct,due:due.length,observed:observed.length,
        observedAtOrAbove:hit,observedBelow:observed.length-hit,
        missing,pending:due.length-observed.length-missing,
        observedSharePct:observed.length?Math.round(10_000*hit/observed.length)/100:null,
      });
    }
  }
  return {
    version:EARLY_PAPER_CANDIDATE_VERSION,active:false,execution:'NONE',
    assumptions:'MARK_PRICE_ONLY_NO_EXIT_QUOTE',
    newPoolsObserved:records.length,eligibleFirstMarks:eligible.length,
    missingFirstMarks:records.length-eligible.length,metrics,
  };
}

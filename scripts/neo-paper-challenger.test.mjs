import test from 'node:test';
import assert from 'node:assert/strict';
import {
  comparePaperChallenger,
  entryEvidenceReasons,
  PAPER_CHALLENGER_VERSION,
} from '../src/lib/neoPaperChallenger.ts';

const T = 1_800_000_000_000;
const H = 3600000;
function make(at, pnl, extras = {}) {
  return {
    id: String(at), address: 'EXACT_MINT', pairAddress: 'EXACT_POOL',
    symbol: 'DEMO', opened_at: at, closed_at: at + 1000,
    pnl_usd: pnl, pnl_pct: pnl, entry_roundtrip_pnl_pct: -1,
    entry_worst_case_roundtrip_pnl_pct: -2.1, entry_price_impact_pct: 0.3,
    price_crosscheck: {status:'pass'}, ...extras,
  };
}
function mode(rows,name) {
  return comparePaperChallenger(rows).results.find(row=>row.mode === name);
}

test('empty and invalid inputs do not fabricate winners',()=>{
  assert.equal(PAPER_CHALLENGER_VERSION,'PAPER_REPO_PATTERN_AUDIT_V1');
  const data=comparePaperChallenger([make(T,10,{technical_voided:true}),{pnl_usd:500}]);
  assert.equal(data.sample_total,0);
  assert.equal(data.results[0].accepted_win_pct,null);
  assert.equal(data.results[0].accepted_net_usd,0);
});
test('original PAPER PnL is unchanged and no simulated exits created',()=>{
  const rows=[make(T,-5),make(T+10_000,8)];
  const snapshot=JSON.stringify(rows);
  const report=comparePaperChallenger(rows);
  assert.equal(report.results[0].accepted_net_usd,3);
  assert.equal(report.results[0].accepted_wins,1);
  assert.equal(report.results[0].accepted_losses,1);
  assert.equal(JSON.stringify(rows),snapshot);
  assert.equal(report.mode,'READ_ONLY_PAPER');
});
test('cost filter admits valid evidence but reports rejected winners and losses',()=>{
  const rows=[make(T,10,{entry_roundtrip_pnl_pct:-2.3}),
    make(T+10000,-4,{entry_price_impact_pct:2.5}),make(T+20000,-1)];
  const filtered=mode(rows,'cost_evidence');
  assert.equal(filtered.accepted,1);
  assert.equal(filtered.rejected,2);
  assert.equal(filtered.removed_wins,1);
  assert.equal(filtered.removed_losses,1);
  assert.equal(filtered.accepted_net_usd,-1);
  assert.equal(filtered.reasons.modeled_roundtrip_cost,1);
  assert.equal(filtered.reasons.modeled_entry_impact,1);
});
test('missing or blocked quote evidence fails closed in research',()=>{
  assert.ok(entryEvidenceReasons(make(T,-2,{entry_roundtrip_pnl_pct:undefined}))
    .includes('missing_entry_quote_evidence'));
  assert.ok(entryEvidenceReasons(make(T,-2,{price_crosscheck:{status:'blocked'}}))
    .includes('independent_price_disagreement'));
});
test('two accepted PAPER losses block subsequent same-pool entries for full 3h',()=>{
  const first=make(T,-2);
  const second=make(T+5.8*H,-3);
  const blocked=make(T+6.1*H,9);
  const other=make(T+6.2*H,2,{pairAddress:'OTHER_POOL'});
  const later=make(T+9*H,1);
  const result=mode([first,second,blocked,other,later],'cost_and_pool');
  assert.equal(result.accepted,4);
  assert.equal(result.rejected,1);
  assert.equal(result.removed_wins,1);
  assert.equal(result.reasons.previous_verified_paper_pool_losses,1);
});
test('outcomes from positions that are still open cannot veto earlier entries',()=>{
  const first=make(T,-2,{closed_at:T+2*H});
  const second=make(T+10_000,-2,{closed_at:T+3*H});
  const earlier=make(T+20_000,9,{closed_at:T+4*H});
  const report=mode([first,second,earlier],'cost_and_pool');
  assert.equal(report.accepted,3);
  assert.equal(report.rejected,0);
});
test('rejected observed trade does not create a fake future pool loss',()=>{
  const rejected=make(T,-4,{entry_roundtrip_pnl_pct:-5});
  const firstEligible=make(T+10000,-2);
  const secondEligible=make(T+20000,4);
  const report=mode([rejected,firstEligible,secondEligible],'cost_and_pool');
  assert.equal(report.accepted,2);
  assert.equal(report.removed_losses,1);
});
test('crosscheck unknown stays visibly unverified, not falsely passed',()=>{
  const report=comparePaperChallenger([make(T,-1,{price_crosscheck:undefined})]);
  assert.equal(report.unverified_price_crosschecks,1);
  assert.equal(report.results[1].accepted,1);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  advanceEarlyCohorts, classifyEarlyCohort, deserializeEarlyCohorts,
  newEarlyCohortState, summarizeEarlyCohorts,
} from '../src/lib/earlyCohortResearch.ts';

const T=1_800_000_000_000;
const seed={
  address:'So11111111111111111111111111111111111111112',
  pairAddress:'11111111111111111111111111111112',
  symbol:'SYNTH',ageMinutes:2,
  updatedAt:T-500,priceUsd:1,liquidityUsd:50000,
  priceChange:{m5:4},txns:{m5:{buys:30,sells:10}},
};
const start=(feed=[seed])=>advanceEarlyCohorts(newEarlyCohortState(T),feed,T);

test('baseline sample and initial cohort are frozen before outcomes',()=>{
  const s=start();
  assert.equal(s.records.length,1);
  assert.equal(s.records[0].category,'early_activity_unverified');
  assert.equal(s.records[0].baselinePriceUsd,1);
  assert.equal(s.records[0].outcomes['1'],undefined);
  const after=advanceEarlyCohorts(s,[{...seed,updatedAt:T+60000,
    priceUsd:2,priceChange:{m5:80}}],T+60000);
  assert.equal(after.records[0].category,'early_activity_unverified');
  assert.equal(after.records[0].outcomes['1']?.status,'observed');
  assert.equal(after.records[0].outcomes['1']?.changePct,100);
});

test('1 / 5 / 15 minute observations use actual follow-up source timestamps',()=>{
  let s=start();
  for(const [min,price] of [[1,1.04],[5,0.92],[15,1.15]]){
    s=advanceEarlyCohorts(s,[{...seed,updatedAt:T+min*60000,priceUsd:price}],T+min*60000);
  }
  assert.equal(s.records[0].outcomes['1']?.changePct,4);
  assert.equal(s.records[0].outcomes['5']?.changePct,-8);
  assert.equal(s.records[0].outcomes['15']?.changePct,15);
  const stats=summarizeEarlyCohorts(s,T+16*60000);
  const a=stats.find(x=>x.category==='early_activity_unverified');
  assert.deepEqual([a.windows['1'].increased,a.windows['5'].increased,a.windows['15'].increased],[1,0,1]);
});

test('stale or unchanged quote is not backfilled into the next milestone',()=>{
  let s=start();
  s=advanceEarlyCohorts(s,[{...seed,updatedAt:T-500,priceUsd:1}],T+60000);
  assert.equal(s.records[0].outcomes['1'],undefined);
  s=advanceEarlyCohorts(s,[{...seed,updatedAt:T+151000,priceUsd:1.25}],T+151000);
  assert.deepEqual(s.records[0].outcomes['1'],{
    status:'missed',reason:'no_fresh_snapshot_in_window',
  });
});

test('missing baseline is reported missing, never a zero-price gain',()=>{
  let s=start([{...seed,updatedAt:T-300000,priceUsd:1}]);
  assert.equal(s.records[0].category,'insufficient_evidence');
  assert.equal(s.records[0].baselinePriceUsd,null);
  s=advanceEarlyCohorts(s,[{...seed,updatedAt:T+60000,priceUsd:10}],T+60000);
  assert.equal(s.records[0].outcomes['1']?.status,'missed');
});

test('sharp pump and skew only trigger review, not a scam proof',()=>{
  const r=classifyEarlyCohort({...seed,priceChange:{m5:30}},T);
  assert.equal(r.category,'sharp_move_review');
  assert.match(r.evidence.join(' '),/НЕ доказва измама/);
});

test('later-captured token is not falsely included in a newborn cohort',()=>{
  const s=start([{...seed,ageMinutes:24}]);
  assert.equal(s.records.length,0);
});

test('malformed storage resets without manipulating observed results',()=>{
  assert.equal(deserializeEarlyCohorts('{invalid',T).records.length,0);
  const s=start();
  const back=deserializeEarlyCohorts(JSON.stringify(s),T+100);
  assert.deepEqual(back.records,s.records);
});

test('missing and increased prices are separate in category statistics',()=>{
  let s=start();
  s=advanceEarlyCohorts(s,[],T+3*60000);
  const g=summarizeEarlyCohorts(s,T+3*60000).find(x=>x.category==='early_activity_unverified');
  assert.equal(g.windows['1'].due,1);
  assert.equal(g.windows['1'].missing,1);
  assert.equal(g.windows['1'].increased,0);
  assert.equal(g.windows['1'].increasedSharePct,null);
});

test('multiple exact pools are tracked independently, no duplicate mint/pair cohort',()=>{
  const second={...seed,pairAddress:'11111111111111111111111111111113',symbol:'ALT'};
  let s=start([seed,second,seed]);
  assert.equal(s.records.length,2);
  s=advanceEarlyCohorts(s,[second,seed],T+1000);
  assert.equal(s.records.length,2);
});

test('observations never produce entries or executable trade claims',()=>{
  const output=start().records[0];
  assert.equal('buy' in output,false);
  assert.equal('sell' in output,false);
  assert.equal('executableQuote' in output,false);
});

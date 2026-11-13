import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateEarlyPaperCandidate } from '../src/lib/earlyPaperCandidate.ts';
import { newEarlyCohortState, advanceEarlyCohorts } from '../src/lib/earlyCohortResearch.ts';

const NOW=1_800_000_000_000;
const coin={address:'So11111111111111111111111111111111111111112',
  pairAddress:'11111111111111111111111111111112',
  symbol:'SYNTH',priceUsd:1,liquidityUsd:20000,updatedAt:NOW-1000,
  ageMinutes:1,txns:{m5:{buys:25,sells:10}},priceChange:{m5:4}};
const first=()=>advanceEarlyCohorts(newEarlyCohortState(NOW),[coin],NOW);
const snapshot=(min,price)=>({...coin,priceUsd:price,updatedAt:NOW+min*60000});

test('no observations mean no claims of target hits, profit or fills',()=>{
 const r=evaluateEarlyPaperCandidate(newEarlyCohortState(NOW),NOW);
 assert.equal(r.active,false);
 assert.equal(r.execution,'NONE');
 assert.equal(r.newPoolsObserved,0);
 assert.equal(r.metrics.length,6);
 assert.ok(r.metrics.every(m=>m.observedSharePct===null));
});
test('5% market-price touch is not mistaken for 10% touch',()=>{
 let state=first();
 state=advanceEarlyCohorts(state,[snapshot(1,1.06)],NOW+60000);
 const r=evaluateEarlyPaperCandidate(state,NOW+60000);
 const five=r.metrics.find(m=>m.horizon==='1'&&m.targetPct===5);
 const ten=r.metrics.find(m=>m.horizon==='1'&&m.targetPct===10);
 assert.equal(five.observedAtOrAbove,1);
 assert.equal(ten.observedAtOrAbove,0);
 assert.equal(five.observedSharePct,100);
 assert.equal(ten.observedSharePct,0);
});
test('missing follow-up prices remain unobserved and cannot inflate hit rate',()=>{
 let state=first();
 state=advanceEarlyCohorts(state,[],NOW+200000);
 const r=evaluateEarlyPaperCandidate(state,NOW+200000);
 const five=r.metrics.find(m=>m.horizon==='1'&&m.targetPct===5);
 assert.equal(five.observed,0);
 assert.equal(five.missing,1);
 assert.equal(five.observedSharePct,null);
});
test('exact 10% threshold counts only from actual after-first observation',()=>{
 let state=first();
 state=advanceEarlyCohorts(state,[snapshot(5,1.1)],NOW+5*60000);
 const r=evaluateEarlyPaperCandidate(state,NOW+5*60000);
 const five=r.metrics.find(m=>m.horizon==='5'&&m.targetPct===5);
 const ten=r.metrics.find(m=>m.horizon==='5'&&m.targetPct===10);
 assert.equal(five.observedAtOrAbove,1);
 assert.equal(ten.observedAtOrAbove,1);
 assert.equal(r.active,false);
});

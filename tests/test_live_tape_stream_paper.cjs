'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const tape=require('../backend/live_tape_stream_paper.cjs');

const t=Date.now();
const meta={pair:'POOL_A',mint:'MINT_A',symbol:'A',price:0.001};
const trade={
  pool:'POOL_A',mint:'MINT_A',is_buy:true,signature:'X'.repeat(88),
  wallet:'Y'.repeat(44),token_amount:200,quote_amount:0.2,
  timestamp:Math.floor(t/1000),
};
test('GOLD candidates remain observable beyond the young-pool age limit',()=>{
  assert.equal(tape.eligible({pairAddress:'p',address:'a',priceUsd:1,score:90,liquidityUsd:20000,ageMinutes:999,priceChange:{m5:2}}),true);
});
test('GOLD prefilter priority beats young less-qualified pools',()=>{
  const gold={score:90,liquidityUsd:20000,priceChange:{m5:2},txns:{m5:{buys:4,sells:3}}};
  const weak={score:40,liquidityUsd:6000,priceChange:{m5:2},txns:{m5:{buys:1000,sells:1}}};
  assert.ok(tape.rank(gold)>tape.rank(weak));
});
test('accepts real events with exact pool, wallet, mint and in-window timestamp',()=>{
 const r=tape.convertTrade(trade,meta,t);
 assert.equal(r.direction,'BUY');
 assert.equal(r.pairAddress,'POOL_A');
 assert.equal(r.usd_amount,0.2);
 assert.equal(r.confirmed_swap,false);
 assert.equal(r.available_at,undefined);
});
test('rejects mismatched mint, pool, stale and invalid trade amounts',()=>{
 assert.equal(tape.convertTrade({...trade,mint:'FAKE'},meta,t),null);
 assert.equal(tape.convertTrade({...trade,pool:'OTHER'},meta,t),null);
 assert.equal(tape.convertTrade({...trade,token_amount:0},meta,t),null);
 assert.equal(tape.convertTrade({...trade,timestamp:Math.floor((t-90000)/1000)},meta,t),null);
});
test('only independently confirmed or finalized successful signatures count',()=>{
 assert.equal(tape.confirmResult({err:null,confirmationStatus:'confirmed'}),true);
 assert.equal(tape.confirmResult({err:null,confirmationStatus:'finalized'}),true);
 assert.equal(tape.confirmResult({err:null,confirmationStatus:'processed'}),false);
 assert.equal(tape.confirmResult({err:{code:1},confirmationStatus:'finalized'}),false);
 assert.equal(tape.confirmResult(null),false);
});

test('stable observation retains active pools despite scanner ranking rotation',()=>{
 const current=new Map([
  ['new1',{pairAddress:'new1',score:50,liquidityUsd:6000,priceChange:{m5:2},txns:{m5:{buys:100,sells:1}}}],
  ['new2',{pairAddress:'new2',score:40,liquidityUsd:6000,priceChange:{m5:2},txns:{m5:{buys:100,sells:2}}}]
 ]);
 const old=new Map([
  ['old1',{lastEligible:10_000,snapshot:{pairAddress:'old1',score:50}}],
  ['old2',{lastEligible:10_000,snapshot:{pairAddress:'old2',score:45}}]
 ]);
 const chosen=tape.chooseStableCandidates(current,old,15_000,2);
 assert.deepEqual([...chosen.keys()],['old1','old2']);
});
test('new real GOLD candidate gets priority over sticky prewarm',()=>{
 const current=new Map([['gold',{pairAddress:'gold',score:95,liquidityUsd:60000,priceChange:{m5:2}}]]);
 const old=new Map([['old',{lastEligible:10_000,snapshot:{pairAddress:'old',score:55}}]]);
 const chosen=tape.chooseStableCandidates(current,old,15_000,1);
 assert.deepEqual([...chosen.keys()],['gold']);
});

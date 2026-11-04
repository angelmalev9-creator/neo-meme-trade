'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const mod=require('../backend/solana_wide_discovery_paper.cjs');
test('reject non-Solana and non-discovery events',()=>{
  assert.equal(mod.observe({action:'buy',mint:'not-a-solana-address'},Date.now()),false);
  assert.equal(mod.observe({action:'transfer',mint:'So11111111111111111111111111111111111111112'},Date.now()),false);
});
test('only stores mint-level counts (no wallets, transaction execution)',()=>{
  const mint='So11111111111111111111111111111111111111112';
  assert.equal(mod.observe({action:'create',mint,txSigner:'private-wallet'},Date.now()),true);
  assert.equal(mod.observe({action:'buy',mint,txSigner:'private-wallet'},Date.now()),true);
  const s=mod.snapshot();
  const row=s.tokens.find(x=>x.address===mint);
  assert.ok(row);
  assert.equal(row.events,2);
  assert.equal(row.created,1);
  assert.equal(row.buys,1);
  assert.equal(row.txSigner,undefined);
  assert.equal(s.mode,'READ_ONLY_PAPER_DISCOVERY');
});

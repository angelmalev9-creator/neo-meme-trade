import test from 'node:test';
import assert from 'node:assert/strict';
import {
  VERIFIED_MOMENTUM_VERSION,
  evaluateVerifiedMomentum,
  summarizeVerifiedMomentum,
} from '../src/lib/verifiedMomentumResearch.ts';

// Fixtures use synthetic token addresses and synthetic market observations.
// This test never sends HTTP requests, places orders, or handles credentials.
const stamp = 1_800_000_000_000;
const sample = {
  address: 'So11111111111111111111111111111111111111112',
  pairAddress: '11111111111111111111111111111112',
  symbol: 'TEST',
  name: 'Synthetic coin',
  priceUsd: 0.000001,
  liquidityUsd: 50_000,
  marketCap: 100_000,
  fdv: 100_000,
  ageMinutes: 25,
  updatedAt: stamp - 5_000,
  priceChange: { m5: 4, h1: 8 },
  txns: { m5: { buys: 18, sells: 12 } },
  sources: ['synthetic-market-feed'],
};

test('version and stable evidence contract', () => {
  assert.equal(VERIFIED_MOMENTUM_VERSION, 'VERIFIED_MOMENTUM_RESEARCH_V1');
  const result = evaluateVerifiedMomentum(sample, stamp);
  assert.equal(result.checks.length, 8);
  assert.equal(result.checks.filter(row => row.status === 'unknown').length, 3);
  assert.equal(result.checks.find(row => row.id === 'organic')?.status, 'unknown');
  assert.equal(result.checks.find(row => row.id === 'token_rights')?.status, 'unknown');
  assert.equal(result.checks.find(row => row.id === 'execution')?.status, 'unknown');
  assert.equal(result.state, 'incomplete');
  assert.equal('buySignal' in result, false);
  assert.equal('entryPrice' in result, false);
});

test('stale market snapshots cannot appear fresh', () => {
  const result = evaluateVerifiedMomentum({ ...sample, updatedAt: stamp - 120_000 }, stamp);
  assert.equal(result.state, 'invalid_or_stale');
  assert.equal(result.checks.find(row => row.id === 'freshness')?.status, 'attention');
});

test('future-dated feed is not silently accepted', () => {
  const result = evaluateVerifiedMomentum({ ...sample, updatedAt: stamp + 180_000 }, stamp);
  assert.equal(result.state, 'invalid_or_stale');
});

test('missing/invalid price and liquidity are not accepted as complete', () => {
  const result = evaluateVerifiedMomentum({ ...sample, priceUsd: 0, liquidityUsd: Number.NaN }, stamp);
  assert.equal(result.state, 'invalid_or_stale');
  assert.equal(result.checks.find(row => row.id === 'market')?.status, 'attention');
});

test('aggregated transaction counts never prove organic wallets', () => {
  const result = evaluateVerifiedMomentum({ ...sample, txns: { m5: { buys: 10_000, sells: 1 } } }, stamp);
  assert.equal(result.checks.find(row => row.id === 'organic')?.status, 'unknown');
  assert.equal(result.checks.find(row => row.id === 'flow')?.status, 'observed');
});

test('zero or invalid transaction counts do not fabricate activity', () => {
  const empty = evaluateVerifiedMomentum({ ...sample, txns: { m5: { buys: 0, sells: 0 } } }, stamp);
  assert.equal(empty.checks.find(row => row.id === 'flow')?.status, 'unknown');
  const invalid = evaluateVerifiedMomentum({ ...sample, txns: { m5: { buys: -1, sells: 3 } } }, stamp);
  assert.equal(invalid.checks.find(row => row.id === 'flow')?.status, 'unknown');
});

test('extreme price change is a caution, not a trade recommendation', () => {
  const result = evaluateVerifiedMomentum({ ...sample, priceChange: { m5: 80, h1: 250 } }, stamp);
  assert.equal(result.checks.find(row => row.id === 'movement')?.status, 'attention');
});

test('summary counts all feed coins without clipping at preview count', () => {
  const coins = Array.from({ length: 240 }, (_, i) =>
    ({ ...sample, symbol: 'TEST' + i, updatedAt: i % 2 ? stamp - 160_000 : stamp - 5_000 }));
  const report = summarizeVerifiedMomentum(coins, stamp);
  assert.equal(report.count, 240);
  assert.equal(report.results.length, 240);
  assert.equal(report.invalidOrStale, 120);
  assert.equal(report.activeObservations, 120);
  assert.equal(report.missingCritical, 240);
});

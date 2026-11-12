# NEO Verified Momentum — research V1

**Status:** read-only PAPER research / data-quality observer. **Not an executable trading strategy or proof of profitability.**

## Where it runs

- Source: `src/lib/verifiedMomentumResearch.ts`
- UI: `src/components/VerifiedMomentumPanel.tsx`
- Placement: dashboard > **Койнове**, using the existing market feed.
- Regression tests: `npm run test:verified-momentum` (also required by the production build).
- Deliberately untouched: order execution, open/close logic, stop-loss, take-profit, deployed VPS engine, and the locked `ORDER_FLOW_ADAPTIVE_OCT4`.

## Evidence model

Each exact mint/pair observation gets eight checks, with **observed**, **attention** or **unknown**:

1. Mint/pair syntax (not ownership verification).
2. Freshness of the current market snapshot (90s window; 10s future clock tolerance).
3. Positive displayed price and liquidity (not LP lock or executable quote).
4. Aggregated 5-minute purchases and sales (not proof of independent wallets).
5. Availability of 5-minute and 1-hour price-change observations. Extremely large changes are flagged for manual scrutiny; they are **not** interpreted as momentum that predicts gains.
6. Independent wallet ownership / wash-trading audit: **unknown** in the market feed.
7. On-chain token rights, creator links and LP control: **unknown** in the market feed.
8. Fresh executable two-sided swap quote: **unknown** in the market feed.

No numeric combination of these checks generates a buy/sell suggestion. Missing fields are never interpreted as zero risk, high confidence, or positive evidence. The preview displays ten feed rows, but the counters evaluate **all** supplied rows.

## Why this is not yet a tested strategy

A price feed cannot independently verify the token issuer, wallet clusters, lock status or quote execution. Historical trade outcomes cannot be fairly attributed to this model, because it did not exist when those orders were entered. A positive return cannot be claimed without forward-only evaluation.

## Requirements before out-of-sample PAPER evaluation

- Collect timestamped and immutable candidate snapshots, recording the **exact token mint and pool**, raw source timestamps, provider freshness and all unknown checks.
- Record independent Solana authority/holder observations and two-sided executable quotes, or explicitly mark them unavailable; never fabricate them from DexScreener data.
- Produce separate later-observed market outcomes only **after** a snapshot's timestamp. Do not use future candles, later verified holders or final trade returns to classify earlier observations.
- Assess coverage, false positives, provider outages, drift and market manipulation warnings by pre-declared criteria and chronological time cohorts. Avoid selection on the eventual outcome.
- Any future PAPER performance comparison must include modeled transaction costs, route failures, quote age and invalid fills. Do not present marked prices as guaranteed realizable prices.
- A future model must go through separate review before any integration with order execution. This V1 module has no such integration.

This document records implementation and known limitations, not a claim that the strategy enters at optimal times or selects profitable assets.

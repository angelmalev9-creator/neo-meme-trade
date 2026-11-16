# NEO Selective Early — independent PAPER V1

> **Status: observational shadow experiment only.** No trading, wallets,
> signatures, bids, executable quotes, or instructions to buy meme coins.

## Motivation

The NEO primary PAPER engine remained loss-making on its latest accessible
seven-day sample (16 November 2026): 99 closed trades, five positive and 94
negative; net recorded PAPER PnL −$383.14. Most exits were
`EXIT_IMPACT_EMERGENCY` or `ORDERFLOW_EXIT`. This is not a validation set
for the new shadow model, which is a separate forward-only experiment.

The GeckoTerminal + DexScreener observer is a less reliable market-data source
than an executable Solana swap quote: prices can change enormously, lack
liquidity, or be temporarily misleading. **A price touching +5% does not imply
a sale at +5%.** Therefore this experiment is deliberately conservative.

## Mechanism

`scripts/neo_selective_shadow_paper.py` subscribes read-only to newly added
marks in the existing SQLite observation archive by strictly increasing rowid.
On first run it records the current highest rowid and **cannot backfill older
marks to manufacture entry opportunities**.

A research candidate must be observed early in the pool's lifetime, have two
sequential observations, show moderate movement (not a single giant spike),
and have sufficient continuously observed liquidity. Aggregate activity does
not establish that wallets are independent. Decisions use only attributes
available by the current observation timestamp; labels are not informed by
future outcomes.

Two independent shadow scenarios use the same initial research evidence:
`NET_TARGET_5` and `NET_TARGET_10`, both stopping at a modeled net mark
change of −5% or a four-minute mark-based maximum hold. A simulated $200
notional and $1,000 initial accounting balance are used in each scenario.
A modelled per-leg cost adjustment incorporates assumed fees/market impact
and is *not an executable quote*. Actual realized return is always $0.

Open positions are capped at one per scenario; two losses can temporarily
pause *new simulated entries*. Losses are never clamped to the nominal stop.
Missing sell marks, insufficient liquidity, extreme positive quote jumps or
stale price gaps are counted as **UNRESOLVED**, not as a win or a zero loss.
Disappeared observations are never removed from the results.

### Integrity boundaries

- Reads: `/var/lib/neo-early-paper/observations.sqlite3` only.
- Writes: independent directory `/var/lib/neo-shadow-paper/` only.
- Exposes an atomic report `/var/lib/neo-shadow-paper/report.json`.
- Uses a dedicated unprivileged service account and read-only filesystem
  where possible.
- Does not alter `ORDER_FLOW_ADAPTIVE_OCT4`, primary NEO PAPER state,
  market observer, account ledger or dashboard historical values.
- The simulation starts when first installed. A separate replay requires a
  separate database and is explicitly labeled **historical**.
- Neither positive forward mark results nor a high mark win rate are proof
  of realizable trading profits.

### Verification

`python3 -m unittest discover -s scripts -p test_neo_selective_shadow_paper.py -v`

The tests cover first-look causality, unverified/illiquid candidates,
two-sided cost effects, candidate independence, mark gaps, false massive
positive price spikes, nonclamped losses, missing exits and no backfill.

### Before any claims about profitability

A genuinely independently validated future model would require time-stamped
executable two-sided swap quotes, net fees, exact-pool routing, market depth,
rug checks, executable exit testing, out-of-sample evaluation and disclosure
of losing, missed and unresolved outcomes. None are supplied or inferred here.

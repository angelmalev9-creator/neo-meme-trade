# TikTok Strategy — light-filter PAPER sniper (Strategy Lab)

Status: **PAPER test strategy in the Strategy Lab.** It has its own $500 book and never touches the primary account, its ledger or `ORDER_FLOW_ADAPTIVE`. Nothing here predicts a win rate or a return.

Book id `TIKTOK`, shown in the dashboard as **TikTok Strategy**.

## Rules

| | TikTok Strategy | Other lab strategies |
| --- | --- | --- |
| Net stop | −12% | −3% |
| Net target | +17% | +10% |
| Max hold | 60 min | 60 min |
| Min score | 65 (lowest in the lab) | 78–93 |
| Min market cap | $30,000 | none |
| Min liquidity | $10,000 (lab-wide floor) | $10,000–80,000 |
| 5m change | −5% … +60% | narrower |
| 1h change | ≥ −50% | varies |
| 5m buys/sells | ≥ 0.80 | 0.80–1.25 |
| Liquidity / market cap | ≥ 0.02 | 0.03–0.17 |
| Pool age | from the first minute, up to 24 h | from 2 min |
| Order-flow evidence | not required | required by most |
| Max round-trip cost at entry | 4.0% | 2.75% |
| Position size | up to $150, cut down until the cost fits | same |

Unchanged for this book: fresh feed (≤ 20 s), valid mint and pool, independent price check, one position at a time, 60 s re-entry cooldown after a win and 180 s after a loss.

## Speed

- **Entry:** candidates are evaluated every 2 s, as soon as the pool appears in the feed. The feed itself refreshes about every 3 s, so scanning faster would re-read the same data.
- **Exit:** while this book holds a position the lab re-checks every 1 s instead of every 2 s, and closes on the first check at or beyond −12% / +17% net.
- Fills are simulated at the observed pool price with the cost model below. This is a paper model: it is not an on-chain sniper and has no transaction-landing race.

## Fees — what "0.2" means here

The lab does not choose the pool fee; the pool does. PumpSwap's fee depends on market cap and is about **1.25% per side near a $30k market cap**, falling to 0.30% for large caps. A paper strategy that pretended to pay 0.2% there would report profits that could not be had.

So the 0.2% is the part that can be controlled: the execution overhead (slippage plus latency buffer) is **0.2% per side**. The pool fee, price impact and network fee are charged in full on top, and among several candidates the cheapest round trip is taken first.

Consequences worth knowing:

- Near a $30k market cap a round trip costs roughly 3% before price impact. The stop and target are net of that, so +17% net needs about a +20% price move, and −12% net is reached at about a −9% move.
- That is why this book alone may enter with up to 4.0% round-trip cost. Under the lab's 2.75% default a pool in PumpSwap's top fee tiers (1.20–1.25% per side) can never be entered, whatever the size.
- **Take profit is a limit at exactly +17% net.** When the net mark is at or above +17% the sale is booked at +17%, never higher; the market value seen at that moment is kept as `observed_exit_pnl_pct`.
- **The stop triggers at −12% net and sells at the market.** A gap through the stop is booked at the observed loss, not clamped to −12%: a constant-product pool has no order that guarantees a price, so a booked −12% would be a result no real sale could deliver. Each exit keeps the previous mark and the seconds between the two observations (`pre_exit_pnl_pct`, `pre_exit_gap_seconds`), shown under the reason in the trade list.

## Where the numbers live

`backend/lab_activity.py`: `RULES['TIKTOK']`, `EXIT_OVERRIDES`, `ENTRY_COST_CAPS`, `SNIPER_IDS`, `SNIPER_POLL_SECONDS`. `backend/strategy_lab.py` applies them. Tests: `tests/test_lab_activity.py` (`TikTokStrategyTests`).

## Running it

The book appears once `neo-strategy-lab.service` runs this code. Existing books and their histories are kept; the new book starts at $500. The automatic deploy restarts the lab only if that unit runs from `/root/neo-meme-trade` (see `VPS_AUTO_DEPLOY.md`).

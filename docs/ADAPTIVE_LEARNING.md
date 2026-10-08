# ORDER_FLOW_ADAPTIVE_LEARNING — the high-frequency PAPER profile that learns

Status: **PAPER only**, and the primary engine profile. No signing, no swap submission. It is built to trade often and to shift size toward what has been working. It cannot promise profit: more trades also means more fees, and what it learns comes from small paper samples.

| Identity | Value |
| --- | --- |
| `signal_strategy` / `strategy_profile` | `ORDER_FLOW_ADAPTIVE_LEARNING` |
| `entry_policy_version` | `ORDER_FLOW_TIERED_LEARNER_V1` |
| `learning_mode` | `ONLINE_CONTEXT_EXPECTANCY_V1` |
| `exit_policy` | `oct4_adaptive` (the `ORDER_FLOW_ADAPTIVE` conviction exits, unchanged) |
| Owner of every setting | `backend/adaptive_learning.py` |

## What makes it trade more

| | ORDER_FLOW_ADAPTIVE | This profile |
| --- | --- | --- |
| Positions at once | 1 | 8 |
| Entry rule | GOLD only | GOLD (**CORE**) plus a looser **EXPLORE** tier |
| Size | fixed $200 | scaled to liquidity, $40–200 |
| Market scan | 15 s | 5 s |
| Quotes per scan | 2 | 6 |
| Same-token cooldown | 20 min | 2 min after a win, 10 min after a loss |
| Daily loss gate | $100 | none |
| Round-trip cost allowed at entry | 2.75% | 4.0% (worst case 6.0%) |
| Net stop | −5% | −10% |

**EXPLORE** accepts score ≥ 70, liquidity ≥ $10,000, 5m change −8…+40%, at least 2 verified trades in 60 s with buy/sell USD ≥ 1.10, conviction ≥ 58, and trades at half the CORE size. Its job is to keep sampling contexts the strict rule never sees.

**Size** aims at about 0.75% modeled price impact per side: roughly $56 in a $15k pool, $150 in a $40k pool, $200 from about $53k. A thin pool gets a small position instead of a cost rejection. $40 is the floor, because below it the fixed account and network costs dominate.

**The stop is −10% because the costs demand it.** A small-cap PumpSwap round trip costs about 3%. With a −5% stop the price would have less than 2% of room and most entries would stop out on fees alone.

## How it learns

Every closed trade is filed under nine context buckets: tier, hold mode at entry, liquidity band, market-cap band, pool age, 5-minute move, flow ratio, score band and DEX. For each bucket the engine keeps the recent net result.

- **Recent counts more.** A result weighs half as much 24 hours later, so the engine keeps re-testing what it believes.
- **Small samples are distrusted.** A bucket has no say below 5 weighted trades, and its average is pulled toward zero as if it had 8 extra break-even trades.
- **Size follows the evidence.** A candidate's buckets are averaged into a learned edge. +4% edge adds +1.0 to the size multiplier; the multiplier stays within 0.25–1.5. Winning contexts get up to 1.5× size, losing ones down to a quarter.
- **Repeated mistakes are skipped.** A bucket with at least 8 weighted trades, an average of −2.5% or worse and under 30% winners is avoided — shown as `learned_avoid` in `entry_diagnostics` — until that evidence has aged out.
- **A bad day shrinks sizes; it does not halt trading.** A bucket holding more than 60% of the recent trades cannot be skipped, only sized down.
- **Five straight losses halve every size** until the next winner.

Only trades booked with quote evidence are used. Trades from the earlier scout profile count too, rebuilt from their recorded fields, so the engine starts with what the account already experienced; they were managed with different exits, and their weight fades within days.

What is **not** learned: entry thresholds, exits and cost caps are fixed. The engine changes how much it trades a context and whether it trades it, nothing else.

`/state` → `learning` shows the trades used, the loss streak, the best and worst buckets and what is currently avoided. The dashboard shows the same.

## Exits

Unchanged from `ORDER_FLOW_ADAPTIVE` (`ORDER_FLOW_ADAPTIVE_OCT4.md`): stop on the net mark, conviction exit, order-flow exit, adaptive target, profit lock, trailing, mode max-hold, 120-minute cap. RUNNER and STRONG positions have no fixed target and are trailed, which is how winners are left to run. A position keeps the stop and exit policy recorded when it was opened.

## Still enforced

Rug guard, independent price check, exact pool and mint, verified order flow for the exact pool, fresh consistent quotes, cost caps, uncapped gap losses, atomic ledger. A pool the shared live tape does not cover is still rejected as `flow_quality`.

**Unsellable positions.** A position whose sell route stayed missing through every retry keeps its capital reserved but no longer freezes the engine or occupies one of the eight slots. A route that is only briefly missing still blocks new entries.

## Switching

`NEO_STRATEGY_PROFILE` selects the profile; unset means this one.

| Value | Profile |
| --- | --- |
| `ORDER_FLOW_ADAPTIVE_LEARNING` | this profile (default) |
| `ORDER_FLOW_ADAPTIVE_OCT4` | one $200 position, GOLD rule, no learning |
| `EARLY_SCOUT_V10` | the 2026-10-05 scout |

To go back, set the variable in the unit (`systemctl edit neo-user-angel-paper.service`) and restart it. The ledger is not touched.

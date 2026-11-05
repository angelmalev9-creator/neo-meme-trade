# October 4 strategy — PAPER-only shadow filter experiment

This experiment **does not change** the restored `ORDER_FLOW_ADAPTIVE_OCT4` strategy, entry/exit decisions, credentials, holdings, service settings, account balance or original ledger.

## Experimental overlays (decisions based only on evidence available at entry)

- **Estimated entry/exit cost gate:** require the recorded *pre-entry round-trip* estimate to be no worse than -2.00% for a $200 notional. This is a preliminary assumption to evaluate, **not** part of the October 4 original. Missing pre-entry evidence is not declared a valid entry.
- **Per-exact-pool loss brake:** inspect only *already-closed*, non-void PAPER positions in the **same mint + pool**. When the latest two closes are both losses within the prior six hours, screen the candidate for up to three hours after the most recent losing close. Use exact token/pool identity, never ticker alone.

All quoted impacts, liquidity and simulated fills are model measurements. This tool reads existing PAPER data; it submits **no orders**, invokes no network APIs, and **does not** enable any filter on the active engine.

## What the retrospective comparison DOES and DOES NOT say

Run:

```sh
python3 scripts/shadow_oct4_filters.py --state /path/to/isolated/paper/state.json \
  --day 2026-11-05 --since '2026-11-05T12:08:00+02:00' \
  --output /tmp/neo-oct4-shadow-comparison.json
```

All four variants (observed baseline, cost cap, loss brake, both overlays) use exactly the *same already-observed original entries*. As-of gating uses only previously closed outcomes, never the return of the candidate being screened. The reported PnL is the **historical observed PnL of screened entries**, not the actual PnL that a modified bot would have earned.

This method cannot predict replacement trades, changed cooldown timers, market movement, future quote availability, liquidity, execution impact, or profits. If both guards screen out nearly every observed entry, the correct result is **insufficient evidence**, not proof of a winning system. There is no automatic adoption into the original engine.

Only compare forward PAPER experiments on new, unseen periods; keep the original October 4 logic and history intact.

## Private dashboard integration (read-only)

The NEO site now has a separate **Сравнение на стратегии** tab under the user's authenticated account. It calls `GET /user/shadow-comparison?period=today` or `?period=since_restore` through the existing `/user/*` gateway and Vercel fallback. The gateway reads only `users/<authenticated-id>/state.json`; it never supplies any other user's data, and it never touches engine settings or calls a swap endpoint.

The tab refreshes approximately every 15 seconds while open and shows the four observed-screen variants, rejection reasons and evidence window. It explicitly labels selected historical PnL as non-counterfactual and declines to auto-promote a screen into the original bot.

The `since_restore` period starts at 2026-11-05 12:08 Europe/Sofia and extends to the report request time. The `today` period uses the current Sofia calendar day. Both exclude technical-voided trades and positions not yet closed by the report time.

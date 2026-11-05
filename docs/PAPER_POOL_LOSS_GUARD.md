# PAPER exact-pool loss-streak quarantine

**Separate risk overlay, not a modification of the original Oct4 entry/exit rules.**

- Active only when the exact current profile is `ORDER_FLOW_ADAPTIVE_OCT4`, engine mode and execution mode both `PAPER`, and `NEO_PAPER_POOL_LOSS_GUARD` is not `false`.
- Counts only completed, non-void trades from the **same account session** and the exact `(mint, poolAddress)` pair, not ticker strings.
- If the last **two** closed trades from that pool are losses within the previous **six hours**, no new entries in that same pool for **three hours after the most recent loss**. A later win/breakeven breaks the streak.
- Evaluates early in the scan *and again before the PAPER entry is recorded*, so a newly recorded loss cannot be ignored while quote verification is running.
- Other pools and strategies are unaffected; the original Oct 4 decision-rule module, fixed $200 notional, stop-loss, target and portfolio risk limit remain unchanged.
- Technical-voided trades and trades from other demo sessions cannot trigger quarantine.
- The authenticated Engine dashboard reports active quarantines, expiration in Sofia local time, and a distinct diagnostic rejection `pool_loss_streak_cooldown`.
- **This can prevent repetitive simulated entries; it does not guarantee profits or limit losses from an already-open trade.**

Regression tests: `test_paper_pool_loss_guard.py`; source integrity: `node scripts/verify-strategy-lock.mjs`.

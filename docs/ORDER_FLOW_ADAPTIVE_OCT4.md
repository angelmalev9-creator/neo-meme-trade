# ORDER_FLOW_ADAPTIVE — the restored 2026-10-04 PAPER strategy

Status: **PAPER only.** No signing, no swap submission, no live-money path. Nothing here promises a win rate, a trade frequency, a stop fill or a maximum realized loss.

The primary engine strategy is again the 2026-10-04 `ORDER_FLOW_ADAPTIVE` decision policy (entry `ORDER_FLOW_BALANCED_V4`, hold `ADAPTIVE_CONTEXT_HOLD`), running on the current durable, exact-pool, honest-accounting runtime. The 2026-10-05 high-frequency scout remains available as an explicit second profile.

| Identity | Value |
| --- | --- |
| `signal_strategy` | `ORDER_FLOW_ADAPTIVE` |
| `strategy_profile` | `ORDER_FLOW_ADAPTIVE_OCT4` |
| `strategy_version` | `gold-2026-10-04` |
| `entry_policy_version` | `ORDER_FLOW_BALANCED_V4` |
| `learning_mode` | `ADAPTIVE_CONTEXT_HOLD` |
| `exit_policy` / `exit_policy_version` | `oct4_adaptive` / `ADAPTIVE_CONTEXT_HOLD_NET_V1` |
| Recovered from | commit `5b78efd` (2026-10-04 22:25) |

## Where each setting comes from

`backend/order_flow_adaptive_oct4.py` is the single owner. It is a pure module (no network, clock or account state) holding `CONFIG`, `ENTRY_LIMITS`, `HOLD_MODES`, `EXIT_LIMITS`, the entry filter, the conviction model and the exit decision. `market_monitor.py` copies those values at start-up.

While this profile is active the engine **ignores** the same-named environment variables (`NEO_MAX_POSITIONS`, `NEO_TRADE_NOTIONAL_USD`, `NEO_SCAN_SECONDS`, `NEO_MAX_DAILY_LOSS_USD`, `NEO_STRICT_*`, …). Any that are set are listed in `/state` → `config.ignored_env_overrides`, so a systemd unit can no longer change the strategy silently. Paths, ports and execution-model variables (`NEO_MARKET_STATE_PATH`, `NEO_MONITOR_PORT`, `NEO_EXEC_*`, …) are still read from the environment.

Effective values: scan 15 s, position scan 2 s, 1 position, $200 notional, $100 daily PAPER loss gate, 5% planned net stop, 20-minute same-token cooldown (wins and losses), 60-second entry flow window, 2 quoted candidates per scan, feed age ≤ 30 s, entry quote age ≤ 10 s, impact ≤ 2.0%, expected round trip ≤ 2.75%, worst case ≤ 4.50%. The base values 18% / 4% / 7 min are reported for completeness; exits use the hold modes below.

## Entry — all must pass

Valid mint and exact pair; price > 0; feed ≤ 30 s old; complete verified order-flow coverage for the exact pool; NEO score ≥ 85; liquidity ≥ $30,000; 5m change −3%…+25%; 1h change −30%…+150%; 5m market buys/sells ≥ 1.0; liquidity/market cap ≥ 0.03; ≥ 4 verified flow trades; flow buy/sell USD ≥ 1.30; verified buy USD ≥ $150; ≥ 4 unique wallets; ≥ 3 buyer wallets; wallet buyer/seller ratio ≥ 1.0; largest sell < max($250, 50% of buy USD); conviction ≥ 72.

Then the modern checks, unchanged: independent price integrity (with exact-pool Jupiter tie-break), rug guard, consistent entry + immediate-exit quotes for the exact pool, cost caps, signal freshness at commit. Missing evidence is a rejection, never a pass.

Every scan writes `entry_diagnostics`: candidates, evaluated, signal_passed, quoted, opened, per-reason rejection counts, up to eight examples with the metrics that failed, the policy version and timestamp.

## Conviction and hold modes

Conviction starts at 50 and is adjusted by 30 s flow, 300 s flow, unique wallets, repeat buyers, whale flow, 5m momentum, 1h trend, market buy/sell ratio, liquidity versus entry and NEO score, then clamped to 0–100. The table is in `conviction_score()` and is checked row by row in `tests/test_order_flow_adaptive_oct4.py`.

| Mode | Conviction | Max hold | Fixed target | Trail arms at | Trail |
| --- | --- | --- | --- | --- | --- |
| RUNNER | ≥ 85 | 60 min | none | +15% | 7% |
| STRONG | ≥ 72 | 30 min | none | +12% | 6% |
| NORMAL | ≥ 58 | 15 min | +20% | +9% | 5% |
| CAUTIOUS | ≥ 45 | 8 min | +14% | +7% | 4% |
| WEAK | < 45 | 4 min | +8% | +5% | 3% |

## Exit priority

1. `STOP_LOSS` — net executable PnL ≤ −5%.
2. `CONVICTION_EXIT` — conviction < 35 and signal PnL < 0.
3. `ORDERFLOW_EXIT` — 30 s flow has ≥ 4 trades, ≥ 3 sells, sell USD ≥ max($200, 2 × buy USD), signal PnL < +3%.
4. `ADAPTIVE_TP_20` / `_14` / `_8` — only modes with a fixed target.
5. `CONVICTION_PROFIT_LOCK` — peak ≥ +10%, conviction < 50, profit > +2%.
6. `ADAPTIVE_TRAILING` — after the mode's arm, price ≤ peak × (1 − trail).
7. `ADAPTIVE_MAX_HOLD` — hold ≥ mode limit and conviction < 72.
8. `ABSOLUTE_MAX_HOLD` — 120 minutes.

## Deliberate differences from the literal 2026-10-04 code

These keep later correctness fixes; none loosens a check.

- **Stop on the net mark only.** The old chart-price stop trigger and the "cancel the stop if the fresh quote looks better" rule are not restored. Once the net mark reaches −5% the position is sold at the fresh exact-quantity quote, whatever that is. Gap losses are booked in full.
- **Context triggers use the exact entry pool's observed price** ("signal PnL"), as they did historically, but only while that price is fresh (≤ 30 s). Otherwise they fall back to the net mark, so a stale chart can neither fake a profit target nor keep a position open. Realized PnL is always the simulated sale.
- **Flow must have complete coverage for the exact pool** (`flow_quality`), instead of counting an empty or delayed tape as "no sellers".
- **Engine safety exits remain**: `LIQUIDITY_EMERGENCY` (liquidity < 80% of entry), `STALE_MARKET_EXIT`, `EXIT_IMPACT_EMERGENCY`, and the unavailable-liquidation handling that keeps exposure instead of inventing a fill.
- **History is not truncated to 300 trades**, the ledger is written atomically, and audit rows go through the durable outbox.

## Positions that are already open

A position is managed by the exit policy recorded when it was opened. Positions opened under `ORDER_FLOW_EARLY_SCOUT_PAPER_V10` keep their own +10% / −5% / 60-minute rules after the switch. With a one-position limit, the first `ORDER_FLOW_ADAPTIVE` entry happens only after those have all closed. Nothing is force-closed and nothing is reset.

## Profiles and other accounts

`NEO_STRATEGY_PROFILE` selects the profile; an unknown value refuses to start.

| Value | Effect |
| --- | --- |
| unset or `ORDER_FLOW_ADAPTIVE_OCT4` | this strategy (repository default) |
| `EARLY_SCOUT_V10` | the 2026-10-05 high-frequency scout, environment-driven as before |

The user gateway passes its environment to the engines it spawns. To keep gateway-spawned accounts on the scout when they next restart, add `NEO_STRATEGY_PROFILE=EARLY_SCOUT_V10` to `/etc/neo/neo-user-gateway.env` **before** deploying. The dedicated `neo-user-angel-paper.service` has its own unit and is not affected by that line. The central engine and the labs run from `/root/neomemecoins` and are untouched by this repository.

## Deploy (target account engine only)

```bash
cd /root/neo-meme-trade
git status && git fetch origin && git rev-parse HEAD origin/main

# 1. Record the "before" facts and archive the ledger (no reset).
curl -s http://127.0.0.1:18804/state | python3 -c "import json,sys; d=json.load(sys.stdin); s=d['stats']; \
print({'history': len(d['history']), 'balance': s['demo_balance_usd'], 'open': s['open_positions'], 'strategy': d['config']['signal_strategy']})"
systemctl cat neo-user-angel-paper.service | grep -E 'NEO_MARKET_(STATE|AUDIT)_PATH'
# With STATE_DIR set to that account directory:
ts=$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$STATE_DIR/archives/pre-oct4-$ts"
cp -a "$STATE_DIR"/state.json "$STATE_DIR"/audit.jsonl "$STATE_DIR"/all_time_history.json "$STATE_DIR/archives/pre-oct4-$ts/" 2>/dev/null
sha256sum "$STATE_DIR/archives/pre-oct4-$ts"/* | tee "$STATE_DIR/archives/pre-oct4-$ts/SHA256SUMS"

# 2. Update the code and test it on the server.
git merge --ff-only origin/main        # or check out the reviewed commit
python3 -m py_compile backend/*.py && python3 scripts/run_python_checks.py && npm run check:strategy

# 3. Restart only the target engine.
systemctl restart neo-user-angel-paper.service

# 4. Verify the live engine.
curl -s http://127.0.0.1:18804/health
curl -s http://127.0.0.1:18804/state | python3 -c "import json,sys; d=json.load(sys.stdin); c=d['config']; s=d['stats']; \
print(json.dumps({k: c.get(k) for k in ('signal_strategy','strategy_profile','entry_policy_version','exit_policy_version','scan_seconds','position_scan_seconds','max_positions','trade_notional_usd','stop_loss_pct','max_daily_loss_usd','same_token_cooldown_seconds','strict_entry_score','strict_min_conviction','strict_min_liquidity_usd','paper_only','ignored_env_overrides','effective_config_hash')}, indent=1)); \
print({'history': len(d['history']), 'balance': s['demo_balance_usd'], 'open': s['open_positions']}); print(d['entry_diagnostics'].get('message'))"
curl -s http://127.0.0.1:8789/user/health
```

Expect `ORDER_FLOW_ADAPTIVE` / `ORDER_FLOW_BALANCED_V4`, `paper_only: true`, the same port, a history count and balance that did not fall, and `ignored_env_overrides` naming the old strategy variables still present in the unit. The gateway needs no restart: `backend/user_gateway.py` is unchanged.

## Roll back

Set `Environment=NEO_STRATEGY_PROFILE=EARLY_SCOUT_V10` in the unit (`systemctl edit neo-user-angel-paper.service`) and restart it, or check out the previous commit and restart. Neither touches the ledger. Positions opened under `ORDER_FLOW_ADAPTIVE` keep their adaptive rules until they close.

## Shared dependency to watch

Entries need verified order flow for the exact pool. The shared live tape (`neo-live-tape.service`, running from `/root/neomemecoins`) records the pools in the **central** engine's feed. A pool the tape does not cover is rejected as `flow_quality`; that is the fail-closed behaviour, and it shows in `entry_diagnostics`.

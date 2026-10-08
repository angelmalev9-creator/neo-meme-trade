# Dashboard layout (2026-10-08)

The dashboard has three sections, chosen in the header; the choice is remembered per browser tab.

## Engine
- Six numbers: balance, today, open/max positions, closed trades, last scan, status.
- **Why it is (not) trading** — `entry_diagnostics` from the engine: message, candidates → evaluated → passed filter → quoted → opened, rejection counts of the last scan as bars with the engine's own labels, up to eight examples. This is the first place to look when the engine is quiet.
- Open positions, activity log, learning summary, closed trades (last 15, expandable), demo reset.

Removed: the "Order Flow · strategy and execution are checked separately" block, the hard-coded Lab integrity warning, the "GOLD ENGINE" badge and the Astra panel. Nothing on the server side of Astra was touched; the lab simply no longer merges its snapshot.

## Лаборатория (Strategy Lab)
- Only books with at least one trade or an open position are listed by default; a button shows the rest.
- Every book expands on click: its **why (not) entering** panel — cumulative counts since the lab's current policy version of which entry condition rejected candidates (`rejection_totals` on the book, `why_quiet` in the compact snapshot, labels in `activity_config.rejection_labels`) — then every trade with entry/exit time to the second, prices, hold time, result and reason.
- Paired lab and paper training panels stay below.

## Койн фийд
- Coin list with search and filters; selected coin shows **buyers / sellers by window**: 1m, 5m, 15m, 30m, 1h, 6h, 24h. Windows longer than the pool's age are disabled.
  - Verified on-chain tape (`live_tape.json`): trade counts, distinct wallets and USD for 1m–1h, flagged `complete` only when the tape's retention covers the window.
  - GeckoTerminal pool endpoint (free, no key): buys, sells, buyers, sellers, volume and price change for 5m–24h, cached 10 s per pool (30 s after an error).
  - DexScreener `txns` from the feed as a fallback.
  - There is no free source for buyers/sellers above 24 h.
- Endpoint: engine `GET /coin-flow?address=&pair=`, gateway `GET /user/coin-flow` (authenticated). Dashboard only; the engine's decisions still use the verified tape and exact-pool quotes.
- **Портфейли** card (engine `GET /coin-wallets`, gateway `GET /user/coin-wallets`, `backend/coin_wallets.py`), refreshed every 3 s:
  - *По портфейл*: every wallet seen trading this pool — bought / sold USD and count, net USD and net tokens, first and last trade. Sources: the verified tape (where it covers the pool) merged by signature with GeckoTerminal's free pool trades (last trades with the signer wallet), cached 10 s.
  - *Сделки на живо*: the merged trade list, each row labelled by source; wallet → Solscan account, tx → Solscan transaction.
  - *Холдъри (топ 20)*: Solana RPC `getTokenLargestAccounts`, owner decoded from the token account, share of `getTokenSupply`, pool vaults marked. "Влязъл" is the oldest signature of the token account (cached 10 min; "над 1000 tx" when it has more than one page). Cached 20 s; entry times are looked up eight accounts per request so the list fills within a few refreshes.
  - Every wallet, token account and transaction is a Solscan link.
- Below: on-chain tape rows, DexScreener chart, NEO price samples, NEO analysis.

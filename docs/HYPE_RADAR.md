# Hype Radar — news + viral memes → meme-coin PAPER entries

Status: **PAPER only**, isolated Strategy Lab book `HYPE_RADAR` ("Hype Radar"). No signing, no real money. Nothing here promises a win rate.

## How it works

1. **Collector** `backend/hype_radar.py` (own service, `backend/neo-hype-radar.service`) runs every 15 minutes by default:
   - reads free, keyless sources: Google News top stories and a crypto/memecoin search, Reddit top of r/memes, hot r/CryptoCurrency, new r/SolanaMemeCoins, CoinGecko trending, DexScreener top boosts, and the last 6 h of X Signal posts when that collector runs;
   - sends the numbered lines to **one cheap LLM call** (OpenAI-compatible chat endpoint; default xAI `grok-4.1-fast-non-reasoning`, about $0.001 per call at list prices) asking for at most 12 themes: name, 3–8 keywords people would put in a coin name or ticker, hype 0–100, category, one-line why, supporting line indices;
   - writes `/var/lib/neo-market/hype_radar.json`. Spend is accounted from token usage and capped per UTC day (`NEO_HYPE_DAILY_BUDGET_USD`, default $1). Without a key the status is `missing_api_key` and nothing is called.
2. **Matching** (`hype_radar.match_token`, pure, shared by lab and dashboard): a token matches a theme when a keyword equals its ticker, or a keyword of ≥4 letters appears inside its name or ticker. Only themes newer than 3 h (`NEO_HYPE_THEME_TTL_SECONDS`) with hype ≥ 40 (`NEO_HYPE_MIN_HYPE`) count. Score = hype × (1.0 ticker / 0.85 name) × keyword-length factor.
3. **Lab book `HYPE_RADAR`** (`strategy_lab.py`): among feed coins that pass the lab's usual feed/price-integrity/liquidity-floor checks and the book's light rule (score ≥ 60, liquidity ≥ $10k, market cap ≥ $30k, 5 m −10…+150 %, buy/sell ≥ 0.7, age ≤ 48 h), it takes a theme match and then a **rug gate**: `engine_rug_guard.fast_chain_check` (token program, supply, decimals, mint/freeze authority, extensions) must pass and the full RugCheck report must not be `blocked` (pending/unavailable is tolerated so the book can be early; the verdict is stored on the trade as `hype_match.rug_check`). Hottest theme first, youngest pool on ties. Exits: **net stop −12 %, take profit +17 % booked as a limit at exactly +17 %**, 1-second re-check while it holds, cost cap 4 %.
4. **Dashboard tab „Hype"**: active themes with hype bars, keywords, why and the source lines; the feed coins matching each theme (click → coin page); the book's balance/trades with the per-trade drop-down; collector status, model, today's spend and per-source status. Engine route `GET /hype-radar`, gateway `GET /user/hype-radar`.

## What it does not do

- It does not buy on the pump.fun bonding curve. The lab's execution model is pool-based (PumpSwap/Raydium pools with ≥ $10k liquidity), so the earliest a match can enter is the first pool with real liquidity. Bonding-curve PAPER entries would need a separate execution model.
- It does not use X's own API; X content arrives only through the X Signal collector (xAI x_search, budget-capped) if that service runs.
- Themes come from an LLM reading headlines; they can be wrong, and a matching name is not proof that a coin is the "real" one for a story.

## Install on the VPS

```bash
cp deploy/hype-radar.env.example /etc/neo/neo-hype-radar.env && chmod 600 /etc/neo/neo-hype-radar.env
# put the real key in NEO_HYPE_LLM_KEY
cp backend/neo-hype-radar.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now neo-hype-radar.service
sleep 20 && python3 -c "import json;d=json.load(open('/var/lib/neo-market/hype_radar.json'));print(d['status'],d.get('budget'),[t['theme'] for t in d.get('themes',[])])"
```

The auto-deploy restarts the engine (it imports `hype_radar.match_token`) and the lab when these files change; the collector service itself must be restarted by hand after a change to `backend/hype_radar.py` (`systemctl restart neo-hype-radar.service`).

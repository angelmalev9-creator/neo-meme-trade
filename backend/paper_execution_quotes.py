#!/usr/bin/env python3
"""Read-only Jupiter quote helper for paper trading.

This module never signs, builds, submits, or executes a transaction.
It only requests executable swap quotes for simulation/accounting.
"""
import fcntl
import os
import time
from pathlib import Path
from typing import Any

import requests

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
QUOTE_URL = os.getenv("NEO_JUPITER_QUOTE_URL", "https://api.jup.ag/swap/v1/quote")
API_KEY = os.getenv("JUPITER_API_KEY", "").strip()
SLIPPAGE_BPS = int(os.getenv("NEO_JUPITER_SLIPPAGE_BPS", "100"))
MIN_INTERVAL = float(os.getenv("NEO_JUPITER_KEYLESS_MIN_INTERVAL", "2.10"))
MARK_TTL_MS = int(os.getenv("NEO_JUPITER_MARK_TTL_MS", "6000"))
LOCK_PATH = Path(os.getenv("NEO_JUPITER_LOCK_PATH", "/var/lib/neo-market/jupiter_quote.lock"))
STAMP_PATH = Path(os.getenv("NEO_JUPITER_STAMP_PATH", "/var/lib/neo-market/jupiter_quote_last.txt"))

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "NEO-Paper-Quote/1.0", "Accept": "application/json"})
_MARK_CACHE: dict[tuple[str, int], dict[str, Any]] = {}


def _compact_route(data: dict[str, Any]) -> list[dict[str, Any]]:
    route = []
    for leg in data.get("routePlan") or []:
        info = leg.get("swapInfo") or {}
        route.append({
            "ammKey": info.get("ammKey"),
            "label": info.get("label"),
            "percent": leg.get("percent"),
        })
    return route


def route_uses_pair(data: dict[str, Any], pair_address: str) -> bool:
    target = str(pair_address or "").lower()
    if not target:
        return False
    return any(
        str(((leg.get("swapInfo") or {}).get("ammKey")) or "").lower() == target
        for leg in (data.get("routePlan") or [])
    )


def quote(input_mint: str, output_mint: str, amount_raw: int) -> dict[str, Any] | None:
    if amount_raw <= 0:
        return None
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Accept": "application/json"}
    if API_KEY:
        headers["x-api-key"] = API_KEY

    with LOCK_PATH.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            if not API_KEY:
                try:
                    last = float(STAMP_PATH.read_text().strip())
                except Exception:
                    last = 0.0
                wait = MIN_INTERVAL - (time.time() - last)
                if wait > 0:
                    time.sleep(wait)

            response = SESSION.get(
                QUOTE_URL,
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(int(amount_raw)),
                    "slippageBps": str(SLIPPAGE_BPS),
                    "swapMode": "ExactIn",
                    "instructionVersion": "V2",
                    "restrictIntermediateTokens": "true",
                },
                headers=headers,
                timeout=12,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or int(data.get("outAmount") or 0) <= 0:
                return None
            data["_compactRoute"] = _compact_route(data)
            data["_quotedAtMs"] = int(time.time() * 1000)
            return data
        except Exception:
            return None
        finally:
            try:
                STAMP_PATH.write_text(str(time.time()))
            except Exception:
                pass
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def entry_quote(token_mint: str, pair_address: str, notional_usd: float) -> dict[str, Any] | None:
    usdc_raw = int(max(0.0, notional_usd) * 1_000_000)
    data = quote(USDC_MINT, token_mint, usdc_raw)
    if not data or not route_uses_pair(data, pair_address):
        return None
    expected_raw = int(data.get("outAmount") or 0)
    floor_raw = int(data.get("otherAmountThreshold") or expected_raw)
    if expected_raw <= 0 or floor_raw <= 0:
        return None
    return {
        "input_usdc_raw": usdc_raw,
        "token_raw_expected": expected_raw,
        "token_raw_floor": floor_raw,
        "price_impact_pct": float(data.get("priceImpactPct") or 0) * 100.0,
        "slippage_bps": SLIPPAGE_BPS,
        "route": data.get("_compactRoute") or [],
        "quoted_at": data.get("_quotedAtMs"),
    }


def exit_quote(token_mint: str, token_raw_amount: int) -> dict[str, Any] | None:
    data = quote(token_mint, USDC_MINT, int(token_raw_amount))
    if not data:
        return None
    expected = int(data.get("outAmount") or 0) / 1_000_000.0
    floor = int(data.get("otherAmountThreshold") or data.get("outAmount") or 0) / 1_000_000.0
    if expected <= 0 or floor <= 0:
        return None
    return {
        "expected_usdc": expected,
        "floor_usdc": floor,
        "price_impact_pct": float(data.get("priceImpactPct") or 0) * 100.0,
        "slippage_bps": SLIPPAGE_BPS,
        "route": data.get("_compactRoute") or [],
        "quoted_at": data.get("_quotedAtMs"),
    }


def position_mark(position: dict[str, Any], coin: dict[str, Any], network_fee_usd: float, force: bool = False) -> dict[str, Any] | None:
    """Return a read-only executable liquidation mark for a paper position."""
    if position.get("execution_mode") != "JUPITER_QUOTE_V2":
        return None
    token_mint = str(position.get("address") or "")
    raw_amount = int(position.get("jupiter_token_raw_amount") or 0)
    if raw_amount <= 0:
        return None

    key = (token_mint, raw_amount)
    now = int(time.time() * 1000)
    cached = _MARK_CACHE.get(key)
    if not force and cached and now - int(cached.get("quoted_at") or 0) <= MARK_TTL_MS:
        return dict(cached)

    fresh = exit_quote(token_mint, raw_amount)
    if not fresh:
        return dict(cached) if cached and not force else None

    market_price = float(coin.get("priceUsd") or 0)
    impact_pct = float(fresh.get("price_impact_pct") or 0)
    slippage_pct = float(fresh.get("slippage_bps") or SLIPPAGE_BPS) / 100.0
    floor_usdc = float(fresh.get("floor_usdc") or 0)
    expected_usdc = float(fresh.get("expected_usdc") or 0)
    result = {
        "execution_source": "JUPITER_QUOTE_V2",
        "market_price": market_price,
        "fill_price": max(0.0, market_price * (1.0 - impact_pct / 100.0 - slippage_pct / 100.0)),
        "market_value_usd": expected_usdc,
        "gross_proceeds_usd": floor_usdc,
        "dex_fee_usd": 0.0,
        "network_fee_usd": max(0.0, network_fee_usd),
        "net_proceeds_usd": max(0.0, floor_usdc - max(0.0, network_fee_usd)),
        "impact_pct": impact_pct,
        "slippage_pct": slippage_pct,
        "latency_pct": 0.0,
        "quoted_at": int(fresh.get("quoted_at") or now),
        "route": fresh.get("route") or [],
    }
    _MARK_CACHE[key] = dict(result)
    return result

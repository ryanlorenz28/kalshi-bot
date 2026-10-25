"""
polymarket_signal.py — Fetches top Polymarket trader positions as a confirmation signal.

Uses the free Polymarket Data API (no auth required) to:
1. Get the top traders by monthly profit
2. Check their open positions on markets similar to ours
3. Return a signal: AGREE / DISAGREE / NEUTRAL

This signal is passed to Claude as context — not as a trade override.
"""

import requests
from datetime import datetime, timezone
from typing import Optional


POLYMARKET_DATA_API = "https://data-api.polymarket.com"
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"

# Keywords that map Kalshi market types to Polymarket search terms
KALSHI_TO_POLY_KEYWORDS = {
    "cpi":          ["cpi", "inflation", "consumer price"],
    "fed":          ["fed funds", "federal reserve", "interest rate", "fomc"],
    "gdp":          ["gdp", "economic growth", "recession"],
    "payrolls":     ["jobs", "payrolls", "nfp", "nonfarm"],
    "unemployment": ["unemployment", "jobless", "u-3"],
    "btc":          ["bitcoin", "btc"],
    "eth":          ["ethereum", "eth"],
    "inx":          ["s&p 500", "sp500", "spx"],
    "ndaq":         ["nasdaq", "ndx"],
}

TOP_TRADER_COUNT = 5   # how many top traders to check
REQUEST_TIMEOUT  = 4   # seconds — keep short so it never holds up analysis


class PolymarketSignal:

    def __init__(self):
        self._top_traders = None
        self._traders_fetched_at = None
        self._cache_ttl_hours = 6  # refresh trader list every 6 hours

    # ─── PUBLIC ──────────────────────────────────────────────────────────────

    def get_signal(self, market_question: str, our_side: str) -> dict:
        """
        Returns a signal dict:
          {
            "signal":      "AGREE" | "DISAGREE" | "NEUTRAL",
            "agree_count": int,
            "disagree_count": int,
            "detail":      str,   # human-readable summary for Claude
          }
        """
        try:
            keywords = self._keywords_for(market_question)
            if not keywords:
                return self._neutral("No matching Polymarket keywords for this market type")

            poly_markets = self._find_poly_markets(keywords)
            if not poly_markets:
                return self._neutral("No matching Polymarket markets found")

            traders = self._get_top_traders()
            if not traders:
                return self._neutral("Could not fetch top trader list")

            agree = 0
            disagree = 0
            details = []

            for market in poly_markets[:3]:  # check up to 3 similar markets
                condition_id = market.get("conditionId") or market.get("id", "")
                if not condition_id:
                    continue
                for trader_addr in traders[:TOP_TRADER_COUNT]:
                    position = self._get_trader_position(trader_addr, condition_id)
                    if position is None:
                        continue
                    trader_side = position.get("side", "").upper()
                    if trader_side == our_side.upper():
                        agree += 1
                        details.append(f"Top trader agrees ({trader_side})")
                    elif trader_side and trader_side != our_side.upper():
                        disagree += 1
                        details.append(f"Top trader disagrees ({trader_side})")

            if agree == 0 and disagree == 0:
                return self._neutral("Top traders have no positions in equivalent Polymarket markets")

            if agree > disagree:
                signal = "AGREE"
                detail = f"{agree} top trader(s) positioned same direction as Claude on similar markets"
            elif disagree > agree:
                signal = "DISAGREE"
                detail = f"{disagree} top trader(s) positioned OPPOSITE to Claude — consider carefully"
            else:
                signal = "NEUTRAL"
                detail = f"Top traders split: {agree} agree, {disagree} disagree"

            return {
                "signal":        signal,
                "agree_count":   agree,
                "disagree_count":disagree,
                "detail":        detail,
            }

        except Exception as e:
            return self._neutral(f"Signal fetch error: {e}")

    # ─── TOP TRADERS ─────────────────────────────────────────────────────────

    def _get_top_traders(self) -> list:
        """Fetch top traders by monthly profit. Cached for 6 hours."""
        now = datetime.now(timezone.utc)
        if (self._top_traders is not None and self._traders_fetched_at is not None and
                (now - self._traders_fetched_at).total_seconds() < self._cache_ttl_hours * 3600):
            return self._top_traders

        try:
            resp = requests.get(
                f"{POLYMARKET_DATA_API}/leaderboard",
                params={"window": "1m", "limit": TOP_TRADER_COUNT},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            # API returns list of {address, name, pnl, ...}
            traders = [row.get("address") or row.get("proxyWallet", "") for row in data]
            traders = [t for t in traders if t]
            self._top_traders = traders[:TOP_TRADER_COUNT]
            self._traders_fetched_at = now
            return self._top_traders
        except Exception:
            return []

    # ─── MARKET SEARCH ───────────────────────────────────────────────────────

    def _find_poly_markets(self, keywords: list) -> list:
        """Find open Polymarket markets matching keywords."""
        results = []
        for kw in keywords[:2]:  # check up to 2 keywords
            try:
                resp = requests.get(
                    f"{POLYMARKET_GAMMA_API}/markets",
                    params={"keyword": kw, "active": "true", "limit": 5},
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    results.extend(resp.json() if isinstance(resp.json(), list)
                                   else resp.json().get("markets", []))
            except Exception:
                pass
        return results[:5]

    def _get_trader_position(self, address: str, condition_id: str) -> Optional[dict]:
        """Check if a trader has a position in a specific market."""
        try:
            resp = requests.get(
                f"{POLYMARKET_DATA_API}/positions",
                params={"user": address, "market": condition_id, "sizeThreshold": "10"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            positions = data if isinstance(data, list) else data.get("positions", [])
            if not positions:
                return None
            # Take the largest position
            pos = max(positions, key=lambda p: float(p.get("size", 0) or 0))
            # Determine side from outcome name (YES/NO)
            outcome = pos.get("outcome", pos.get("title", "")).upper()
            side = "YES" if "YES" in outcome else ("NO" if "NO" in outcome else "")
            return {"side": side, "size": float(pos.get("size", 0) or 0)}
        except Exception:
            return None

    # ─── HELPERS ─────────────────────────────────────────────────────────────

    def _keywords_for(self, question: str) -> list:
        q = question.lower()
        for key, keywords in KALSHI_TO_POLY_KEYWORDS.items():
            if any(k in q for k in [key] + keywords):
                return keywords
        return []

    @staticmethod
    def _neutral(reason: str) -> dict:
        return {
            "signal":        "NEUTRAL",
            "agree_count":   0,
            "disagree_count":0,
            "detail":        reason,
        }

"""
kalshi_client.py — Handles all communication with Kalshi's API.
"""

import requests
import json
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from datetime import datetime, timezone


class KalshiClient:
    BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

    def __init__(self, config):
        self.config  = config
        self.session = requests.Session()
        self.session.headers.update({
            "Accept":       "application/json",
            "Content-Type": "application/json",
        })

    def _sign_request(self, method: str, path: str) -> dict:
        """Sign the request with your private key — required by Kalshi."""
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        message   = timestamp + method.upper() + path

        try:
            private_key_pem = self.config.KALSHI_API_PRIVATE_KEY
           private_key_pem = self.config.KALSHI_API_PRIVATE_KEY
private_key_pem = private_key_pem.replace("\\n", "\n")
if not private_key_pem.startswith("-----"):
    private_key_pem = f"-----BEGIN RSA PRIVATE KEY-----\n{private_key_pem}\n-----END RSA PRIVATE KEY-----"

            private_key = serialization.load_pem_private_key(
                private_key_pem.encode(), password=None
            )
            signature = private_key.sign(
                message.encode(), padding.PKCS1v15(), hashes.SHA256()
            )
            signature_b64 = base64.b64encode(signature).decode()

            return {
                "KALSHI-ACCESS-KEY":       self.config.KALSHI_API_KEY_ID,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": signature_b64,
            }
        except Exception as e:
            print(f"Signing error: {e}")
            return {}

    def get_markets(self, limit: int = 10) -> list:
        """Fetch active markets from Kalshi."""
        path = "/markets"
        try:
            headers = self._sign_request("GET", path)
            resp = self.session.get(
                f"{self.BASE_URL}{path}",
                headers=headers,
                params={
                    "status": "open",
                    "limit":  limit * 3,
                },
                timeout=15,
            )
            resp.raise_for_status()
            markets_raw = resp.json().get("markets", [])
            return [self._normalize(m) for m in markets_raw if m]
        except Exception as e:
            print(f"Error fetching Kalshi markets: {e}")
            return self._demo_markets()[:limit]

    def _normalize(self, raw: dict) -> dict:
        """Convert Kalshi market format to our standard format."""
        try:
            yes_price = float(raw.get("yes_ask", 0.5))
            no_price  = 1 - yes_price

            return {
                "id":              raw.get("ticker", "unknown"),
                "question":        raw.get("title", "Unknown market"),
                "description":     raw.get("rules_primary", ""),
                "market_type":     "binary",
                "outcomes": [
                    {"name": "Yes", "price": yes_price},
                    {"name": "No",  "price": no_price},
                ],
                "volume":          float(raw.get("volume", 0) or 0),
                "liquidity":       float(raw.get("open_interest", 0) or 0),
                "days_to_resolve": self._days_until(raw.get("close_time", "")),
                "end_date":        raw.get("close_time", ""),
                "category":        raw.get("category", "General"),
                "tags":            [],
                "url":             f"https://kalshi.com/markets/{raw.get('ticker', '')}",
            }
        except Exception:
            return None

    def place_order(self, ticker: str, side: str, amount_usd: float, dry_run: bool = True) -> dict:
        """
        Place a trade on Kalshi.
        side = "yes" or "no"
        """
        if dry_run:
            print(f"[PAPER] Would bet ${amount_usd} on {side.upper()} for {ticker}")
            return {"status": "paper", "ticker": ticker, "side": side, "amount": amount_usd}

        path = "/portfolio/orders"
        try:
            headers = self._sign_request("POST", path)
            payload = {
                "ticker":    ticker,
                "side":      side,
                "type":      "market",
                "count":     int(amount_usd),
            }
            resp = self.session.post(
                f"{self.BASE_URL}{path}",
                headers=headers,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Error placing Kalshi order: {e}")
            return None

    def get_balance(self) -> float:
        """Get your real Kalshi account balance."""
        path = "/portfolio/balance"
        try:
            headers = self._sign_request("GET", path)
            resp = self.session.get(
                f"{self.BASE_URL}{path}",
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return float(resp.json().get("balance", 0)) / 100
        except Exception:
            return 0.0

    @staticmethod
    def _days_until(date_str: str) -> int:
        if not date_str:
            return 999
        try:
            from datetime import datetime, timezone
            end   = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            delta = end - datetime.now(timezone.utc)
            return max(0, delta.days)
        except Exception:
            return 999

    @staticmethod
    def _demo_markets() -> list:
        return [
            {
                "id": "INXD-23DEC31-T4500",
                "question": "Will the S&P 500 close above 4500 today?",
                "description": "Resolves YES if S&P 500 closes above 4500.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.62}, {"name": "No", "price": 0.38}],
                "volume": 50000, "liquidity": 25000,
                "days_to_resolve": 1, "end_date": "",
                "category": "Financials", "tags": [],
                "url": "https://kalshi.com/demo",
            },
            {
                "id": "BTCD-23DEC31-T30000",
                "question": "Will Bitcoin be above $80,000 at end of day?",
                "description": "Resolves YES if BTC/USD is above $80,000 at market close.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.44}, {"name": "No", "price": 0.56}],
                "volume": 80000, "liquidity": 40000,
                "days_to_resolve": 1, "end_date": "",
                "category": "Crypto", "tags": [],
                "url": "https://kalshi.com/demo",
            },
        ]

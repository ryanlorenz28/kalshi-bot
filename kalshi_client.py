import requests
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from datetime import datetime, timezone


class KalshiClient:
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _sign_request(self, method, path):
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        message = timestamp + method.upper() + path
        try:
            pk = self.config.KALSHI_API_PRIVATE_KEY
            pk = pk.replace("\\n", "\n")
            if not pk.startswith("-----"):
                pk = f"-----BEGIN RSA PRIVATE KEY-----\n{pk}\n-----END RSA PRIVATE KEY-----"
            private_key = serialization.load_pem_private_key(pk.encode(), password=None)
            signature = private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
            return {
                "KALSHI-ACCESS-KEY": self.config.KALSHI_API_KEY_ID,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            }
        except Exception as e:
            print(f"Signing error: {e}")
            return {}

    def get_markets(self, limit=10):
        path = "/markets"
        try:
            resp = self.session.get(
                f"{self.BASE_URL}{path}",
                headers=self._sign_request("GET", path),
                params={"status": "open", "limit": limit * 3},
                timeout=15,
            )
            resp.raise_for_status()
            markets = resp.json().get("markets", [])
            if markets:
                first = markets[0]
                print(f"DEBUG keys: {list(first.keys())}")
                print(f"DEBUG prices: yes_ask={first.get('yes_ask')} yes_bid={first.get('yes_bid')} last_price={first.get('last_price')} yes_price={first.get('yes_price')}")
            result = [self._normalize(m) for m in markets if m]
            return [m for m in result if m][:limit]
        except Exception as e:
            print(f"Error fetching Kalshi markets: {e}")
            return self._demo_markets()[:limit]

    def _normalize(self, raw):
        try:
            yes_price = (
                raw.get("yes_ask") or
                raw.get("yes_bid") or
                raw.get("last_price") or
                raw.get("yes_price") or
                0.5
            )
            yes_price = float(yes_price)
            if yes_price > 1:
                yes_price = yes_price / 100
            if yes_price <= 0 or yes_price >= 1:
                yes_price = 0.5
            return {
                "id": raw.get("ticker", "unknown"),
                "question": raw.get("title", "Unknown market"),
                "description": raw.get("rules_primary", ""),
                "market_type": "binary",
                "outcomes": [
                    {"name": "Yes", "price": yes_price},
                    {"name": "No", "price": round(1 - yes_price, 4)},
                ],
                "volume": float(raw.get("volume", 0) or 0),
                "liquidity": float(raw.get("open_interest", 0) or 0),
                "days_to_resolve": self._days_until(raw.get("close_time", "")),
                "category": raw.get("category", "General"),
                "url": f"https://kalshi.com/markets/{raw.get('ticker', '')}",
            }
        except Exception:
            return None

    def place_order(self, ticker, side, amount_usd, dry_run=True):
        if dry_run:
            print(f"[PAPER] Would bet ${amount_usd} on {side.upper()} for {ticker}")
            return {"status": "paper", "ticker": ticker, "side": side, "amount": amount_usd}
        path = "/portfolio/orders"
        try:
            resp = self.session.post(
                f"{self.BASE_URL}{path}",
                headers=self._sign_request("POST", path),
                json={"ticker": ticker, "side": side, "type": "market", "count": int(amount_usd)},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Error placing order: {e}")
            return None

    def _days_until(self, date_str):
        if not date_str:
            return 999
        try:
            end = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return max(0, (end - datetime.now(timezone.utc)).days)
        except Exception:
            return 999

    def _demo_markets(self):
        return [
            {
                "id": "DEMO-001",
                "question": "Will the S&P 500 close above 5000 today?",
                "description": "Resolves YES if S&P 500 closes above 5000.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.62}, {"name": "No", "price": 0.38}],
                "volume": 50000, "liquidity": 25000,
                "days_to_resolve": 1, "category": "Financials",
                "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-002",
                "question": "Will Bitcoin be above $80,000 at end of day?",
                "description": "Resolves YES if BTC/USD is above 80000.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.44}, {"name": "No", "price": 0.56}],
                "volume": 80000, "liquidity": 40000,
                "days_to_resolve": 1, "category": "Crypto",
                "url": "https://kalshi.com/demo",
            },
        ]

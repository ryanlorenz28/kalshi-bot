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

    # ─── AUTH ──────────────────────────────────────────────────────────────────

    def _parse_private_key(self, raw):
        """
        Normalize a private key from any Railway storage format into valid PEM.
        Handles: literal \\n, real newlines, missing headers, one long base64 string.
        """
        # Replace literal \n sequences with real newlines
        pk = raw.replace("\\n", "\n").replace("\\\\n", "\n")

        # Split into lines, strip whitespace, drop blanks
        lines = [l.strip() for l in pk.splitlines()]
        lines = [l for l in lines if l]

        # Extract only the base64 body (drop any existing headers)
        body_lines = [l for l in lines if not l.startswith("-----")]

        # If body landed as one long string, chunk into 64-char lines
        if len(body_lines) == 1:
            b64 = body_lines[0]
            body_lines = [b64[i:i+64] for i in range(0, len(b64), 64)]

        # Reassemble with correct PEM structure
        pem = "-----BEGIN RSA PRIVATE KEY-----\n"
        pem += "\n".join(body_lines)
        pem += "\n-----END RSA PRIVATE KEY-----\n"
        return pem

    def _sign_request(self, method, path):
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        message = timestamp + method.upper() + path
        try:
            pem = self._parse_private_key(self.config.KALSHI_API_PRIVATE_KEY)
            private_key = serialization.load_pem_private_key(pem.encode(), password=None)
            signature = private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
            return {
                "KALSHI-ACCESS-KEY": self.config.KALSHI_API_KEY_ID,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            }
        except Exception as e:
            print("Signing error: " + str(e))
            return {}

    # ─── MARKETS ───────────────────────────────────────────────────────────────

    def get_markets(self, limit=10):
        path = "/markets"
        try:
            resp = self.session.get(
                self.BASE_URL + path,
                headers=self._sign_request("GET", path),
                params={"status": "open", "limit": limit * 3},
                timeout=15,
            )
            resp.raise_for_status()
            markets = resp.json().get("markets", [])
            result = [self._normalize(m) for m in markets if m]
            result = [m for m in result if m]
            print("Found " + str(len(result)) + " markets after filtering")
            return result[:limit]
        except Exception as e:
            print("Error fetching Kalshi markets: " + str(e))
            return self._demo_markets()[:limit]

    def _normalize(self, raw):
        try:
            yes_price = (
                raw.get("yes_ask_dollars")
                or raw.get("yes_bid_dollars")
                or raw.get("last_price_dollars")
                or raw.get("previous_yes_ask_dollars")
                or "0.5"
            )
            yes_price = float(yes_price)
            if yes_price > 1:
                yes_price = yes_price / 100
            if yes_price <= 0 or yes_price >= 1:
                yes_price = 0.5
            ticker = raw.get("ticker", "unknown")
            return {
                "id": ticker,
                "question": raw.get("title", "Unknown market"),
                "description": raw.get("rules_primary", ""),
                "market_type": "binary",
                "outcomes": [
                    {"name": "Yes", "price": yes_price},
                    {"name": "No", "price": round(1 - yes_price, 4)},
                ],
                "volume": float(raw.get("volume_fp", 0) or 0),
                "liquidity": float(raw.get("liquidity_dollars", 0) or 0),
                "days_to_resolve": self._days_until(raw.get("close_time", "")),
                "category": raw.get("event_ticker", "General"),
                "url": "https://kalshi.com/markets/" + ticker,
            }
        except Exception:
            return None

    # ─── ORDERS ────────────────────────────────────────────────────────────────

    def place_order(self, ticker, side, amount_usd, price, dry_run=None):
        """
        Place a market order on Kalshi.

        Parameters
        ----------
        ticker     : market ticker string
        side       : "yes" or "no"
        amount_usd : dollar amount to spend
        price      : current price of the chosen side (0.0–1.0)
        dry_run    : override paper/live mode. None = use config.PAPER_TRADING

        Kalshi counts
        -------------
        Each contract costs `price` dollars and pays $1 if correct.
        count = floor(amount_usd / price)   — minimum 1 contract
        """
        if dry_run is None:
            dry_run = self.config.PAPER_TRADING

        # Contract count calculation (the critical fix)
        price = max(0.01, min(0.99, float(price)))   # guard against 0/1
        count = max(1, int(amount_usd / price))
        actual_cost = round(count * price, 2)

        if dry_run:
            print(
                f"[PAPER] {side.upper()} {count} contracts @ ${price:.2f} "
                f"= ${actual_cost:.2f} on {ticker}"
            )
            return {
                "status": "paper",
                "ticker": ticker,
                "side": side,
                "count": count,
                "price": price,
                "cost_usd": actual_cost,
            }

        # ── Live order ──────────────────────────────────────────
        path = "/portfolio/orders"
        payload = {
            "ticker": ticker,
            "action": "buy",
            "side": side.lower(),       # "yes" or "no"
            "type": "limit",
            "count": count,
            "yes_price": int(price * 100) if side.lower() == "yes" else int((1 - price) * 100),
        }
        try:
            resp = self.session.post(
                self.BASE_URL + path,
                headers=self._sign_request("POST", path),
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            result["cost_usd"] = actual_cost
            result["count"] = count
            return result
        except Exception as e:
            print("Error placing order: " + str(e))
            return None

    # ─── PORTFOLIO ─────────────────────────────────────────────────────────────

    def get_balance(self):
        """Return available balance in dollars, or None on error."""
        path = "/portfolio/balance"
        try:
            resp = self.session.get(
                self.BASE_URL + path,
                headers=self._sign_request("GET", path),
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # Kalshi returns balance in cents
            return data.get("balance", 0) / 100
        except Exception as e:
            print("Error fetching balance: " + str(e))
            return None

    # ─── HELPERS ───────────────────────────────────────────────────────────────

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
                "outcomes": [
                    {"name": "Yes", "price": 0.62},
                    {"name": "No", "price": 0.38},
                ],
                "volume": 50000,
                "liquidity": 25000,
                "days_to_resolve": 1,
                "category": "Financials",
                "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-002",
                "question": "Will Bitcoin be above $80000 at end of day?",
                "description": "Resolves YES if BTC/USD is above 80000.",
                "market_type": "binary",
                "outcomes": [
                    {"name": "Yes", "price": 0.44},
                    {"name": "No", "price": 0.56},
                ],
                "volume": 80000,
                "liquidity": 40000,
                "days_to_resolve": 1,
                "category": "Crypto",
                "url": "https://kalshi.com/demo",
            },
        ]

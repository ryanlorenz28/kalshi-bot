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
        message   = timestamp + method.upper() + path
        try:
            pk = self.config.KALSHI_API_PRIVATE_KEY
            pk = pk.replace("\\n", "\n")
            if not pk.startswith("-----"):
                pk = "-----BEGIN RSA PRIVATE KEY-----\n" + pk + "\n-----END RSA PRIVATE KEY-----"
            private_key = serialization.load_pem_private_key(pk.encode(), password=None)
            signature   = private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
            return {
                "KALSHI-ACCESS-KEY":       self.config.KALSHI_API_KEY_ID,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            }
        except Exception as e:
            print("Signing error: " + str(e))
            return {}

    def get_markets(self, limit=10):
        """
        Fetch markets from specific Kalshi series that have clean binary questions.
        Sports props come as garbled multi-outcome lists so we skip those series.
        """
        # These series reliably return clean binary Yes/No markets on Kalshi
        good_series = [
            "KXBTCD",       # Bitcoin daily
            "KXBTCW",       # Bitcoin weekly
            "KXETHD",       # Ethereum daily
            "KXSPX",        # S&P 500
            "KXSPXD",       # S&P 500 daily
            "KXNASD",       # Nasdaq
            "KXNASDD",      # Nasdaq daily
            "KXINFL",       # Inflation / CPI
            "KXCPI",        # CPI
            "KXFED",        # Fed rate decisions
            "KXFFR",        # Federal funds rate
            "KXUNEMP",      # Unemployment
            "KXGDP",        # GDP
            "KXREC",        # Recession
            "KXPRES",       # Presidential approval
            "KXTRUMP",      # Trump related
            "KXHOUSE",      # Housing / real estate
            "KXOIL",        # Oil prices
            "KXGOLD",       # Gold
            "KXDXY",        # US Dollar index
            "KXVIX",        # VIX volatility
        ]

        all_markets = []

        # First try fetching by series
        for series in good_series:
            if len(all_markets) >= limit * 3:
                break
            try:
                path = "/markets"
                resp = self.session.get(
                    self.BASE_URL + path,
                    headers=self._sign_request("GET", path),
                    params={
                        "status":        "open",
                        "series_ticker": series,
                        "limit":         10,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    markets = resp.json().get("markets", [])
                    all_markets.extend(markets)
            except Exception:
                continue

        # If series fetch got nothing, fall back to general fetch with heavy filtering
        if not all_markets:
            try:
                path = "/markets"
                resp = self.session.get(
                    self.BASE_URL + path,
                    headers=self._sign_request("GET", path),
                    params={"status": "open", "limit": 200},
                    timeout=15,
                )
                resp.raise_for_status()
                all_markets = resp.json().get("markets", [])
            except Exception as e:
                print("Error fetching markets: " + str(e))
                return self._demo_markets()[:limit]

        # Also do a general fetch to supplement series results
        try:
            path = "/markets"
            resp = self.session.get(
                self.BASE_URL + path,
                headers=self._sign_request("GET", path),
                params={"status": "open", "limit": 200},
                timeout=15,
            )
            if resp.status_code == 200:
                general = resp.json().get("markets", [])
                # Only add markets from non-sports categories
                sports_keywords = [
                    "nba", "nfl", "mlb", "nhl", "soccer", "tennis",
                    "golf", "mma", "ufc", "ncaa", "cfb", "nascar",
                    "pitcher", "batter", "touchdown", "homerun",
                ]
                for m in general:
                    ticker = (m.get("ticker") or "").lower()
                    title  = (m.get("title") or "").lower()
                    if not any(kw in ticker or kw in title for kw in sports_keywords):
                        all_markets.append(m)
        except Exception:
            pass

        result = [self._normalize(m) for m in all_markets if m]
        result = [m for m in result if m]
        print("Found " + str(len(result)) + " markets after filtering")

        if not result:
            print("No real markets found — using demo markets")
            return self._demo_markets()[:limit]

        return result[:limit]

    def _normalize(self, raw):
        try:
            # Try to build the most descriptive title possible
            title = (
                raw.get("title") or
                raw.get("subtitle") or
                raw.get("yes_sub_title") or
                ""
            ).strip()

            # If subtitle adds useful info, append it
            subtitle = (raw.get("subtitle") or raw.get("yes_sub_title") or "").strip()
            if subtitle and subtitle not in title:
                title = title + " — " + subtitle

            # Skip multi-outcome markets
            if title.lower().startswith("yes ") and "," in title:
                return None
            if title.lower().startswith("no ") and "," in title:
                return None
            if title.count(",") >= 3:
                return None
            if len(title) < 8:
                return None

            # Get yes price
            yes_price = (
                raw.get("yes_ask_dollars") or
                raw.get("yes_bid_dollars") or
                raw.get("last_price_dollars") or
                raw.get("previous_yes_ask_dollars") or
                "0.5"
            )
            yes_price = float(yes_price)
            if yes_price > 1:
                yes_price = yes_price / 100
            if yes_price <= 0 or yes_price >= 1:
                yes_price = 0.5

            # Skip near-certain and near-impossible markets early
            # (saves Claude API calls on obvious skips)
            if yes_price < 0.04 or yes_price > 0.96:
                return None

            return {
                "id":              raw.get("ticker", "unknown"),
                "question":        title,
                "description":     raw.get("rules_primary", ""),
                "market_type":     "binary",
                "outcomes": [
                    {"name": "Yes", "price": yes_price},
                    {"name": "No",  "price": round(1 - yes_price, 4)},
                ],
                "volume":          float(raw.get("volume_fp", 0) or 0),
                "liquidity":       float(raw.get("liquidity_dollars", 0) or 0),
                "days_to_resolve": self._days_until(raw.get("close_time", "")),
                "category":        raw.get("event_ticker", "General"),
                "url":             "https://kalshi.com/markets/" + raw.get("ticker", ""),
            }
        except Exception:
            return None

    def place_order(self, ticker, side, amount_usd, dry_run=True):
        if dry_run:
            print("[PAPER] Would bet $" + str(amount_usd) + " on " + side + " for " + ticker)
            return {"status": "paper", "ticker": ticker, "side": side, "amount": amount_usd}
        path = "/portfolio/orders"
        try:
            resp = self.session.post(
                self.BASE_URL + path,
                headers=self._sign_request("POST", path),
                json={"ticker": ticker, "side": side, "type": "market", "count": int(amount_usd)},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print("Error placing order: " + str(e))
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
                "volume": 50000, "liquidity": 25000, "days_to_resolve": 1,
                "category": "Financials", "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-002",
                "question": "Will Bitcoin close above $80,000 today?",
                "description": "Resolves YES if BTC/USD closes above $80,000.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.44}, {"name": "No", "price": 0.56}],
                "volume": 80000, "liquidity": 40000, "days_to_resolve": 1,
                "category": "Crypto", "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-003",
                "question": "Will the Fed hold rates at the next meeting?",
                "description": "Resolves YES if the Fed makes no rate change.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.78}, {"name": "No", "price": 0.22}],
                "volume": 120000, "liquidity": 60000, "days_to_resolve": 14,
                "category": "Economics", "url": "https://kalshi.com/demo",
            },
        ]

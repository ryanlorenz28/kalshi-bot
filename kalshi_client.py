import requests
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from datetime import datetime, timezone


class KalshiClient:
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    # Series tickers that return clean binary Yes/No markets
    GOOD_SERIES = [
        "KXBTCD", "KXBTCW", "KXETHD",
        "KXSPX",  "KXSPXD", "KXNASD", "KXNASDD",
        "KXINFL", "KXCPI",  "KXFED",  "KXFFR",
        "KXUNEMP","KXGDP",  "KXREC",
        "KXPRES", "KXTRUMP","KXHOUSE",
        "KXOIL",  "KXGOLD", "KXDXY",  "KXVIX",
    ]

    # Sports keywords — skip any market whose ticker or title contains these
    SPORTS_KEYWORDS = [
        "nba", "nfl", "mlb", "nhl", "soccer", "tennis", "golf",
        "mma", "ufc", "ncaa", "nascar", "pitcher", "batter",
        "touchdown", "homerun", "pitcher", "rebounds", "assists",
    ]

    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "Accept":       "application/json",
            "Content-Type": "application/json",
        })

    # ─── SIGNING ─────────────────────────────────────────────────────────────

    def _sign_request(self, method, path):
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        message   = timestamp + method.upper() + path
        try:
            pk = self.config.KALSHI_API_PRIVATE_KEY.replace("\\n", "\n")
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

    # ─── MARKET FETCHING ─────────────────────────────────────────────────────

    def get_markets(self, limit=10):
        all_markets = []

        # 1. Fetch from known good series
        for series in self.GOOD_SERIES:
            if len(all_markets) >= limit * 4:
                break
            try:
                path = "/markets"
                resp = self.session.get(
                    self.BASE_URL + path,
                    headers=self._sign_request("GET", path),
                    params={"status": "open", "series_ticker": series, "limit": 15},
                    timeout=10,
                )
                if resp.status_code == 200:
                    all_markets.extend(resp.json().get("markets", []))
            except Exception:
                continue

        # 2. General fetch — skip sports by ticker/title
        try:
            path = "/markets"
            resp = self.session.get(
                self.BASE_URL + path,
                headers=self._sign_request("GET", path),
                params={"status": "open", "limit": 200},
                timeout=15,
            )
            if resp.status_code == 200:
                for m in resp.json().get("markets", []):
                    ticker = (m.get("ticker") or "").lower()
                    title  = (m.get("title")  or "").lower()
                    if not any(kw in ticker or kw in title for kw in self.SPORTS_KEYWORDS):
                        all_markets.append(m)
        except Exception as e:
            print("General fetch error: " + str(e))

        # Normalize, filter, deduplicate
        normalized  = [self._normalize(m) for m in all_markets if m]
        normalized  = [m for m in normalized if m]
        deduplicated = self._deduplicate(normalized)

        print(f"Found {len(deduplicated)} markets after filtering")

        if not deduplicated:
            print("No real markets found — using demo markets")
            return self._demo_markets()[:limit]

        return deduplicated[:limit]

    # ─── DEDUPLICATION ───────────────────────────────────────────────────────

    def _deduplicate(self, markets: list) -> list:
        """
        Kalshi lists the same underlying event as 10-20 price buckets
        (e.g. 'CPI above 0.1%', 'CPI above 0.2%'... 'CPI above 1.0%').
        We keep only the bucket closest to 50% (most uncertain = most interesting)
        for each unique event, then sort by how close to 50% the price is.
        """
        # Group by event — use first 4 words of title as key
        groups: dict = {}
        for m in markets:
            words = m["question"].split()[:4]
            key   = " ".join(words).lower()
            if key not in groups:
                groups[key] = []
            groups[key].append(m)

        result = []
        for key, group in groups.items():
            if len(group) == 1:
                result.append(group[0])
            else:
                # Pick the market closest to 50% — most tradeable
                best = min(group, key=lambda m: abs(m["outcomes"][0]["price"] - 0.5))
                result.append(best)

        # Sort: markets closest to 50% first (most uncertain = most opportunity)
        result.sort(key=lambda m: abs(m["outcomes"][0]["price"] - 0.5))
        return result

    # ─── NORMALIZE ───────────────────────────────────────────────────────────

    def _normalize(self, raw):
        try:
            # Build best possible title
            title    = (raw.get("title") or "").strip()
            subtitle = (raw.get("subtitle") or raw.get("yes_sub_title") or "").strip()
            if subtitle and subtitle not in title:
                title = title + " — " + subtitle
            if not title:
                return None

            # Skip garbled multi-outcome markets
            if title.lower().startswith("yes ") and "," in title:
                return None
            if title.lower().startswith("no ")  and "," in title:
                return None
            if title.count(",") >= 3:
                return None
            if len(title) < 8:
                return None

            # Get yes price
            yes_price = float(
                raw.get("yes_ask_dollars") or
                raw.get("yes_bid_dollars")  or
                raw.get("last_price_dollars") or
                raw.get("previous_yes_ask_dollars") or
                0.5
            )
            if yes_price > 1:
                yes_price /= 100
            if yes_price <= 0 or yes_price >= 1:
                yes_price = 0.5

            # Skip near-certain / near-impossible — saves Claude API calls
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

    # ─── ORDER PLACEMENT ─────────────────────────────────────────────────────

    def place_order(self, ticker, side, amount_usd, dry_run=True):
        if dry_run:
            print(f"[PAPER] Would bet ${amount_usd} on {side.upper()} for {ticker}")
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
            print("Order error: " + str(e))
            return None

    # ─── HELPERS ─────────────────────────────────────────────────────────────

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
                "question": "Will the S&P 500 close above 5000 this week?",
                "description": "Resolves YES if S&P 500 closes above 5000.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.62}, {"name": "No", "price": 0.38}],
                "volume": 50000, "liquidity": 25000, "days_to_resolve": 5,
                "category": "Financials", "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-002",
                "question": "Will Bitcoin close above $80,000 this week?",
                "description": "Resolves YES if BTC/USD closes above $80,000.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.55}, {"name": "No", "price": 0.45}],
                "volume": 80000, "liquidity": 40000, "days_to_resolve": 5,
                "category": "Crypto", "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-003",
                "question": "Will the Fed hold rates at the May 2026 meeting?",
                "description": "Resolves YES if the Fed makes no rate change in May 2026.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.72}, {"name": "No", "price": 0.28}],
                "volume": 120000, "liquidity": 60000, "days_to_resolve": 14,
                "category": "Economics", "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-004",
                "question": "Will CPI rise more than 0.3% in April 2026?",
                "description": "Resolves YES if monthly CPI change exceeds 0.3%.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.48}, {"name": "No", "price": 0.52}],
                "volume": 35000, "liquidity": 18000, "days_to_resolve": 21,
                "category": "Economics", "url": "https://kalshi.com/demo",
            },
        ]

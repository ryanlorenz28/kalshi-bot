import requests
import base64
import uuid
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.padding import PSS, MGF1
from datetime import datetime, timezone


class KalshiClient:
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, config):
        self.config = config
        self._private_key = self._load_key(config.KALSHI_API_PRIVATE_KEY)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # KEY LOADING
    def _load_key(self, raw: str):
        pk = raw.replace("\\n", "\n").replace("\\\\n", "\n")
        lines = [l.strip() for l in pk.splitlines() if l.strip()]
        body  = [l for l in lines if not l.startswith("-----")]
        if len(body) == 1:
            b = body[0]
            body = [b[i:i+64] for i in range(0, len(b), 64)]
        pem = "-----BEGIN RSA PRIVATE KEY-----\n"
        pem += "\n".join(body)
        pem += "\n-----END RSA PRIVATE KEY-----\n"
        return serialization.load_pem_private_key(pem.encode(), password=None)

    # SIGNING
    def _headers(self, method: str, path: str) -> dict:
        ts  = str(int(datetime.now().timestamp() * 1000))
        full_path = "/trade-api/v2" + path.split("?")[0]
        msg = (ts + method.upper() + full_path).encode("utf-8")
        sig = self._private_key.sign(
            msg,
            PSS(mgf=MGF1(hashes.SHA256()), salt_length=PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY":       self.config.KALSHI_API_KEY_ID,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    # BALANCE
    def get_balance(self):
        path = "/portfolio/balance"
        try:
            r = self.session.get(self.BASE_URL + path, headers=self._headers("GET", path), timeout=15)
            r.raise_for_status()
            return r.json().get("balance", 0) / 100
        except Exception as e:
            print("Error fetching balance: " + str(e))
            return None

    SKIP_KEYWORDS = (
        # Player name indicators
        "rebounds", "assists", "points scored", "wins by", "goals scored",
        "3-pointers", "steals", "blocks", "turnovers", "field goals",
        # Sports events
        "premier league", "champions league", "la liga", "bundesliga",
        "serie a", "ligue 1", "mls", "ufc", "mma", "nascar", "pga",
        # Outcome patterns common in sports
        "over 1.5", "over 2.5", "over 3.5", "over 4.5", "over 0.5",
    )

    SKIP_EVENT_PREFIXES = (
        "KXMVE", "KXNBA", "KXNFL", "KXMLB", "KXNHL",
        "KXSOC", "KXMMA", "KXTEN", "KXNASCAR", "KXPGA",
        "KXUFC", "KXCBB", "KXNCAA",
    )

    # MARKETS
    def get_markets(self, limit=10):
        path = "/markets"
        try:
            r = self.session.get(
                self.BASE_URL + path,
                headers=self._headers("GET", path),
                params={"status": "open", "limit": 200},
                timeout=15,
            )
            r.raise_for_status()
            markets = r.json().get("markets", [])

            result = []
            for m in markets:
                # Filter by event_ticker prefix (most reliable)
                event_ticker = m.get("event_ticker", "")
                if any(event_ticker.startswith(p) for p in self.SKIP_EVENT_PREFIXES):
                    continue
                # Also filter by title keywords as backup
                title = m.get("title", "").lower()
                if any(kw in title for kw in self.SKIP_KEYWORDS):
                    continue
                norm = self._normalize(m)
                if norm:
                    result.append(norm)
                if len(result) >= limit:
                    break

            print(f"Found {len(result)} non-sports markets (from {len(markets)} total)")
            return result if result else self._demo_markets()[:limit]
        except Exception as e:
            print("Error fetching markets: " + str(e))
            return self._demo_markets()[:limit]

    def _normalize(self, raw):
        try:
            yes_price = (raw.get("yes_ask_dollars") or raw.get("yes_bid_dollars")
                         or raw.get("last_price_dollars") or raw.get("previous_yes_ask_dollars") or "0.5")
            yes_price = float(yes_price)
            if yes_price > 1: yes_price /= 100
            if yes_price <= 0 or yes_price >= 1: yes_price = 0.5
            ticker = raw.get("ticker", "unknown")
            return {
                "id": ticker, "question": raw.get("title", "Unknown market"),
                "description": raw.get("rules_primary", ""), "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": yes_price}, {"name": "No", "price": round(1 - yes_price, 4)}],
                "volume": float(raw.get("volume_fp", 0) or 0),
                "liquidity": float(raw.get("liquidity_dollars", 0) or 0),
                "days_to_resolve": self._days_until(raw.get("close_time", "")),
                "category": raw.get("event_ticker", "General"),
                "url": "https://kalshi.com/markets/" + ticker,
            }
        except Exception:
            return None

    # ORDERS
    def place_order(self, ticker, side, amount_usd, price, dry_run=None):
        if dry_run is None: dry_run = self.config.PAPER_TRADING
        price = max(0.01, min(0.99, float(price)))
        count = max(1, int(amount_usd / price))
        actual_cost = round(count * price, 2)
        if dry_run:
            print(f"[PAPER] {side.upper()} {count} contracts @ ${price:.2f} = ${actual_cost:.2f} on {ticker}")
            return {"status": "paper", "ticker": ticker, "side": side, "count": count, "price": price, "cost_usd": actual_cost}
        path = "/portfolio/orders"
        payload = {
            "ticker": ticker, "action": "buy", "side": side.lower(), "type": "limit", "count": count,
            "yes_price": int(price * 100) if side.lower() == "yes" else int((1 - price) * 100),
            "client_order_id": str(uuid.uuid4()), "time_in_force": "good_til_cancelled",
        }
        try:
            r = self.session.post(self.BASE_URL + path, headers=self._headers("POST", path), json=payload, timeout=15)
            if not r.ok:
                print(f"Error placing order ({r.status_code}): {r.text[:300]}")
                print(f"Payload was: {payload}")
                return None
            result = r.json()
            result["cost_usd"] = actual_cost
            result["count"] = count
            return result
        except Exception as e:
            print("Error placing order: " + str(e))
            return None

    def _days_until(self, date_str):
        if not date_str: return 999
        try:
            end = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return max(0, (end - datetime.now(timezone.utc)).days)
        except Exception:
            return 999

    def _demo_markets(self):
        return [
            {"id": "DEMO-001", "question": "Will the S&P 500 close above 5000 today?", "description": "",
             "market_type": "binary", "outcomes": [{"name": "Yes", "price": 0.62}, {"name": "No", "price": 0.38}],
             "volume": 50000, "liquidity": 25000, "days_to_resolve": 1, "category": "Financials", "url": "https://kalshi.com/demo"},
            {"id": "DEMO-002", "question": "Will Bitcoin be above $80000 at end of day?", "description": "",
             "market_type": "binary", "outcomes": [{"name": "Yes", "price": 0.44}, {"name": "No", "price": 0.56}],
             "volume": 80000, "liquidity": 40000, "days_to_resolve": 1, "category": "Crypto", "url": "https://kalshi.com/demo"},
        ]

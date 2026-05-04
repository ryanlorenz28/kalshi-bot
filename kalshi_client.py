from kalshi_python_sync import Configuration, ApiClient
from kalshi_python_sync.api.portfolio_api import PortfolioApi
from kalshi_python_sync.api.markets_api import MarketsApi
from kalshi_python_sync.models.create_order_request import CreateOrderRequest
from datetime import datetime, timezone
import uuid


class KalshiClient:

    def __init__(self, config):
        self.config = config
        pem = self._parse_private_key(config.KALSHI_API_PRIVATE_KEY)

        kalshi_config = Configuration(
            host="https://api.elections.kalshi.com/trade-api/v2"
        )
        kalshi_config.api_key_id      = config.KALSHI_API_KEY_ID
        kalshi_config.private_key_pem = pem

        self._api_client  = ApiClient(kalshi_config)
        self._portfolio   = PortfolioApi(self._api_client)
        self._markets_api = MarketsApi(self._api_client)

    # ─── KEY PARSING ───────────────────────────────────────────────────────────

    def _parse_private_key(self, raw: str) -> str:
        """Normalize private key from any Railway storage format into valid PEM."""
        pk = raw.replace("\\n", "\n").replace("\\\\n", "\n")
        lines = [l.strip() for l in pk.splitlines()]
        lines = [l for l in lines if l]
        body_lines = [l for l in lines if not l.startswith("-----")]
        if len(body_lines) == 1:
            b64 = body_lines[0]
            body_lines = [b64[i:i+64] for i in range(0, len(b64), 64)]
        pem = "-----BEGIN RSA PRIVATE KEY-----\n"
        pem += "\n".join(body_lines)
        pem += "\n-----END RSA PRIVATE KEY-----\n"
        return pem

    # ─── BALANCE ───────────────────────────────────────────────────────────────

    def get_balance(self):
        """Return available balance in dollars, or None on error."""
        try:
            resp = self._portfolio.get_balance()
            return resp.balance / 100
        except Exception as e:
            print("Error fetching balance: " + str(e))
            return None

    # ─── MARKETS ───────────────────────────────────────────────────────────────

    def get_markets(self, limit=10):
        try:
            resp = self._markets_api.get_markets(status="open", limit=limit * 3)
            markets = resp.markets or []
            result = [self._normalize(m) for m in markets]
            result = [m for m in result if m]
            print("Found " + str(len(result)) + " markets after filtering")
            return result[:limit]
        except Exception as e:
            print("Error fetching Kalshi markets: " + str(e))
            return self._demo_markets()[:limit]

    def _normalize(self, raw):
        try:
            yes_price = (
                getattr(raw, "yes_ask_dollars", None)
                or getattr(raw, "yes_bid_dollars", None)
                or getattr(raw, "last_price_dollars", None)
                or "0.5"
            )
            yes_price = float(yes_price)
            if yes_price > 1:
                yes_price = yes_price / 100
            if yes_price <= 0 or yes_price >= 1:
                yes_price = 0.5

            ticker     = getattr(raw, "ticker", "unknown")
            close_time = str(getattr(raw, "close_time", "") or "")

            return {
                "id":              ticker,
                "question":        getattr(raw, "title", "Unknown market"),
                "description":     getattr(raw, "rules_primary", "") or "",
                "market_type":     "binary",
                "outcomes": [
                    {"name": "Yes", "price": yes_price},
                    {"name": "No",  "price": round(1 - yes_price, 4)},
                ],
                "volume":          float(getattr(raw, "volume_fp", 0) or 0),
                "liquidity":       float(getattr(raw, "liquidity_dollars", 0) or 0),
                "days_to_resolve": self._days_until(close_time),
                "category":        getattr(raw, "event_ticker", "General") or "General",
                "url":             "https://kalshi.com/markets/" + ticker,
            }
        except Exception:
            return None

    # ─── ORDERS ────────────────────────────────────────────────────────────────

    def place_order(self, ticker, side, amount_usd, price, dry_run=None):
        """
        Place an order. dry_run=None uses config.PAPER_TRADING.
        price: current price of the chosen side (0.0-1.0)
        """
        if dry_run is None:
            dry_run = self.config.PAPER_TRADING

        price      = max(0.01, min(0.99, float(price)))
        count      = max(1, int(amount_usd / price))
        actual_cost = round(count * price, 2)

        if dry_run:
            print(f"[PAPER] {side.upper()} {count} contracts @ ${price:.2f} = ${actual_cost:.2f} on {ticker}")
            return {"status": "paper", "ticker": ticker, "side": side,
                    "count": count, "price": price, "cost_usd": actual_cost}

        try:
            yes_price_cents = int(price * 100) if side.lower() == "yes" else int((1 - price) * 100)

            order_request = CreateOrderRequest(
                ticker         = ticker,
                action         = "buy",
                side           = side.lower(),
                type           = "limit",
                count          = count,
                yes_price      = yes_price_cents,
                client_order_id= str(uuid.uuid4()),
                time_in_force  = "fill_or_kill",
            )
            resp = self._portfolio.create_order(order_request)
            return {
                "status":   getattr(resp, "status", "submitted"),
                "order_id": getattr(resp, "order_id", ""),
                "count":    count,
                "cost_usd": actual_cost,
            }
        except Exception as e:
            print("Error placing order: " + str(e))
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
                "id": "DEMO-001", "question": "Will the S&P 500 close above 5000 today?",
                "description": "", "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.62}, {"name": "No", "price": 0.38}],
                "volume": 50000, "liquidity": 25000, "days_to_resolve": 1,
                "category": "Financials", "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-002", "question": "Will Bitcoin be above $80000 at end of day?",
                "description": "", "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.44}, {"name": "No", "price": 0.56}],
                "volume": 80000, "liquidity": 40000, "days_to_resolve": 1,
                "category": "Crypto", "url": "https://kalshi.com/demo",
            },
        ]

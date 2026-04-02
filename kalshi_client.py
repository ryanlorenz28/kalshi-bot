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
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def _sign_request(self, method, path):
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        message = timestamp + method.upper() + path
        try:
            pk = self.config.KALSHI_API_PRIVATE_KEY
            pk = pk.replace("\\n", "\n")
            if not pk.startswith("-----"):
                pk = "-----BEGIN RSA PRIVATE KEY-----\n" + pk + "\n-----END RSA PRIVATE KEY-----"
            private_key = serialization.load_pem_private_key(pk.encode(), password=None)
            signature = private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
            return {"KALSHI-ACCESS-KEY": self.config.KALSHI_API_KEY_ID, "KALSHI-ACCESS-TIMESTAMP": timestamp, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode()}
        except Exception as e:
            print("Signing error: " + str(e))
            return {}

    def get_markets(self, limit=10):
        path = "/markets"
        try:
            resp = self.session.get(self.BASE_URL + path, headers=self._sign_request("GET", path), params={"status": "open", "limit": limit * 3}, timeout=15)
            resp.raise_for_status()
            markets = resp.json().get("markets", [])
            result = [self._normalize(m) for m in markets if m]
            result = [m for m in result if m]
            print("Found " + str(len(result)) + " markets")
            return result[:limit]
        except Exception as e:
            print("Error fetching markets: " + str(e))
            return self._demo_markets()[:limit]

    def _normalize(self, raw):
        try:
            yes_price = raw.get("yes_ask_dollars") or raw.get("yes_bid_dollars") or raw.get("last_price_dollars") or "0.5"
            yes_price = float(yes_price)
            if ye

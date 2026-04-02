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
                "KALSHI-ACCESS-TIMESTAMP": times

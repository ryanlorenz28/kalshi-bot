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

    def _load_key(self, raw):
        pk = raw.replace("\\n", "\n").replace("\\\\n", "\n")
        lines = [l.strip() for l in pk.splitlines() if l.strip()]
        body = [l for l in lines if not l.startswith("-----")]
        if len(body) == 1:
            b = body[0]
            body = [b[i:i+64] for i in range(0, len(b), 64)]
        pem = "-----BEGIN RSA PRIVATE KEY-----\n"
        pem += "\n".join(body)
        pem += "\n-----END RSA PRIVATE KEY-----\n"
        return serialization.load_pem_private_key(pem.encode(), password=None)

    def _headers(self, method, path):
        ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        msg = (ts + method.upper() + path).encode("utf-8")
        sig = self._private_key.sign(
            msg,
            PSS(mgf=MGF1(hashes.SHA256()), salt_length=PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY

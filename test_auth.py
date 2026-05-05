"""
test_auth.py — Run this once to verify Kalshi API authentication.
Add this file to your GitHub repo, then in Railway run:
  python test_auth.py
Or just check the logs after deploy — it runs and exits.
"""

import requests
import base64
import os
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.padding import PSS, MGF1
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

BASE_URL   = "https://api.elections.kalshi.com/trade-api/v2"
KEY_ID     = os.getenv("KALSHI_API_KEY_ID", "")
PRIV_KEY   = os.getenv("KALSHI_API_PRIVATE_KEY", "")

def load_key(raw):
    pk = raw.replace("\\n", "\n").replace("\\\\n", "\n")
    lines = [l.strip() for l in pk.splitlines() if l.strip()]
    body  = [l for l in lines if not l.startswith("-----")]
    if len(body) == 1:
        b = body[0]
        body = [b[i:i+64] for i in range(0, len(b), 64)]
    pem = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END RSA PRIVATE KEY-----\n"
    return serialization.load_pem_private_key(pem.encode(), password=None)

def make_headers(key, method, path):
    ts  = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    msg = (ts + method.upper() + path).encode("utf-8")
    sig = key.sign(msg, PSS(mgf=MGF1(hashes.SHA256()), salt_length=PSS.DIGEST_LENGTH), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY":       KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "Accept":                  "application/json",
        "Content-Type":            "application/json",
    }

print("\n=== KALSHI AUTH TEST ===\n")

try:
    private_key = load_key(PRIV_KEY)
    print("✅ Private key loaded successfully")
except Exception as e:
    print(f"❌ Key load failed: {e}")
    exit(1)

# Test 1: Balance
path = "/portfolio/balance"
r = requests.get(BASE_URL + path, headers=make_headers(private_key, "GET", path), timeout=15)
print(f"\nGET /portfolio/balance → {r.status_code}")
if r.status_code == 200:
    bal = r.json().get("balance", 0) / 100
    print(f"✅ Balance: ${bal:.2f}")
else:
    print(f"❌ Response: {r.text[:300]}")

# Test 2: Markets (no auth needed but confirms connectivity)
path = "/markets"
r = requests.get(BASE_URL + path, headers=make_headers(private_key, "GET", path),
                 params={"status": "open", "limit": 1}, timeout=15)
print(f"\nGET /markets → {r.status_code}")
if r.status_code == 200:
    markets = r.json().get("markets", [])
    print(f"✅ Got {len(markets)} market(s)")
else:
    print(f"❌ Response: {r.text[:300]}")

# Test 3: Portfolio positions
path = "/portfolio/positions"
r = requests.get(BASE_URL + path, headers=make_headers(private_key, "GET", path), timeout=15)
print(f"\nGET /portfolio/positions → {r.status_code}")
if r.status_code == 200:
    print(f"✅ Positions endpoint works: {r.text[:100]}")
else:
    print(f"❌ Response: {r.text[:300]}")

print("\n=== DONE ===\n")

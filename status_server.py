"""
status_server.py — Lightweight HTTP server exposing /status endpoint.
Run in a background thread alongside the bot.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import os

_state_ref = None
_client_ref = None
_config_ref = None

def start(state, client, config):
    global _state_ref, _client_ref, _config_ref
    _state_ref = state
    _client_ref = client
    _config_ref = config

    port = int(os.environ.get("PORT", 8080))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            if self.path == "/status":
                balance = None
                try:
                    balance = _client_ref.get_balance()
                except Exception:
                    pass

                payload = {
                    "balance_usd":        balance,
                    "real_money_spent":   _state_ref.get("real_money_spent", 0),
                    "daily_loss":         _state_ref.get("daily_loss", 0),
                    "real_money_limit":   _config_ref.REAL_MONEY_LIMIT,
                    "daily_loss_limit":   _config_ref.DAILY_LOSS_LIMIT,
                    "open_positions":     _state_ref.get("open_positions", {}),
                    "paper_trading":      _config_ref.PAPER_TRADING,
                }
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.end_headers()

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[status] Server running on port {port}")

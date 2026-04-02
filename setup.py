import os

folder = "kalshi_bot"
os.makedirs(folder, exist_ok=True)

files = {}

files["requirements.txt"] = """anthropic
requests
python-dotenv
schedule
colorama
cryptography"""

files["logger.py"] = """import logging
import sys
from colorama import Fore, Style

class BotLogger:
    def __init__(self, log_file="bot_log.txt", log_to_file=True):
        self._logger = logging.getLogger("KalshiBot")
        self._logger.setLevel(logging.DEBUG)
        if not self._logger.handlers:
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(ch)
            if log_to_file:
                fh = logging.FileHandler(log_file, encoding="utf-8")
                fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
                self._logger.addHandler(fh)
    def info(self, msg): self._logger.info(msg)
    def warning(self, msg): self._logger.warning(Fore.YELLOW + f"WARNING: {msg}" + Style.RESET_ALL)
    def error(self, msg): self._logger.error(Fore.RED + f"ERROR: {msg}" + Style.RESET_ALL)"""

files["bot_config.py"] = """import os
from dotenv import load_dotenv
load_dotenv()

class BotConfig:
    ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
    KALSHI_API_KEY_ID      = os.getenv("KALSHI_API_KEY_ID", "")
    KALSHI_API_PRIVATE_KEY = os.getenv("KALSHI_API_PRIVATE_KEY", "")
    PAPER_TRADING          = True
    PAPER_STARTING_BALANCE = 1000.00
    MARKETS_TO_SCAN        = 10
    CYCLE_INTERVAL_MINUTES = 30
    DELAY_BETWEEN_ANALYSES = 2
    MIN_CONFIDENCE_TO_TRADE = 0.65
    MAX_BET_SIZE            = 25.00
    MIN_BET_SIZE            = 5.00
    MAX_OPEN_POSITIONS      = 3
    KELLY_FRACTION          = 0.25
    CLAUDE_MODEL            = "claude-sonnet-4-6"
    MAX_TOKENS              = 1500
    LOG_FILE                = "bot_log.txt"
    LOG_TRADES              = True
    TRADE_LOG_FILE          = "trades.csv"

    def validate(self):
        if not self.ANTHROPIC_API_KEY:
            print("Missing ANTHROPIC_API_KEY")
            return False
        if not self.KALSHI_API_KEY_ID:
            print("Missing KALSHI_API_KEY_ID")
            return False
        return True

    def bet_size(self, confidence, available_balance):
        edge = confidence - 0.5
        if edge <= 0:
            return 0.0
        kelly_bet = (edge / (1 - confidence + 1e-9)) * self.KELLY_FRACTION
        kelly_bet = min(kelly_bet, 0.20)
        raw_amount = available_balance * kelly_bet
        return round(max(self.MIN_BET_SIZE, min(self.MAX_BET_SIZE, raw_amount)), 2)"""

files["kalshi_client.py"] = """import requests
import json
import base64
from datetime import datetime, timezone

class KalshiClient:
    BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _auth_headers(self, method, path):
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
            message = timestamp + method.upper() + path
            pk = self.config.KALSHI_API_PRIVATE_KEY
            if not pk.startswith("-----"):
                pk = f"-----BEGIN RSA PRIVATE KEY-----\\n{pk}\\n-----END RSA PRIVATE KEY-----"
            private_key = serialization.load_pem_private_key(pk.encode(), password=None)
            signature = private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
            return {
                "KALSHI-ACCESS-KEY": self.config.KALSHI_API_KEY_ID,
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            }
        except Exception as e:
            print(f"Auth error: {e}")
            return {}

    def get_markets(self, limit=10):
        path = "/markets"
        try:
            resp = self.session.get(
                f"{self.BASE_URL}{path}",
                headers=self._auth_headers("GET", path),
                params={"status": "open", "limit": limit * 3},
                timeout=15,
            )
            resp.raise_for_status()
            markets = resp.json().get("markets", [])
            result = [self._normalize(m) for m in markets if m]
            return [m for m in result if m][:limit]
        except Exception as e:
            print(f"Error fetching markets: {e}")
            return self._demo_markets()[:limit]

    def _normalize(self, raw):
        try:
            yes_price = float(raw.get("yes_ask", 0.5))
            return {
                "id": raw.get("ticker", "unknown"),
                "question": raw.get("title", "Unknown"),
                "description": raw.get("rules_primary", ""),
                "market_type": "binary",
                "outcomes": [
                    {"name": "Yes", "price": yes_price},
                    {"name": "No", "price": round(1 - yes_price, 4)},
                ],
                "volume": float(raw.get("volume", 0) or 0),
                "liquidity": float(raw.get("open_interest", 0) or 0),
                "days_to_resolve": self._days_until(raw.get("close_time", "")),
                "category": raw.get("category", "General"),
                "url": f"https://kalshi.com/markets/{raw.get('ticker', '')}",
            }
        except Exception:
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
                "volume": 50000, "liquidity": 25000,
                "days_to_resolve": 1, "category": "Financials",
                "url": "https://kalshi.com/demo",
            },
            {
                "id": "DEMO-002",
                "question": "Will Bitcoin be above $80,000 at end of day?",
                "description": "Resolves YES if BTC/USD is above 80000.",
                "market_type": "binary",
                "outcomes": [{"name": "Yes", "price": 0.44}, {"name": "No", "price": 0.56}],
                "volume": 80000, "liquidity": 40000,
                "days_to_resolve": 1, "category": "Crypto",
                "url": "https://kalshi.com/demo",
            },
        ]"""

files["ai_analyzer.py"] = """import anthropic

class AIAnalyzer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def analyze_market(self, market):
        question  = market.get("question", "")
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)
        no_price  = 1 - yes_price
        category  = market.get("category", "General")
        days_left = market.get("days_to_resolve", 1)

        prompt = f\"\"\"You are an expert prediction market trader analyzing a Kalshi market.

Market: {question}
Category: {category}
Days until resolution: {days_left}
Current YES price: {yes_price:.2%}
Current NO price: {no_price:.2%}

Analyze this market:
1. What is the true probability based on your knowledge?
2. Is there at least 8% edge?
3. Which side has better value?

Respond in EXACTLY this format:
TRADE: YES or NO or SKIP
CONFIDENCE: 0.0 to 1.0
MY_PROBABILITY: 0.0 to 1.0
EDGE: decimal difference between your probability and market price
REASONING: one paragraph
KEY_RISKS: one sentence\"\"\"

        try:
            msg = self.client.messages.create(
                model=self.config.CLAUDE_MODEL,
                max_tokens=self.config.MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )
            return self._parse(msg.content[0].text)
        except Exception as e:
            self.logger.error(f"Analysis error: {e}")
            return {"should_trade": False, "reason": str(e)}

    def _parse(self, text):
        result = {
            "should_trade": False,
            "outcome_to_buy": None,
            "confidence": 0.0,
            "my_probability": 0.5,
            "edge": 0.0,
            "reasoning": "",
            "key_risks": "",
            "reason": "low confidence",
        }
        for line in text.strip().split("\\n"):
            if line.startswith("TRADE:"):
                val = line.split(":", 1)[1].strip()
                if val in ("YES", "NO"):
                    result["outcome_to_buy"] = val.capitalize()
            elif line.startswith("CONFIDENCE:"):
                try: result["confidence"] = float(line.split(":", 1)[1].strip())
                except: pass
            elif line.startswith("MY_PROBABILITY:"):
                try: result["my_probability"] = float(line.split(":", 1)[1].strip())
                except: pass
            elif line.startswith("EDGE:"):
                try: result["edge"] = float(line.split(":", 1)[1].strip())
                except: pass
            elif line.startswith("REASONING:"):
                result["reasoning"] = line.split(":", 1)[1].strip()
            elif line.startswith("KEY_RISKS:"):
                result["key_risks"] = line.split(":", 1)[1].strip()

        if (result["outcome_to_buy"] and
                result["confidence"] >= self.config.MIN_CONFIDENCE_TO_TRADE and
                abs(result["edge"]) >= 0.08):
            result["should_trade"] = True
            result["reason"] = "opportunity found"
        else:
            result["reason"] = f"Edge {abs(result['edge']):.1%} or confidence too low"
        return result"""

files["main.py"] = """import time
import schedule
import sys
import csv
import os
from datetime import datetime
from colorama import init, Fore, Style
from bot_config import BotConfig
from kalshi_client import KalshiClient
from ai_analyzer import AIAnalyzer
from logger import BotLogger

init(autoreset=True)

def print_banner():
    print(Fore.CYAN + \"\"\"
╔══════════════════════════════════════════════════════╗
║         KALSHI AI TRADING BOT                        ║
║         Powered by Claude AI                         ║
║         Mode: PAPER TRADING (no real money)          ║
╚══════════════════════════════════════════════════════╝
    \"\"\" + Style.RESET_ALL)

def log_trade(trade, config):
    if not config.LOG_TRADES:
        return
    is_new = not os.path.exists(config.TRADE_LOG_FILE)
    with open(config.TRADE_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(trade.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(trade)

def run_cycle(client, analyzer, logger, config, state):
    logger.info("=" * 55)
    logger.info("Scanning Kalshi for active markets...")
    markets = client.get_markets(limit=config.MARKETS_TO_SCAN)
    markets = [m for m in markets if m]

    if not markets:
        logger.warning("No markets found. Retrying next cycle.")
        return

    logger.info(f"Found {len(markets)} markets to analyze")

    for i, market in enumerate(markets):
        if len(state["open_positions"]) >= config.MAX_OPEN_POSITIONS:
            logger.info(f"Position limit reached.")
            break
        if market["id"] in state["open_positions"]:
            continue

        logger.info(f"Analyzing market {i+1}/{len(markets)}: {market['question'][:60]}...")
        analysis = analyzer.analyze_market(market)

        if analysis.get("should_trade"):
            outcome     = analysis["outcome_to_buy"]
            confidence  = analysis["confidence"]
            bet_amount  = config.bet_size(confidence, config.PAPER_STARTING_BALANCE)
            entry_price = market["outcomes"][0]["price"] if outcome == "Yes" else market["outcomes"][1]["price"]

            logger.info(Fore.GREEN + f\"\"\"
  PAPER TRADE PLACED
     Market:    {market['question'][:55]}
     Bet:       BUY '{outcome}'
     Amount:    ${bet_amount:.2f} @ {entry_price:.3f}
     AI says:   {analysis['my_probability']:.0%} true prob vs {entry_price:.0%} market
     Edge:      {analysis['edge']:+.1%}
     Reasoning: {analysis['reasoning'][:100]}\"\"\" + Style.RESET_ALL)

            trade = {
                "timestamp":      datetime.now().isoformat(),
                "market_id":      market["id"],
                "question":       market["question"],
                "outcome":        outcome,
                "entry_price":    entry_price,
                "amount_usd":     bet_amount,
                "ai_confidence":  confidence,
                "ai_probability": analysis["my_probability"],
                "edge":           analysis["edge"],
                "reasoning":      analysis["reasoning"],
                "key_risks":      analysis["key_risks"],
                "status":         "open",
                "url":            market.get("url", ""),
            }
            state["open_positions"][market["id"]] = trade
            state["balance"] -= bet_amount
            log_trade(trade, config)
        else:
            logger.info(f"  Skipping: {analysis.get('reason', 'low confidence')}")

        time.sleep(config.DELAY_BETWEEN_ANALYSES)

    logger.info(f"Balance: ${state['balance']:,.2f} | Open positions: {len(state['open_positions'])}")
    logger.info(f"Next cycle in {config.CYCLE_INTERVAL_MINUTES} minutes...")

def main():
    print_banner()
    config = BotConfig()
    if not config.validate():
        print(Fore.RED + "Check your Railway environment variables.")
        sys.exit(1)

    logger   = BotLogger(config.LOG_FILE, True)
    client   = KalshiClient(config)
    analyzer = AIAnalyzer(config, logger)
    state    = {"balance": config.PAPER_STARTING_BALANCE, "open_positions": {}}

    logger.info(f"Bot started! Scanning {config.MARKETS_TO_SCAN} markets every {config.CYCLE_INTERVAL_MINUTES} min")
    logger.info(f"Starting paper balance: ${config.PAPER_STARTING_BALANCE:,.2f}")

    run_cycle(client, analyzer, logger, config, state)
    schedule.every(config.CYCLE_INTERVAL_MINUTES).minutes.do(
        run_cycle, client, analyzer, logger, config, state
    )

    print(Fore.GREEN + "Bot is running! Press Ctrl+C to stop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print(Fore.YELLOW + "Bot stopped.")
        print(f"Final balance: ${state['balance']:,.2f}")

if __name__ == "__main__":
    main()"""

for filename, content in files.items():
    filepath = os.path.join(folder, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content.strip())
    print(f"Created {filename}")

print("\nAll files created in kalshi_bot folder on your Desktop!")
```

Save it, then in Terminal run:
```
cd Desktop
python setup.py
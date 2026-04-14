"""
bot_config.py — All settings for the Kalshi trading bot.
"""
import os
from dotenv import load_dotenv
load_dotenv()

class BotConfig:
    # ─── API KEYS ─────────────────────────────────────────────
    ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
    KALSHI_API_KEY_ID      = os.getenv("KALSHI_API_KEY_ID", "")
    KALSHI_API_PRIVATE_KEY = os.getenv("KALSHI_API_PRIVATE_KEY", "")
    NEWS_API_KEY           = os.getenv("NEWS_API_KEY", "")   # optional, newsapi.org

    # ─── TRADING MODE ─────────────────────────────────────────
    PAPER_TRADING          = True
    PAPER_STARTING_BALANCE = 1000.00

    # ─── SCANNING ─────────────────────────────────────────────
    MARKETS_TO_SCAN        = 15     # Reduced from 30 to save API costs
    CYCLE_INTERVAL_MINUTES = 60     # Run every hour instead of 30 min
    DELAY_BETWEEN_ANALYSES = 3

    # ─── TRADING RULES ────────────────────────────────────────
    MIN_CONFIDENCE_TO_TRADE = 0.65
    MAX_BET_SIZE            = 25.00
    MIN_BET_SIZE            = 5.00
    MAX_OPEN_POSITIONS      = 3
    KELLY_FRACTION          = 0.25

    # ─── POSITION MANAGER ─────────────────────────────────────
    TAKE_PROFIT_PCT = 0.50    # Close trade if up 50%
    STOP_LOSS_PCT   = 0.40    # Close trade if down 40%

    # ─── AI SETTINGS ──────────────────────────────────────────
    CLAUDE_MODEL           = "claude-sonnet-4-6"
    MAX_TOKENS             = 1500
    NEWS_ARTICLES_TO_FETCH = 3

    # ─── LOGGING ──────────────────────────────────────────────
    LOG_TO_FILE    = True
    LOG_FILE       = "bot_log.txt"
    LOG_TRADES     = True
    TRADE_LOG_FILE = "trades.csv"

    def validate(self):
        if not self.ANTHROPIC_API_KEY:
            print("❌ Missing ANTHROPIC_API_KEY")
            return False
        if not self.KALSHI_API_KEY_ID:
            print("❌ Missing KALSHI_API_KEY_ID")
            return False
        if not self.NEWS_API_KEY:
            print("⚠️  NEWS_API_KEY not set — using Reddit + DuckDuckGo for news (free)")
        return True

    def bet_size(self, confidence: float, available_balance: float) -> float:
        edge = confidence - 0.5
        if edge <= 0:
            return 0.0
        kelly_bet  = (edge / (1 - confidence + 1e-9)) * self.KELLY_FRACTION
        kelly_bet  = min(kelly_bet, 0.20)
        raw_amount = available_balance * kelly_bet
        amount     = max(self.MIN_BET_SIZE, min(self.MAX_BET_SIZE, raw_amount))
        return round(amount, 2)

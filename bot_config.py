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
    NEWS_API_KEY           = os.getenv("NEWS_API_KEY", "")   # optional — bot works without it

    # ─── TRADING MODE ─────────────────────────────────────────
    # Set PAPER_TRADING = False to enable real orders.
    # The bot will only spend up to REAL_MONEY_LIMIT in real money;
    # any trades beyond that cap fall back to paper mode automatically.
    PAPER_TRADING          = False        # ← flipped to live
    PAPER_STARTING_BALANCE = 1000.00     # used only when fully paper trading

    # ─── LIVE TRADING SAFETY LIMITS ───────────────────────────
    REAL_MONEY_LIMIT       = 150.00      # hard cap on total real-money spend
    DAILY_LOSS_LIMIT       = 50.00       # bot shuts down if real losses hit this today
    MAX_BET_SIZE           = 15.00       # tightened for live start (was $25)
    MIN_BET_SIZE           = 5.00

    # ─── SCANNING ─────────────────────────────────────────────
    MARKETS_TO_SCAN        = 10
    CYCLE_INTERVAL_MINUTES = 30
    DELAY_BETWEEN_ANALYSES = 2

    # ─── TRADING RULES ────────────────────────────────────────
    MIN_CONFIDENCE_TO_TRADE = 0.60
    MIN_EDGE_TO_TRADE       = 0.05       # lowered from 6% to 5% to catch near-threshold opportunities
    MAX_OPEN_POSITIONS      = 5          # increased since we now cap per-series
    MAX_EXPOSURE_PER_SERIES = 30.00      # max $ spent on any single company/topic
    KELLY_FRACTION          = 0.25

    # ─── POSITION MANAGER ─────────────────────────────────────
    TAKE_PROFIT_PCT = 0.50
    STOP_LOSS_PCT   = 0.40

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
            print("❌ Missing ANTHROPIC_API_KEY in your .env file")
            return False
        if not self.KALSHI_API_KEY_ID:
            print("❌ Missing KALSHI_API_KEY_ID in your .env file")
            return False
        if not self.PAPER_TRADING and not self.KALSHI_API_PRIVATE_KEY:
            print("❌ Missing KALSHI_API_PRIVATE_KEY — required for live trading")
            return False
        return True

    def bet_size(self, confidence: float, available_balance: float) -> float:
        edge = confidence - 0.5
        if edge <= 0:
            return 0.0
        kelly_bet = (edge / (1 - confidence + 1e-9)) * self.KELLY_FRACTION
        kelly_bet = min(kelly_bet, 0.20)
        raw_amount = available_balance * kelly_bet
        amount = max(self.MIN_BET_SIZE, min(self.MAX_BET_SIZE, raw_amount))
        return round(amount, 2)

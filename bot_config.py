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
    REAL_MONEY_LIMIT        = 450.00     # updated to reflect actual open position exposure
    TOTAL_EXPOSURE_LIMIT    = 500.00     # absolute hard stop — bot stops trading at this total
    DAILY_LOSS_LIMIT        = 50.00      # bot shuts down if real losses hit this today
    MAX_BET_SIZE            = 15.00      # max single bet
    MIN_BET_SIZE            = 3.00       # min single bet

    # ─── SCANNING ─────────────────────────────────────────────
    MARKETS_TO_SCAN        = 20   # raised — with 9+ positions held, need more to find new ones
    CYCLE_INTERVAL_MINUTES = 30
    DELAY_BETWEEN_ANALYSES = 1    # lowered from 2s — no reason to wait longer

    # ─── TRADING RULES ────────────────────────────────────────
    MIN_CONFIDENCE_TO_TRADE = 0.60
    MIN_EDGE_TO_TRADE       = 0.05       # lowered from 6% to 5% to catch near-threshold opportunities
    MAX_OPEN_POSITIONS      = 15         # raised to accommodate loaded existing positions
    MAX_EXPOSURE_PER_SERIES = 30.00      # max $ spent on any single company/topic
    KELLY_FRACTION          = 0.10       # 10% Kelly (was 0.25)
    FILL_BUFFER             = 0.03       # place_order pays the ask + 3¢; size on that price

    # ─── POSITION MANAGER ─────────────────────────────────────
    TAKE_PROFIT_PCT      = 0.40   # lowered from 50% — exit when up 40%
    TAKE_PROFIT_MIN_PRICE= 0.85   # OR when trading above 85¢ (near-certain resolution)
    STOP_LOSS_PCT        = 0.40

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

    def bet_size(self, win_prob: float, price: float, available_balance: float) -> float:
        """Fractional Kelly on the real edge: win_prob is the AI's probability
        for the side being bought, price is that side's market price.
        Returns 0.0 (skip the trade) when there's no edge or the bet would be
        under MIN_BET_SIZE."""
        cost = min(0.99, price + self.FILL_BUFFER)
        kelly = (win_prob - cost) / (1 - cost)
        if kelly <= 0:
            return 0.0
        kelly_bet = min(kelly * self.KELLY_FRACTION, 0.10)   # cap at 10% of balance
        raw_amount = available_balance * kelly_bet
        if raw_amount < self.MIN_BET_SIZE:
            return 0.0
        return round(min(self.MAX_BET_SIZE, raw_amount), 2)

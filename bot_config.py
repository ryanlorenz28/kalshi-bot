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
    FRED_API_KEY           = os.getenv("FRED_API_KEY", "")   # jobs/Fed/GDP/CPI live data; without it those show "unavailable"

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
    MIN_EDGE_TO_TRADE       = 0.08       # raised from 5% (Nov 2026): fees + 3¢ buffer ate most of a 5% edge
    MAX_OPEN_POSITIONS      = 15         # raised to accommodate loaded existing positions
    MAX_EXPOSURE_PER_SERIES = 30.00      # max $ spent on any single company/topic
    KELLY_FRACTION          = 0.10       # 10% Kelly (was 0.25)
    FILL_BUFFER             = 0.03       # place_order pays the ask + 3¢; size on that price
    MAX_SELL_SPREAD         = 0.10       # exits (stop-loss / take-profit) hold if bid-ask spread is wider
    KALSHI_FEE_RATE         = 0.07       # taker fee per contract = rate × price × (1 − price)

    # ─── POSITION MANAGER ─────────────────────────────────────
    TAKE_PROFIT_PCT      = 0.40   # lowered from 50% — exit when up 40%
    TAKE_PROFIT_MIN_PRICE= 0.85   # OR when trading above 85¢ (near-certain resolution)
    STOP_LOSS_PCT        = 0.40

    # ─── AI SETTINGS ──────────────────────────────────────────
    CLAUDE_MODEL           = "claude-sonnet-4-6"
    MAX_TOKENS             = 1500
    NEWS_ARTICLES_TO_FETCH = 3

    # ─── DECISION CACHE (cuts Claude API cost) ────────────────
    # Claude is only re-asked about a market when its price moves, the answer gets old,
    # or (macro series) new FRED data is released. Set CACHE_ENABLED = False to turn off.
    CACHE_ENABLED      = True
    CACHE_PRICE_MOVE   = 0.03    # re-ask if the YES price moves 3 cents or more
    CACHE_TTL_NEAR_MIN = 30      # markets resolving within 1 day: re-ask after 30 min
    CACHE_TTL_WEEK_MIN = 120     # resolving within 7 days: after 2 hours
    CACHE_TTL_FAR_MIN  = 360     # everything else (and macro series): after 6 hours
    CACHE_NO_EDGE_BELOW   = 0.02  # last answer was SKIP with under 2% edge, and resolves >7 days out:
    CACHE_TTL_NO_EDGE_MIN = 1440  #   reuse it for 24 hours (price moves / FRED releases still re-ask)

    # ─── RESTING (MAKER) BUY ORDERS — trial, off by default ────
    # Buy at/near the bid with a post-only order that waits on the book, instead of
    # paying the ask + 3¢. Sells (stop-loss / take-profit) always stay immediate.
    MAKER_ORDERS             = True     # trial started Nov 2026 (Ryan approved), 1 waiting order at a time
    MAKER_EXPIRY_MIN         = 120    # unfilled orders expire after 2 hours
    MAKER_MAX_RESTING        = 1      # at most this many waiting orders at once
    MAKER_IMPROVE_MIN_SPREAD = 0.03   # bid +1¢ when the spread is at least 3¢, else join the bid

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

    def bet_size(self, win_prob: float, price: float, available_balance: float, fill_buffer: float = None) -> float:
        """Fractional Kelly on the real edge: win_prob is the AI's probability
        for the side being bought, price is that side's market price.
        Returns 0.0 (skip the trade) when there's no edge or the bet would be
        under MIN_BET_SIZE."""
        buffer = self.FILL_BUFFER if fill_buffer is None else fill_buffer   # 0 for resting orders (pay the limit price)
        cost = min(0.99, price + buffer)
        cost += self.KALSHI_FEE_RATE * cost * (1 - cost)   # Kalshi's fee is paid per contract on top
        kelly = (win_prob - cost) / (1 - cost)
        if kelly <= 0:
            return 0.0
        kelly_bet = min(kelly * self.KELLY_FRACTION, 0.10)   # cap at 10% of balance
        raw_amount = available_balance * kelly_bet
        if raw_amount < self.MIN_BET_SIZE:
            return 0.0
        return round(min(self.MAX_BET_SIZE, raw_amount), 2)

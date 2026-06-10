"""
=============================================================
  KALSHI AI TRADING BOT - MAIN ENTRY POINT
=============================================================
  pip install anthropic requests python-dotenv schedule colorama cryptography
  Run: python main.py
=============================================================
"""

import time
import schedule
import sys
import csv
import os
import status_server
from datetime import datetime, date
from colorama import init, Fore, Style
from bot_config import BotConfig
from kalshi_client import KalshiClient
from ai_analyzer import AIAnalyzer
from logger import BotLogger

init(autoreset=True)


def print_banner(config):
    mode = "🔴 LIVE TRADING" if not config.PAPER_TRADING else "📄 PAPER TRADING"
    limit = f"${config.REAL_MONEY_LIMIT:.0f} real-money cap" if not config.PAPER_TRADING else "no real money"
    print(Fore.CYAN + f"""
╔══════════════════════════════════════════════════════╗
║         KALSHI AI TRADING BOT                        ║
║         Powered b

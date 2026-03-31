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
from datetime import datetime
from colorama import init, Fore, Style
from bot_config import BotConfig
from kalshi_client import KalshiClient
from ai_analyzer import AIAnalyzer
from logger import BotLogger

init(autoreset=True)


def print_banner():
    print(Fore.CYAN + """
╔══════════════════════════════════════════════════════╗
║         KALSHI AI TRADING BOT                        ║
║         Powered by Claude AI                         ║
║         Mode: PAPER TRADING (no real money)          ║
╚══════════════════════════════════════════════════════╝
    """ + Style.RESET_ALL)


def log_trade(trade: dict, config):
    """Save trade to CSV."""
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
    
    # Show open positions at the start of every cycle
    if state["open_positions"]:
        logger.info(f"\n📋 CURRENT OPEN POSITIONS ({len(state['open_positions'])}):")
        for pos in state["open_positions"].values():
            logger.info(f"  • {pos['question'][:50]}...")
            logger.info(f"    Bet: {pos['outcome']} @ ${pos['amount_usd']:.2f} | Confidence: {pos['ai_confidence']:.0%} | Edge: {pos['edge']:+.0%}")
    else:
        logger.info("📋 No open positions")

    logger.info("Scanning Kalshi for active markets...")

    markets = client.get_markets(limit=config.MARKETS_TO_SCAN)

    if not markets:
        logger.warning("No markets found. Retrying next cycle.")
        return

    # Filter out None markets
    markets = [m for m in markets if m]
    logger.info(f"✅ Found {len(markets)} markets to analyze")

    for i, market in enumerate(markets):
        # Stop if position limit reached
        if len(state["open_positions"]) >= config.MAX_OPEN_POSITIONS:
            logger.info(f"⛔ Position limit ({config.MAX_OPEN_POSITIONS}) reached.")
            break

        # Skip if already have position
        if market["id"] in state["open_positions"]:
            continue

        logger.info(f"\n🤖 Analyzing market {i+1}/{len(markets)}: {market['question'][:60]}...")
        analysis = analyzer.analyze_market(market)

        if analysis.get("should_trade"):
            outcome    = analysis["outcome_to_buy"]
            confidence = analysis["confidence"]
            bet_amount = config.bet_size(confidence, config.PAPER_STARTING_BALANCE)
            entry_price = market["outcomes"][0]["price"] if outcome == "Yes" else market["outcomes"][1]["price"]

            logger.info(
                Fore.GREEN +
                f"\n  ✅ PAPER TRADE PLACED\n"
                f"     Market:    {market['question'][:55]}\n"
                f"     Bet:       BUY '{outcome}'\n"
                f"     Amount:    ${bet_amount:.2f} @ {entry_price:.3f}\n"
                f"     AI says:   {analysis['my_probability']:.0%} true prob vs {entry_price:.0%} market\n"
                f"     Edge:      {analysis['edge']:+.1%}\n"
                f"     Reasoning: {analysis['reasoning'][:100]}"
                + Style.RESET_ALL
            )

            trade = {
                "timestamp":     datetime.now().isoformat(),
                "market_id":     market["id"],
                "question":      market["question"],
                "outcome":       outcome,
                "entry_price":   entry_price,
                "amount_usd":    bet_amount,
                "ai_confidence": confidence,
                "ai_probability":analysis["my_probability"],
                "edge":          analysis["edge"],
                "reasoning":     analysis["reasoning"],
                "key_risks":     analysis["key_risks"],
                "status":        "open",
                "url":           market.get("url", ""),
            }

            state["open_positions"][market["id"]] = trade
            state["balance"] -= bet_amount
            log_trade(trade, config)

        else:
            logger.info(f"  ⏭️  Skipping: {analysis.get('reason', 'low confidence')}")

        time.sleep(config.DELAY_BETWEEN_ANALYSES)

    # Print summary
    open_count = len(state["open_positions"])
    logger.info(f"\n📊 Balance: ${state['balance']:,.2f} | Open positions: {open_count}")
    logger.info(f"⏰ Next cycle in {config.CYCLE_INTERVAL_MINUTES} minutes...\n")


def main():
    print_banner()

    config = BotConfig()
    if not config.validate():
        print(Fore.RED + "\n❌ Check your .env file or Railway variables.")
        sys.exit(1)

    logger   = BotLogger(config.LOG_FILE, config.LOG_TO_FILE)
    client   = KalshiClient(config)
    analyzer = AIAnalyzer(config, logger)

    state = {
        "balance":        config.PAPER_STARTING_BALANCE,
        "open_positions": {},
    }

    logger.info(f"🚀 Bot started! Scanning {config.MARKETS_TO_SCAN} markets every {config.CYCLE_INTERVAL_MINUTES} min")
    logger.info(f"💼 Starting paper balance: ${config.PAPER_STARTING_BALANCE:,.2f}")
    logger.info(f"🎯 Min confidence: {config.MIN_CONFIDENCE_TO_TRADE:.0%}")
    logger.info(f"💵 Max bet: ${config.MAX_BET_SIZE:.2f}\n")

    run_cycle(client, analyzer, logger, config, state)

    schedule.every(config.CYCLE_INTERVAL_MINUTES).minutes.do(
        run_cycle, client, analyzer, logger, config, state
    )

    print(Fore.GREEN + "\n✅ Bot is running! Press Ctrl+C to stop.\n")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n👋 Bot stopped.")
        print(f"Final balance: ${state['balance']:,.2f}")


if __name__ == "__main__":
    main()

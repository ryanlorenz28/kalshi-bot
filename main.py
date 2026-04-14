"""
=============================================================
  KALSHI AI TRADING BOT - MAIN ENTRY POINT  (v2)
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
import traceback
from datetime import datetime
from colorama import init, Fore, Style
from bot_config import BotConfig
from kalshi_client import KalshiClient
from ai_analyzer import AIAnalyzer
from logger import BotLogger

init(autoreset=True)

# ── COST TRACKING ─────────────────────────────────────────────────────────────
# Approximate cost per Claude API call (claude-sonnet ~$0.003 per analysis)
COST_PER_ANALYSIS = 0.003


def print_banner():
    print(Fore.CYAN + """
╔══════════════════════════════════════════════════════╗
║         KALSHI AI TRADING BOT  v2                    ║
║         Powered by Claude AI                         ║
║         Mode: PAPER TRADING (no real money)          ║
╚══════════════════════════════════════════════════════╝
    """ + Style.RESET_ALL)


def log_trade(trade: dict, config):
    """Save trade to CSV for later analysis."""
    if not config.LOG_TRADES:
        return
    is_new = not os.path.exists(config.TRADE_LOG_FILE)
    try:
        with open(config.TRADE_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(trade.keys()))
            if is_new:
                writer.writeheader()
            writer.writerow(trade)
    except Exception as e:
        print(f"Could not write trade log: {e}")


def run_cycle(client, analyzer, logger, config, state):
    logger.info("=" * 55)
    logger.info(f"🔄 Cycle #{state['cycle_count']} | "
                f"Est. API spend today: ${state['daily_cost']:.2f}")

    # ── Show open positions ───────────────────────────────────
    if state["open_positions"]:
        logger.info(f"\n📋 OPEN POSITIONS ({len(state['open_positions'])}):")
        for pos in state["open_positions"].values():
            logger.info(f"  • {pos['question'][:50]}...")
            logger.info(f"    {pos['outcome']} @ ${pos['amount_usd']:.2f} | "
                        f"Confidence: {pos['ai_confidence']:.0%} | "
                        f"Edge: {pos['edge']:+.0%} | "
                        f"Reasoning: {pos['reasoning'][:80]}...")
    else:
        logger.info("📋 No open positions")

    # ── Scan markets ─────────────────────────────────────────
    logger.info("\n📡 Scanning Kalshi for active markets...")
    markets = client.get_markets(limit=config.MARKETS_TO_SCAN)

    if not markets:
        logger.warning("No markets found. Retrying next cycle.")
        return

    markets = [m for m in markets if m]
    logger.info(f"✅ Found {len(markets)} markets to analyze\n")

    trades_this_cycle = 0

    for i, market in enumerate(markets):
        # Position limit check
        if len(state["open_positions"]) >= config.MAX_OPEN_POSITIONS:
            logger.info(f"⛔ Position limit ({config.MAX_OPEN_POSITIONS}) reached.")
            break

        # Skip if already have position in this market
        if market["id"] in state["open_positions"]:
            continue

        logger.info(f"🤖 Analyzing market {i+1}/{len(markets)}: {market['question'][:65]}...")

        analysis = analyzer.analyze_market(market)
        state["daily_cost"]   += COST_PER_ANALYSIS
        state["total_analyses"] += 1

        if analysis.get("should_trade"):
            outcome     = analysis["outcome_to_buy"]
            confidence  = analysis["confidence"]
            bet_amount  = config.bet_size(confidence, state["balance"])
            entry_price = (market["outcomes"][0]["price"] if outcome == "Yes"
                           else market["outcomes"][1]["price"])

            logger.info(
                Fore.GREEN +
                f"\n  ✅ PAPER TRADE PLACED\n"
                f"     Market:     {market['question']}\n"
                f"     Bet:        BUY '{outcome}'\n"
                f"     Amount:     ${bet_amount:.2f} @ {entry_price:.3f}\n"
                f"     AI says:    {analysis['my_probability']:.0%} true prob "
                f"vs {entry_price:.0%} market price\n"
                f"     Edge:       {analysis['edge']:+.1%}\n"
                f"     Confidence: {confidence:.0%}\n"
                f"     Reasoning:  {analysis['reasoning']}\n"
                f"     Key risks:  {analysis['key_risks']}\n"
                f"     URL:        {market.get('url', '')}"
                + Style.RESET_ALL
            )

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
                "category":       market.get("category", ""),
                "days_to_resolve":market.get("days_to_resolve", 0),
                "volume":         market.get("volume", 0),
                "status":         "open",
                "url":            market.get("url", ""),
            }

            state["open_positions"][market["id"]] = trade
            state["balance"]        -= bet_amount
            state["total_trades"]   += 1
            trades_this_cycle       += 1
            log_trade(trade, config)

        else:
            reason = analysis.get("reason", "low confidence")
            logger.info(f"  ⏭️  Skipping: {reason}")

        time.sleep(config.DELAY_BETWEEN_ANALYSES)

    # ── Cycle summary ─────────────────────────────────────────
    logger.info(f"\n{'─' * 50}")
    logger.info(f"📊 CYCLE SUMMARY")
    logger.info(f"   Balance:        ${state['balance']:,.2f}")
    logger.info(f"   Open positions: {len(state['open_positions'])}")
    logger.info(f"   Trades placed:  {trades_this_cycle} this cycle "
                f"({state['total_trades']} total)")
    logger.info(f"   Total analyses: {state['total_analyses']}")
    logger.info(f"   Est. API cost:  ${state['daily_cost']:.2f} today")
    logger.info(f"⏰ Next cycle in {config.CYCLE_INTERVAL_MINUTES} minutes...\n")

    state["cycle_count"] += 1


def main():
    print_banner()

    config = BotConfig()
    if not config.validate():
        print(Fore.RED + "\n❌ Check your environment variables.")
        sys.exit(1)

    logger   = BotLogger(config.LOG_FILE, config.LOG_TO_FILE)
    client   = KalshiClient(config)
    analyzer = AIAnalyzer(config, logger)

    state = {
        "balance":         config.PAPER_STARTING_BALANCE,
        "open_positions":  {},
        "cycle_count":     1,
        "total_trades":    0,
        "total_analyses":  0,
        "daily_cost":      0.0,
    }

    logger.info(f"🚀 Bot started! Scanning {config.MARKETS_TO_SCAN} markets "
                f"every {config.CYCLE_INTERVAL_MINUTES} min")
    logger.info(f"💼 Starting paper balance: ${config.PAPER_STARTING_BALANCE:,.2f}")
    logger.info(f"🎯 Min confidence: {config.MIN_CONFIDENCE_TO_TRADE:.0%}")
    logger.info(f"💵 Max bet: ${config.MAX_BET_SIZE:.2f}\n")

    # Run immediately on startup
    run_cycle(client, analyzer, logger, config, state)

    schedule.every(config.CYCLE_INTERVAL_MINUTES).minutes.do(
        run_cycle, client, analyzer, logger, config, state
    )

    print(Fore.GREEN + "\n✅ Bot is running! Press Ctrl+C to stop.\n")

    # ── Main loop with auto-restart on crash ──────────────────
    consecutive_errors = 0
    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
            consecutive_errors = 0   # Reset on success
        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n👋 Bot stopped.")
            print(f"Final balance: ${state['balance']:,.2f}")
            print(f"Total trades:  {state['total_trades']}")
            print(f"Est. API cost: ${state['daily_cost']:.2f}")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Unexpected error (#{consecutive_errors}): {e}")
            logger.error(traceback.format_exc())
            if consecutive_errors >= 5:
                logger.error("Too many consecutive errors — stopping bot.")
                sys.exit(1)
            logger.info("Recovering in 60 seconds...")
            time.sleep(60)


if __name__ == "__main__":
    main()

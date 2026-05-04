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
║         Powered by Claude AI                         ║
║         Mode: {mode:<38}║
║         {limit:<54}║
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


def check_daily_loss_limit(state, config, logger) -> bool:
    """
    Returns True if it's safe to keep trading.
    Returns False (and logs a warning) if daily loss limit is breached.
    """
    if config.PAPER_TRADING:
        return True
    if state["daily_loss"] >= config.DAILY_LOSS_LIMIT:
        logger.warning(
            f"🛑 DAILY LOSS LIMIT HIT (${state['daily_loss']:.2f} >= "
            f"${config.DAILY_LOSS_LIMIT:.2f}). Bot halted for today."
        )
        return False
    return True


def reset_daily_state_if_new_day(state):
    """Reset daily counters at the start of each new calendar day."""
    today = date.today()
    if state.get("last_reset_date") != today:
        state["daily_loss"] = 0.0
        state["last_reset_date"] = today


def real_money_remaining(state, config) -> float:
    """How much real money is still available to spend."""
    return max(0.0, config.REAL_MONEY_LIMIT - state["real_money_spent"])


def run_cycle(client, analyzer, logger, config, state):
    reset_daily_state_if_new_day(state)

    # Daily loss check before doing anything
    if not check_daily_loss_limit(state, config, logger):
        return

    logger.info("=" * 55)
    logger.info("🔄 Starting new trading cycle...")

    # Show real money status if live
    if not config.PAPER_TRADING:
        remaining = real_money_remaining(state, config)
        logger.info(
            f"💰 Real money: ${state['real_money_spent']:.2f} spent / "
            f"${config.REAL_MONEY_LIMIT:.2f} limit  |  "
            f"Daily loss: ${state['daily_loss']:.2f} / ${config.DAILY_LOSS_LIMIT:.2f}"
        )

    logger.info("📡 Scanning Kalshi for active markets...")
    markets = client.get_markets(limit=config.MARKETS_TO_SCAN)

    if not markets:
        logger.warning("No markets found. Retrying next cycle.")
        return

    markets = [m for m in markets if m]
    logger.info(f"✅ Found {len(markets)} markets to analyze")

    for i, market in enumerate(markets):
        if len(state["open_positions"]) >= config.MAX_OPEN_POSITIONS:
            logger.info(f"⛔ Position limit ({config.MAX_OPEN_POSITIONS}) reached.")
            break

        if market["id"] in state["open_positions"]:
            continue

        # Re-check daily loss limit each iteration
        if not check_daily_loss_limit(state, config, logger):
            return

        logger.info(f"\n🤖 [{i+1}/{len(markets)}] {market['question'][:65]}...")
        analysis = analyzer.analyze_market(market)

        if not analysis.get("should_trade"):
            logger.info(f"  ⏭️  Skip: {analysis.get('reason', 'low confidence')}")
            time.sleep(config.DELAY_BETWEEN_ANALYSES)
            continue

        # ── Decide live vs paper ────────────────────────────────
        outcome     = analysis["outcome_to_buy"]
        confidence  = analysis["confidence"]
        side        = outcome.lower()   # "yes" or "no"

        # Pick the right side's price
        entry_price = (
            market["outcomes"][0]["price"] if outcome == "Yes"
            else market["outcomes"][1]["price"]
        )

        # How much to bet
        if not config.PAPER_TRADING:
            remaining = real_money_remaining(state, config)
            if remaining <= 0:
                # Real money cap hit — fall back to paper
                bet_amount = config.bet_size(confidence, config.PAPER_STARTING_BALANCE)
                use_real   = False
                logger.info(
                    Fore.YELLOW +
                    f"  ℹ️  Real money cap reached (${config.REAL_MONEY_LIMIT:.0f}). "
                    f"Falling back to paper trade." + Style.RESET_ALL
                )
            else:
                bet_amount = min(
                    config.bet_size(confidence, config.REAL_MONEY_LIMIT),
                    remaining
                )
                use_real = True
        else:
            bet_amount = config.bet_size(confidence, config.PAPER_STARTING_BALANCE)
            use_real   = False

        # ── Place the order ─────────────────────────────────────
        order_result = client.place_order(
            ticker    = market["id"],
            side      = side,
            amount_usd= bet_amount,
            price     = entry_price,
            dry_run   = not use_real,
        )

        if order_result is None:
            logger.error(f"  ❌ Order failed for {market['id']} — skipping.")
            time.sleep(config.DELAY_BETWEEN_ANALYSES)
            continue

        actual_cost = order_result.get("cost_usd", bet_amount)
        trade_mode  = "LIVE" if use_real else "PAPER"
        color       = Fore.GREEN if use_real else Fore.CYAN

        logger.info(
            color +
            f"\n  ✅ {trade_mode} TRADE PLACED\n"
            f"     Market:    {market['question'][:55]}\n"
            f"     Bet:       BUY '{outcome}'  x{order_result.get('count', '?')} contracts\n"
            f"     Cost:      ${actual_cost:.2f} @ {entry_price:.3f}\n"
            f"     AI says:   {analysis['my_probability']:.0%} true prob vs {entry_price:.0%} market\n"
            f"     Edge:      {analysis['edge']:+.1%}\n"
            f"     Reasoning: {analysis['reasoning'][:100]}"
            + Style.RESET_ALL
        )

        # ── Update state ────────────────────────────────────────
        if use_real:
            state["real_money_spent"] += actual_cost
            # Daily loss is updated when positions resolve — for now track spend
            # as a conservative proxy so the limit still triggers
            state["daily_loss"] += actual_cost

        trade_record = {
            "timestamp":      datetime.now().isoformat(),
            "mode":           trade_mode,
            "market_id":      market["id"],
            "question":       market["question"],
            "outcome":        outcome,
            "contracts":      order_result.get("count", "?"),
            "entry_price":    entry_price,
            "cost_usd":       actual_cost,
            "ai_confidence":  confidence,
            "ai_probability": analysis["my_probability"],
            "edge":           analysis["edge"],
            "reasoning":      analysis["reasoning"],
            "key_risks":      analysis["key_risks"],
            "status":         "open",
            "url":            market.get("url", ""),
        }

        state["open_positions"][market["id"]] = trade_record
        log_trade(trade_record, config)

        time.sleep(config.DELAY_BETWEEN_ANALYSES)

    # ── Cycle summary ───────────────────────────────────────────────────────
    open_count = len(state["open_positions"])
    if not config.PAPER_TRADING:
        logger.info(
            f"\n📊 Real spent: ${state['real_money_spent']:.2f} / ${config.REAL_MONEY_LIMIT:.2f}"
            f"  |  Daily loss: ${state['daily_loss']:.2f} / ${config.DAILY_LOSS_LIMIT:.2f}"
            f"  |  Open positions: {open_count}"
        )
    else:
        logger.info(f"\n📊 Open positions: {open_count}")
    logger.info(f"⏰ Next cycle in {config.CYCLE_INTERVAL_MINUTES} minutes...\n")


def main():
    config = BotConfig()
    print_banner(config)

    if not config.validate():
        print(Fore.RED + "\n❌ Fix your .env / Railway variables before continuing.")
        sys.exit(1)

    logger   = BotLogger(config.LOG_FILE, config.LOG_TO_FILE)
    client   = KalshiClient(config)
    analyzer = AIAnalyzer(config, logger)

    # ── Fetch real balance from Kalshi on startup (live mode only) ──────────
    if not config.PAPER_TRADING:
        live_balance = client.get_balance()
        if live_balance is not None:
            logger.info(Fore.GREEN + f"💳 Kalshi account balance: ${live_balance:.2f}" + Style.RESET_ALL)
        else:
            logger.warning("Could not fetch Kalshi balance — check API credentials.")

    state = {
        "open_positions":   {},
        "real_money_spent": 0.0,
        "daily_loss":       0.0,
        "last_reset_date":  date.today(),
    }

    mode_str = "LIVE" if not config.PAPER_TRADING else "PAPER"
    logger.info(f"🚀 Bot started in {mode_str} mode")
    logger.info(f"📡 Scanning {config.MARKETS_TO_SCAN} markets every {config.CYCLE_INTERVAL_MINUTES} min")
    if not config.PAPER_TRADING:
        logger.info(f"💵 Real money cap: ${config.REAL_MONEY_LIMIT:.0f}")
        logger.info(f"🛑 Daily loss limit: ${config.DAILY_LOSS_LIMIT:.0f}")
    logger.info(f"🎯 Min confidence: {config.MIN_CONFIDENCE_TO_TRADE:.0%}  |  Max bet: ${config.MAX_BET_SIZE:.0f}\n")

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
        if not config.PAPER_TRADING:
            print(f"Total real money spent: ${state['real_money_spent']:.2f}")
            print(f"Daily loss tracked:     ${state['daily_loss']:.2f}")


if __name__ == "__main__":
    main()

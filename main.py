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
    today = date.today()
    if state.get("last_reset_date") != today:
        state["daily_loss"] = 0.0
        state["last_reset_date"] = today


def real_money_remaining(state, config) -> float:
    return max(0.0, config.REAL_MONEY_LIMIT - state["real_money_spent"])


def check_exit_positions(client, logger, config, state):
    """Exit positions that have moved strongly in our favor (lock in profit)
    or are deep losers near expiry (stop loss)."""
    if config.PAPER_TRADING:
        return

    to_remove = []
    for market_id, pos in state["open_positions"].items():
        if pos.get("mode") != "LIVE":
            continue
        try:
            path = f"/markets/{market_id}"
            r = client.session.get(
                client.BASE_URL + path,
                headers=client._headers("GET", path),
                timeout=10,
            )
            if r.status_code != 200:
                continue
            m = r.json().get("market", {})

            side = pos.get("outcome", "Yes").lower()
            if side == "yes":
                current_price = float(m.get("yes_bid_dollars") or m.get("last_price_dollars") or 0)
            else:
                current_price = float(m.get("no_bid_dollars") or 0)
                if current_price == 0:
                    yes_price = float(m.get("yes_ask_dollars") or 0.5)
                    current_price = 1 - yes_price
            if current_price > 1:
                current_price /= 100

            entry_price = float(pos.get("entry_price", 0.5))
            contracts   = int(pos.get("contracts", 1))
            # Skip profit/loss checks for positions loaded at startup with unknown entry price
            if pos.get("reasoning") == "loaded from Kalshi on startup":
                continue
            gain_pct    = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            # Use live close_time for accurate days remaining
            try:
                close_time = m.get("close_time", "")
                from datetime import timezone as tz
                end = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                days_left = max(0, (end - datetime.now(tz.utc)).days)
            except Exception:
                days_left = pos.get("days_to_resolve", 999)

            # Take profit: position up TAKE_PROFIT_PCT and trading above 80¢
            if gain_pct >= config.TAKE_PROFIT_PCT and current_price >= 0.80:
                logger.info(
                    Fore.GREEN +
                    f"  💰 TAKE PROFIT: {market_id} up {gain_pct:.0%} "
                    f"(entry {entry_price:.2f} → now {current_price:.2f})"
                    + Style.RESET_ALL
                )
                success = client.sell_position(market_id, side, contracts)
                if success:
                    profit = (current_price - entry_price) * contracts
                    state["real_money_spent"] = max(0, state["real_money_spent"] - pos.get("cost_usd", 0))
                    to_remove.append(market_id)
                    logger.info(f"  ✅ Exited {market_id} for ~${profit:.2f} profit")

            # Stop loss: down STOP_LOSS_PCT with fewer than 10 days left — cut losses
            elif gain_pct <= -config.STOP_LOSS_PCT and days_left < 10:
                logger.info(
                    Fore.RED +
                    f"  🛑 STOP LOSS: {market_id} down {abs(gain_pct):.0%} "
                    f"with {days_left} days left — exiting"
                    + Style.RESET_ALL
                )
                success = client.sell_position(market_id, side, contracts)
                if success:
                    to_remove.append(market_id)

        except Exception as e:
            logger.error(f"Error checking exit for {market_id}: {e}")

    for market_id in to_remove:
        state["open_positions"].pop(market_id, None)
        logger.info(f"  🗑️  Removed {market_id} from open positions")


def run_cycle(client, analyzer, logger, config, state):
    reset_daily_state_if_new_day(state)

    if not check_daily_loss_limit(state, config, logger):
        return

    logger.info("=" * 55)
    logger.info("🔄 Starting new trading cycle...")

    # Check if any existing positions should be exited
    check_exit_positions(client, logger, config, state)

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

        # Use ticker prefix as series key (e.g. "KXGDP" from "KXGDP-26Q2-T2")
        series = market["id"].split("-")[0]
        series_spent = sum(
            t.get("cost_usd", 0) for t in state["open_positions"].values()
            if t.get("series", "") == series and t.get("mode") == "LIVE"
        )
        if series_spent >= config.MAX_EXPOSURE_PER_SERIES:
            logger.info(f"  ⛔ Series cap (${config.MAX_EXPOSURE_PER_SERIES:.0f}) reached for {series}")
            continue

        if not check_daily_loss_limit(state, config, logger):
            return

        logger.info(f"\n🤖 [{i+1}/{len(markets)}] {market['question'][:65]}...")
        analysis = analyzer.analyze_market(market)

        if not analysis.get("should_trade"):
            logger.info(f"  ⏭️  Skip: {analysis.get('reason', 'low confidence')}")
            time.sleep(config.DELAY_BETWEEN_ANALYSES)
            continue

        outcome     = analysis["outcome_to_buy"]
        confidence  = analysis["confidence"]
        side        = outcome.lower()

        entry_price = (
            market["outcomes"][0]["price"] if outcome == "Yes"
            else market["outcomes"][1]["price"]
        )

        if not config.PAPER_TRADING:
            # Hard total exposure limit
            if state["real_money_spent"] >= config.TOTAL_EXPOSURE_LIMIT:
                logger.info(Fore.RED + f"  🛑 Total exposure limit (${config.TOTAL_EXPOSURE_LIMIT:.0f}) reached — no new trades" + Style.RESET_ALL)
                break
            remaining = real_money_remaining(state, config)
            if remaining <= 0:
                bet_amount = config.bet_size(confidence, config.PAPER_STARTING_BALANCE)
                use_real   = False
                logger.info(
                    Fore.YELLOW +
                    f"  ℹ️  Real money cap reached (${config.REAL_MONEY_LIMIT:.0f}). "
                    f"Falling back to paper trade." + Style.RESET_ALL
                )
            else:
                # Use live Kalshi balance for bet sizing, not config limit
                live_balance = client.get_balance() or remaining
                bet_amount = min(
                    config.bet_size(confidence, live_balance),
                    remaining,
                    live_balance * 0.25,   # never bet more than 25% of available cash
                )
                use_real = True
        else:
            bet_amount = config.bet_size(confidence, config.PAPER_STARTING_BALANCE)
            use_real   = False

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

        if use_real:
            state["real_money_spent"] += actual_cost
            state["daily_loss"] += actual_cost

        trade_record = {
            "timestamp":      datetime.now().isoformat(),
            "mode":           trade_mode,
            "series":         market["id"].split("-")[0],
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

    # ── Load existing Kalshi positions into state on startup ───
    # This prevents the series cap from resetting on every redeploy
    if not config.PAPER_TRADING:
        existing = client.get_positions()
        if existing:
            # Log first position's raw fields so we can verify cost field name
            logger.info(f"🔍 Sample position fields: {list(existing[0].keys())}")
        for pos in existing:
            ticker = pos.get("ticker", "")
            # Kalshi returns cost in dollars under total_traded_dollars
            raw_cost = (
                pos.get("total_traded_dollars") or
                pos.get("market_exposure_dollars") or
                0
            )
            cost = abs(float(raw_cost or 0))
            # If cost is still 0, estimate from position size and avg price
            if cost == 0:
                contracts = abs(int(float(pos.get("position_fp", 0) or 0)))
                avg_price = 0.5
                cost = round(contracts * avg_price, 2)
            side = "Yes" if float(pos.get("position_fp", 0) or 0) > 0 else "No"
            contracts = abs(int(float(pos.get("position_fp", 0) or 0)))
            if ticker and cost > 0:
                # Fetch live market data to get accurate days_to_resolve
                days = 999
                try:
                    path = f"/markets/{ticker}"
                    r = client.session.get(
                        client.BASE_URL + path,
                        headers=client._headers("GET", path),
                        timeout=10,
                    )
                    if r.status_code == 200:
                        close_time = r.json().get("market", {}).get("close_time", "")
                        days = client._days_until(close_time)
                except Exception:
                    pass
                state["open_positions"][ticker] = {
                    "mode":            "LIVE",
                    "series":          ticker.split("-")[0],
                    "market_id":       ticker,
                    "outcome":         side,
                    "cost_usd":        cost,
                    "contracts":       contracts,
                    "entry_price":     0.5,
                    "days_to_resolve": days,
                    "question":        ticker,
                    "reasoning":       "loaded from Kalshi on startup",
                    "key_risks":       "",
                    "edge":            0,
                    "ai_confidence":   0,
                    "ai_probability":  0.5,
                    "timestamp":       datetime.now().isoformat(),
                    "status":          "open",
                }
                state["real_money_spent"] += cost
        if existing:
            logger.info(f"📂 Loaded {len(existing)} existing positions from Kalshi (${state['real_money_spent']:.2f} spent)")
        else:
            logger.info("📂 No existing positions found on Kalshi")

    # ── Load persisted auto-blacklist ──────────────────────────
    blacklist_file = "auto_blacklist.txt"
    if os.path.exists(blacklist_file):
        with open(blacklist_file) as f:
            for line in f:
                ticker = line.strip()
                if ticker:
                    client.BLACKLIST.add(ticker)
        logger.info(f"📋 Loaded {len(client.BLACKLIST)} blacklisted tickers")

    # ── Start status server for the dashboard ──────────────────
    status_server.start(state, client, config)

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

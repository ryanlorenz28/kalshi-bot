"""
maker_orders.py — resting (maker) buy orders: buy at/near the bid with a
post-only order that waits on the book, instead of paying the ask + 3¢.

Off unless MAKER_ORDERS = True. Buys only; stop-loss / take-profit sells stay
immediate-or-cancel in kalshi_client.sell_position.

Lifecycle of a waiting order (state["resting_orders"][ticker]):
  place()  -> post-only good_till_canceled order with expiration_time
  manage() -> each cycle: record fills as open positions, drop orders that
              executed / expired / were canceled
  cancel   -> when Claude no longer wants the trade, the daily loss limit hits,
              MAKER_ORDERS is turned off, or (startup) the bot finds an order of
              its own it doesn't recognise
While waiting, an order reserves its full cost against the position limit,
series caps, the $450 limit and the 25%-of-cash rule.

Waiting orders are saved to $DATA_DIR/resting_orders.json so a redeploy keeps
track of them. Without a volume, startup cancels every bot order it finds,
which is safe.
"""
import json
import os
import time
from datetime import datetime

import prediction_log

RESTING_FILE = "resting_orders.json"


def _path():
    d = os.environ.get("DATA_DIR", "")
    return os.path.join(d, RESTING_FILE) if d and os.path.isdir(d) else None


def _save(state):
    path = _path()
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state.get("resting_orders", {}), f)
        os.replace(tmp, path)
    except Exception as e:
        print(f"⚠️  Couldn't save resting orders: {e}")


def resting(state) -> dict:
    return state.setdefault("resting_orders", {})


def reserved_usd(state, series=None) -> float:
    """Cost still tied up in unfilled contracts of waiting orders."""
    return round(sum(o["price"] * o["remaining"] for o in resting(state).values()
                     if series is None or o["series"] == series), 2)


def waiting_tickers(state) -> set:
    return set(resting(state))


def load(state, logger):
    path = _path()
    if not path:
        return
    try:
        with open(path) as f:
            state["resting_orders"] = json.load(f)
        if state["resting_orders"]:
            logger.info(f"📌 Loaded {len(state['resting_orders'])} waiting buy order(s) from {path}")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Couldn't read waiting orders ({e}) — startup check will cancel unknown ones")


def reconcile_at_startup(client, state, logger, config):
    """Cancel any of the bot's own resting orders it isn't tracking (lost on a
    redeploy without a volume), and every bot order if MAKER_ORDERS is off."""
    orders = client.list_resting_orders()
    if orders is None:
        logger.warning("Couldn't list resting orders on Kalshi — will retry orphan check next startup")
        return
    known = {o["order_id"] for o in resting(state).values()}
    for o in orders:
        if not str(o.get("client_order_id", "")).startswith(client.MAKER_TAG):
            continue   # not the bot's — e.g. Ryan's manual orders; leave alone
        if config.MAKER_ORDERS and o.get("order_id") in known:
            continue
        why = "MAKER_ORDERS is off" if not config.MAKER_ORDERS else "not tracked"
        if client.cancel_order(o["order_id"], o.get("ticker", "")):
            logger.info(f"  📌 Canceled waiting order on {o.get('ticker')} ({why})")
    if not config.MAKER_ORDERS and resting(state):
        state["resting_orders"] = {}
        _save(state)


def cancel_all(client, state, logger, reason):
    for ticker, o in list(resting(state).items()):
        if client.cancel_order(o["order_id"], ticker):
            logger.info(f"  📌 Canceled waiting buy on {ticker} ({reason})")
            resting(state).pop(ticker, None)
    _save(state)


def cancel(client, state, logger, ticker, reason):
    o = resting(state).get(ticker)
    if o and client.cancel_order(o["order_id"], ticker):
        logger.info(f"  📌 Canceled waiting buy on {ticker} ({reason})")
        resting(state).pop(ticker, None)
        _save(state)


def place(client, state, logger, config, market, analysis, outcome, limit_price, bet_amount) -> bool:
    """Place a waiting buy for bet_amount at limit_price. True if it's on the book."""
    count = int(bet_amount / limit_price)
    if count < 1:
        return False
    side = outcome.lower()
    info = client.place_resting_buy(market["id"], side, count, limit_price, config.MAKER_EXPIRY_MIN)
    if not info:
        logger.info(f"  📌 Waiting buy not placed on {market['id']} (rejected — see above)")
        return False
    now = time.time()
    resting(state)[market["id"]] = {
        "order_id":     info["order_id"],
        "client_order_id": info["client_order_id"],
        "ticker":       market["id"],
        "series":       market["id"].split("-")[0],
        "outcome":      outcome,
        "price":        limit_price,
        "count":        count,
        "filled":       0.0,
        "remaining":    float(count),
        "ask_at_placement": (market["outcomes"][0]["price"] if outcome == "Yes"
                             else market["outcomes"][1]["price"]),
        "placed_ts":    now,
        "expires_ts":   now + config.MAKER_EXPIRY_MIN * 60,
        # what's needed to record the position and prediction on a fill
        "market":       {"id": market["id"], "question": market.get("question", ""),
                         "outcomes": market["outcomes"], "days_to_resolve": market.get("days_to_resolve"),
                         "url": market.get("url", "")},
        "analysis":     {k: analysis.get(k) for k in ("my_probability", "confidence", "edge",
                                                       "outcome_to_buy", "should_trade", "reason",
                                                       "reasoning", "key_risks")},
    }
    _save(state)
    saved = resting(state)[market["id"]]["ask_at_placement"] - limit_price
    logger.info(f"\n  📌 WAITING BUY PLACED: {outcome.upper()} x{count} {market['id']} @ {limit_price:.2f} "
                f"(ask was {saved + limit_price:.2f}, {saved * 100:+.0f}¢/contract vs paying it) — "
                f"expires in {config.MAKER_EXPIRY_MIN} min")
    return True


def _record_fill(state, logger, o, new_contracts, avg_price):
    """Add/extend the open position for newly filled contracts."""
    ticker = o["ticker"]
    a, m = o["analysis"], o["market"]
    pos = state["open_positions"].get(ticker)
    cost = round(new_contracts * avg_price, 2)
    if pos is None:
        state["open_positions"][ticker] = {
            "timestamp":      datetime.now().isoformat(),
            "mode":           "LIVE",
            "series":         o["series"],
            "market_id":      ticker,
            "question":       m.get("question", ""),
            "outcome":        o["outcome"],
            "contracts":      new_contracts,
            "entry_price":    avg_price,
            "cost_usd":       cost,
            "ai_confidence":  a.get("confidence"),
            "ai_probability": a.get("my_probability"),
            "edge":           a.get("edge"),
            "reasoning":      a.get("reasoning") or "",
            "key_risks":      a.get("key_risks") or "",
            "status":         "open",
            "url":            m.get("url", ""),
            "days_to_resolve": m.get("days_to_resolve", 999),
        }
    else:
        total = float(pos.get("contracts", 0) or 0) + new_contracts
        pos["cost_usd"] = round(float(pos.get("cost_usd", 0) or 0) + cost, 2)
        pos["contracts"] = total
        pos["entry_price"] = round(pos["cost_usd"] / total, 4) if total else avg_price
    state["real_money_spent"] = round(state.get("real_money_spent", 0) + cost, 2)
    logger.info(f"  ✅ WAITING BUY FILLED: {o['outcome'].upper()} x{new_contracts:g} {ticker} @ {avg_price:.2f} "
                f"(ask was {o['ask_at_placement']:.2f} when placed)")


def manage(client, state, logger, config):
    """Each live cycle: pick up fills and drop finished orders."""
    if not resting(state):
        return
    if not config.MAKER_ORDERS:
        cancel_all(client, state, logger, "MAKER_ORDERS turned off")
        return
    changed = False
    for ticker, o in list(resting(state).items()):
        order = client.get_order(o["order_id"])
        if order is None:
            if time.time() > o["expires_ts"] + 600:
                # Kalshi has expired it by now; don't let an unreadable order block
                # new buys forever. Any fill still shows up via the position sync.
                client.cancel_order(o["order_id"], ticker)
                logger.warning(f"Couldn't check waiting order on {ticker}; past expiry — dropped it")
                resting(state).pop(ticker, None)
                changed = True
            else:
                logger.warning(f"Couldn't check waiting order on {ticker} — will retry next cycle")
            continue
        filled    = float(order.get("fill_count_fp", "0") or "0")
        remaining = float(order.get("remaining_count_fp", "0") or "0")
        status    = order.get("status", "")
        if filled > o["filled"] + 1e-9:
            new = filled - o["filled"]
            maker_cost = float(order.get("maker_fill_cost_dollars", "0") or "0")
            avg = round(maker_cost / filled, 4) if maker_cost > 0 and filled > 0 else o["price"]
            _record_fill(state, logger, o, new, avg)
            o["filled"] = filled
            changed = True
        o["remaining"] = remaining
        if status in ("executed", "canceled") or remaining <= 0:
            if o["filled"] > 0:
                prediction_log.record("trade", o["market"], o["analysis"], side=o["outcome"],
                                      price=o["price"], bet_usd=round(o["filled"] * o["price"], 2),
                                      mode="LIVE", order_type="resting")
            what = "fully filled" if o["filled"] >= o["count"] - 1e-9 else \
                   (f"filled {o['filled']:g} of {o['count']}" if o["filled"] > 0 else "not filled")
            logger.info(f"  📌 Waiting buy on {ticker} finished: {what} ({status or 'done'})")
            resting(state).pop(ticker, None)
            changed = True
        elif time.time() > o["expires_ts"] + 300:
            # Kalshi should have expired it; cancel in case the expiry didn't apply
            if client.cancel_order(o["order_id"], ticker):
                logger.info(f"  📌 Canceled waiting buy on {ticker} (past expiry)")
                resting(state).pop(ticker, None)
                changed = True
    if changed:
        _save(state)

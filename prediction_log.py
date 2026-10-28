"""
prediction_log.py — records every Claude prediction to a durable file and
scores the ones whose markets have settled.

Read-only with respect to trading: nothing here places, sizes, or blocks a
trade, and every function swallows its own errors so it can't break a cycle.

Storage: $DATA_DIR (a Railway volume mounted at e.g. /data). Railway wipes
the container on redeploy, so if DATA_DIR isn't set the log is OFF and the
bot says so at startup rather than writing somewhere that gets erased.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

DATA_DIR      = os.environ.get("DATA_DIR", "")
PRED_FILE     = "predictions.jsonl"
RESULTS_FILE  = "results.json"     # ticker -> "yes"/"no" once settled
SCORE_EVERY_S = 6 * 3600
MAX_FETCH     = 40                 # settlement lookups per scoring run

_lock       = threading.Lock()
_summary    = {"status": "not scored yet"}
_last_score = 0.0


def enabled() -> bool:
    return bool(DATA_DIR) and os.path.isdir(DATA_DIR)


def _path(name):
    return os.path.join(DATA_DIR, name)


def startup_message() -> str:
    if not DATA_DIR:
        return "⚠️  Prediction log OFF — DATA_DIR not set (attach a Railway volume and set DATA_DIR)"
    if not os.path.isdir(DATA_DIR):
        return f"⚠️  Prediction log OFF — DATA_DIR {DATA_DIR} doesn't exist (is the volume mounted?)"
    n = 0
    try:
        with open(_path(PRED_FILE)) as f:
            n = sum(1 for _ in f)
    except FileNotFoundError:
        pass
    return f"📝 Prediction log ON — {_path(PRED_FILE)} ({n} records)"


def record(kind, market, analysis=None, **extra):
    """kind: "analysis" (every market Claude priced) or "trade" (an order placed)."""
    if not enabled():
        return
    try:
        a = analysis or {}
        rec = {
            "ts":             datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind":           kind,
            "ticker":         market["id"],
            "series":         market["id"].split("-")[0],
            "question":       market.get("question", "")[:200],
            "yes_price":      market["outcomes"][0]["price"],   # YES ask
            "no_price":       market["outcomes"][1]["price"],   # NO ask
            "days_left":      market.get("days_to_resolve"),
            "my_probability": a.get("my_probability"),          # Claude, relative to YES
            "confidence":     a.get("confidence"),
            "recommended":    a.get("outcome_to_buy"),
            "should_trade":   a.get("should_trade"),
            "reason":         a.get("reason"),
        }
        rec.update(extra)
        with _lock, open(_path(PRED_FILE), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        print(f"[predictions] couldn't record {market.get('id')}: {e}")


def summary() -> dict:
    return _summary


def _load_json(name, default):
    try:
        with open(_path(name)) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _fetch_result(client, ticker):
    path = f"/markets/{ticker}"
    r = client.session.get(client.BASE_URL + path, headers=client._headers("GET", path), timeout=10)
    if r.status_code != 200:
        return None
    result = (r.json().get("market", {}).get("result") or "").lower()
    return result if result in ("yes", "no") else None


def _brier(pairs):
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def score(client, logger=None, force=False) -> dict:
    """Look up settlements for predicted markets and score Claude vs the market.
    Runs at most every SCORE_EVERY_S unless force=True."""
    global _summary, _last_score
    if not enabled() or (not force and time.time() - _last_score < SCORE_EVERY_S):
        return _summary
    _last_score = time.time()
    try:
        preds = []
        with _lock:
            try:
                with open(_path(PRED_FILE)) as f:
                    preds = [json.loads(line) for line in f if line.strip()]
            except FileNotFoundError:
                pass
        results = _load_json(RESULTS_FILE, {})

        # Earliest analysis per market = Claude's first read of it. Repeat
        # analyses of the same market every 30 min would otherwise dominate.
        first = {}
        for p in preds:
            if p.get("kind") == "analysis" and p.get("my_probability") is not None:
                first.setdefault(p["ticker"], p)

        pending = [t for t in first if t not in results]
        for t in pending[:MAX_FETCH]:
            try:
                res = _fetch_result(client, t)
                if res:
                    results[t] = res
            except Exception:
                pass
        with _lock, open(_path(RESULTS_FILE), "w") as f:
            json.dump(results, f)

        settled = [(p, 1.0 if results[t] == "yes" else 0.0) for t, p in first.items() if t in results]
        # Market baseline = YES mid price: (YES ask + YES bid) / 2, YES bid = 1 - NO ask
        claude_pairs = [(p["my_probability"], y) for p, y in settled]
        market_pairs = [((p["yes_price"] + 1 - p["no_price"]) / 2, y) for p, y in settled]

        buckets = []
        for lo in range(0, 100, 10):
            inb = [(p, y) for p, y in claude_pairs if lo / 100 <= p < (lo + 10) / 100 or (lo == 90 and p == 1.0)]
            if inb:
                buckets.append({
                    "claude_said":  f"{lo}-{lo + 10}%",
                    "n":            len(inb),
                    "avg_claude":   round(sum(p for p, _ in inb) / len(inb), 3),
                    "actually_yes": round(sum(y for _, y in inb) / len(inb), 3),
                })

        by_series = {}
        for (pred, y), (m, _) in zip(settled, market_pairs):
            s = by_series.setdefault(pred["series"], {"claude": [], "market": []})
            s["claude"].append((pred["my_probability"], y))
            s["market"].append((m, y))
        by_series = {k: {"n": len(v["claude"]), "brier_claude": _brier(v["claude"]),
                         "brier_market": _brier(v["market"])} for k, v in sorted(by_series.items())}

        # Trades placed by the bot, scored at the price it used (fees and
        # fill slippage not included, so this is slightly optimistic).
        trades = [p for p in preds if p.get("kind") == "trade" and p.get("ticker") in results]
        wins, pnl = 0, 0.0
        for t in trades:
            won = (results[t["ticker"]] == "yes") == (t.get("side") == "Yes")
            bet, price = float(t.get("bet_usd") or 0), float(t.get("price") or 0)
            if won and price > 0:
                wins += 1
                pnl += bet / price - bet
            else:
                pnl -= bet

        _summary = {
            "status":              "ok",
            "scored_at":           datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "markets_predicted":   len(first),
            "markets_settled":     len(settled),
            "still_to_check":      max(0, len(pending) - MAX_FETCH),
            "brier_claude":        _brier(claude_pairs),
            "brier_market":        _brier(market_pairs),
            "note":                "Brier score: lower is better. Claude adds value only if brier_claude < brier_market over many settled markets.",
            "calibration":         buckets,
            "by_series":           by_series,
            "trades_settled":      len(trades),
            "trades_won":          wins,
            "trades_pnl_est_usd":  round(pnl, 2),
        }
        if logger:
            logger.info(f"📝 Predictions: {len(settled)}/{len(first)} markets settled  |  "
                        f"Brier Claude {_summary['brier_claude']} vs market {_summary['brier_market']}  |  "
                        f"trades {wins}/{len(trades)} won, est P&L ${pnl:+.2f}")
    except Exception as e:
        _summary = {"status": f"scoring error: {e}"}
        if logger:
            logger.warning(f"Prediction scoring failed: {e}")
    return _summary

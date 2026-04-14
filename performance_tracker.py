"""
performance_tracker.py — Analyzes your trades.csv and tells you
exactly how the bot is performing.

Run anytime with:  python performance_tracker.py
"""

import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from colorama import init, Fore, Style

init(autoreset=True)


def load_trades(path="trades.csv"):
    if not os.path.exists(path):
        print(Fore.RED + f"No trades file found at '{path}'")
        print("Run the bot for a few weeks before analyzing performance.")
        return []
    trades = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def analyze(trades):
    if not trades:
        return {}

    closed = [t for t in trades if t.get("status") in ("won", "lost")]
    open_  = [t for t in trades if t.get("status") == "open"]
    wins   = [t for t in closed if t.get("status") == "won"]

    total_wagered = sum(safe_float(t.get("amount_usd"))  for t in closed)
    total_payout  = sum(safe_float(t.get("payout", 0))   for t in wins)
    total_profit  = total_payout - total_wagered
    win_rate      = len(wins) / len(closed) if closed else 0
    roi           = total_profit / total_wagered if total_wagered else 0

    # By category
    by_cat = defaultdict(lambda: {"trades": 0, "wins": 0, "profit": 0.0, "wagered": 0.0})
    for t in closed:
        cat = t.get("category", "Unknown")
        by_cat[cat]["trades"]  += 1
        by_cat[cat]["wagered"] += safe_float(t.get("amount_usd"))
        if t.get("status") == "won":
            by_cat[cat]["wins"]   += 1
            by_cat[cat]["profit"] += safe_float(t.get("payout", 0)) - safe_float(t.get("amount_usd"))
        else:
            by_cat[cat]["profit"] -= safe_float(t.get("amount_usd"))

    # Confidence calibration
    conf_buckets = defaultdict(lambda: {"trades": 0, "wins": 0})
    for t in closed:
        conf   = safe_float(t.get("ai_confidence"))
        bucket = f"{int(conf * 10) * 10}–{int(conf * 10) * 10 + 10}%"
        conf_buckets[bucket]["trades"] += 1
        if t.get("status") == "won":
            conf_buckets[bucket]["wins"] += 1

    # Edge analysis
    edge_buckets = defaultdict(lambda: {"trades": 0, "wins": 0})
    for t in closed:
        edge = abs(safe_float(t.get("edge", 0)))
        if edge < 0.10:   b = "8–10%"
        elif edge < 0.15: b = "10–15%"
        elif edge < 0.20: b = "15–20%"
        else:             b = "20%+"
        edge_buckets[b]["trades"] += 1
        if t.get("status") == "won":
            edge_buckets[b]["wins"] += 1

    return {
        "total_trades":  len(trades),
        "closed":        len(closed),
        "open":          len(open_),
        "wins":          len(wins),
        "win_rate":      win_rate,
        "total_wagered": total_wagered,
        "total_profit":  total_profit,
        "roi":           roi,
        "by_category":   dict(by_cat),
        "confidence":    dict(conf_buckets),
        "edge_buckets":  dict(edge_buckets),
        "open_trades":   open_,
    }


def recommend(stats):
    recs = []
    if stats["closed"] < 20:
        recs.append("📊 Need at least 20 closed trades for reliable stats — keep running!")
        return recs

    if stats["win_rate"] < 0.45:
        recs.append("⚠️  Win rate below 45% — raise MIN_CONFIDENCE_TO_TRADE to 0.75")
    elif stats["win_rate"] > 0.65:
        recs.append("🎯 Win rate above 65% — could lower MIN_CONFIDENCE_TO_TRADE to 0.60")

    if stats["roi"] < -0.10:
        recs.append("⚠️  ROI negative — reduce MAX_BET_SIZE until performance improves")
    elif stats["roi"] > 0.20:
        recs.append("🚀 ROI above 20% — consider gradually increasing MAX_BET_SIZE")

    best_cat = worst_cat = None
    best_wr  = 0
    worst_wr = 1.1
    for cat, data in stats["by_category"].items():
        if data["trades"] >= 5:
            wr = data["wins"] / data["trades"]
            if wr > best_wr:  best_wr,  best_cat  = wr, cat
            if wr < worst_wr: worst_wr, worst_cat = wr, cat

    if best_cat:
        recs.append(f"✅ Best category: {best_cat} ({best_wr:.0%} win rate)")
    if worst_cat and worst_cat != best_cat:
        recs.append(f"❌ Worst category: {worst_cat} ({worst_wr:.0%} win rate) — consider avoiding")

    return recs


def print_report(stats, recs):
    print(Fore.CYAN + "\n" + "═" * 58)
    print("  📈  KALSHI BOT PERFORMANCE REPORT")
    print("  Generated:", datetime.now().strftime("%B %d, %Y at %I:%M %p"))
    print("═" * 58 + Style.RESET_ALL)

    color = Fore.GREEN if stats["total_profit"] >= 0 else Fore.RED
    print(f"\n{'OVERALL':─<55}")
    print(f"  Trades:    {stats['total_trades']} total  "
          f"({stats['closed']} closed, {stats['open']} open)")
    print(f"  Record:    {stats['wins']}W / {stats['closed'] - stats['wins']}L  "
          f"({stats['win_rate']:.1%} win rate)")
    print(f"  Wagered:   ${stats['total_wagered']:,.2f}")
    print(color + f"  Profit:    ${stats['total_profit']:+,.2f}  "
          f"(ROI: {stats['roi']:+.1%})" + Style.RESET_ALL)

    if stats["by_category"]:
        print(f"\n{'BY CATEGORY':─<55}")
        for cat, data in sorted(stats["by_category"].items(),
                                key=lambda x: x[1]["profit"], reverse=True):
            wr    = data["wins"] / data["trades"] if data["trades"] else 0
            color = Fore.GREEN if data["profit"] >= 0 else Fore.RED
            print(color + f"  {cat:<22} {data['trades']:>3} trades  "
                  f"{wr:>5.0%} WR  ${data['profit']:>+8.2f}" + Style.RESET_ALL)

    if stats["confidence"]:
        print(f"\n{'CONFIDENCE CALIBRATION':─<55}")
        for bucket in sorted(stats["confidence"].keys()):
            data = stats["confidence"][bucket]
            if data["trades"] > 0:
                wr  = data["wins"] / data["trades"]
                bar = "█" * int(wr * 20)
                print(f"  {bucket:<10} {data['trades']:>3} trades  {wr:.0%}  {bar}")

    if stats["open_trades"]:
        print(f"\n{'OPEN POSITIONS':─<55}")
        for t in stats["open_trades"][-5:]:
            print(f"  • {t['question'][:50]}...")
            print(f"    {t['outcome']}  ${safe_float(t['amount_usd']):.2f}  "
                  f"conf {safe_float(t['ai_confidence']):.0%}  "
                  f"edge {safe_float(t['edge']):+.0%}")

    if recs:
        print(f"\n{'💡 RECOMMENDATIONS':─<55}")
        for r in recs:
            print(f"  {r}")

    print(Fore.CYAN + "\n" + "═" * 58 + Style.RESET_ALL)


if __name__ == "__main__":
    trades = load_trades()
    if trades:
        stats = analyze(trades)
        recs  = recommend(stats)
        print_report(stats, recs)
        with open("performance_report.json", "w") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "summary":      {k: v for k, v in stats.items() if k != "open_trades"},
                "recommendations": recs,
            }, f, indent=2)
        print(f"\n📁 Report saved to performance_report.json")

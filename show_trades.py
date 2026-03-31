import json
import csv
import os
from datetime import datetime
from colorama import init, Fore, Style
init(autoreset=True)

def show_trades():
    # Try trades.csv first
    if os.path.exists("trades.csv"):
        print(Fore.CYAN + "\n📊 ALL TRADES\n" + "=" * 55)
        with open("trades.csv", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            trades = list(reader)
        
        if not trades:
            print("No trades yet.")
            return

        open_trades  = [t for t in trades if t.get("status") == "open"]
        closed_trades = [t for t in trades if t.get("status") != "open"]

        print(Fore.GREEN + f"\n✅ OPEN POSITIONS ({len(open_trades)})")
        print("-" * 55)
        for t in open_trades:
            print(f"  Market:     {t['question'][:55]}")
            print(f"  Bet:        {t['outcome']} @ ${float(t['amount_usd']):.2f}")
            print(f"  Confidence: {float(t['ai_confidence']):.0%}")
            print(f"  Edge:       {float(t['edge']):+.0%}")
            print(f"  Date:       {t['timestamp'][:10]}")
            print(f"  URL:        {t.get('url', 'N/A')}")
            print()

        if closed_trades:
            print(Fore.YELLOW + f"\n📁 CLOSED TRADES ({len(closed_trades)})")
            print("-" * 55)
            for t in closed_trades:
                status_color = Fore.GREEN if t.get("status") == "won" else Fore.RED
                print(status_color + f"  {t.get('status', '?').upper()} — {t['question'][:50]}")
                print(f"  Bet: {t['outcome']} @ ${float(t['amount_usd']):.2f}")
                print()

        print(Fore.CYAN + "=" * 55)
    else:
        print(Fore.RED + "No trades.csv file found. The bot needs to run first!")

if __name__ == "__main__":
    show_trades()
```

Click **Commit new file**.

Then to run it, open Terminal, navigate to your kalshi_bot folder and type:
```
python show_trades.py

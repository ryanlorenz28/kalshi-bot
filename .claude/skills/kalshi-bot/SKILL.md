---
name: kalshi-bot
description: Working rules, architecture, deploy workflow, and past-bug lessons for Ryan's live Kalshi trading bot (ryanlorenz28/kalshi-bot). Use this skill for ANY work in this repo — editing main.py, kalshi_client.py, ai_analyzer.py, bot_config.py or status_server.py, debugging orders or Railway deploys, changing market selection, reviewing trades or positions, adding signals (including whale/leaderboard tracking), or answering questions about how the bot works. This bot trades real money, so load this before touching any file.
---

# Kalshi Bot

This bot places **real-money trades**. A push to `main` goes live on Railway within minutes. Treat every change as a production change.

Ryan owns the bot and has no prior coding experience. He approves changes; you explain them in plain English.

---

## 1. What the bot does

A Python bot that uses Claude (`claude-sonnet-4-6`) to analyze binary Kalshi macro prediction markets (jobs, Fed, GDP, CPI, crypto, equities, commodities, a few misc events) and place trades when it finds enough edge. Sizing (`bot_config.bet_size`, v2.4+) is 10% Kelly on the real edge: Claude's probability for the side being bought versus that side's price plus the 3¢ fill buffer. The bet is capped at 10% of balance and $15, and the trade is skipped when the math says under $3.

| File | Role |
|---|---|
| `main.py` | Main loop: fetch markets, analyze, size, trade, manage positions, stop losses |
| `kalshi_client.py` | Kalshi Trade API v2: auth/signing, market fetch, `place_order`, `sell_position`, `_to_book_order` |
| `ai_analyzer.py` | Builds context (live FRED + CoinGecko data) and asks Claude for a probability |
| `bot_config.py` | Limits, sizing, edge threshold, series list, per-series caps |
| `status_server.py` | `/status` endpoint for Ryan's dashboard; must read `PORT` from env |
| `auto_blacklist.txt` | Persisted list of zero-fill tickers |

External data: FRED (`DFEDTARU`, `UNRATE`, `PAYEMS`, plus last released real GDP, labeled as lagging), CoinGecko for crypto prices.

Hosting: Railway, auto-deploys from the `main` branch. In-process state resets on every redeploy.

Series list: verified tickers only, in priority order (jobs/Fed/GDP first, CPI last). See `bot_config.py` for the live list. `KXMVE` combo/parlay tickers are filtered out on both the targeted and fallback fetch paths. Tesla (`KXTSLA`) and Nvidia (`KXNVDA`) are removed on purpose.

---

## 2. Hard rules

These are not suggestions. If a task seems to require breaking one, stop and ask Ryan.

1. **Protected settings need Ryan's explicit "yes" in this session.** Do not change, even "temporarily":
   - Money limits: `REAL_MONEY_LIMIT`, `TOTAL_EXPOSURE_LIMIT`, daily loss limit, `MAX_OPEN_POSITIONS`
   - Sizing: `MIN_BET_SIZE`, `MAX_BET_SIZE`, Kelly fraction, `MIN_EDGE_TO_TRADE`, `MAX_EXPOSURE_PER_SERIES` and its overrides
   - Stop-loss thresholds and dead-position rules
   - Anything that switches the bot between paper/dry-run and live
   - Order logic in `place_order`, `sell_position`, `_to_book_order`
   Show Ryan the exact before/after values and wait for approval.
2. **Never touch secrets.** Never print, log, echo, commit, or paste API keys, the Kalshi private key, the Anthropic key, FRED keys, or GitHub tokens. Never hardcode them. Never `cat` `.env` files. If a key looks wrong, tell Ryan which env var to check in Railway; don't ask him to paste it.
3. **Paper/dry-run first.** Any change to trading behavior runs in dry-run before it reaches `main`. First check `bot_config.py` and `main.py` for an existing paper/dry-run flag (grep for `PAPER`, `DRY_RUN`, `SIMULATE`). If none exists, propose adding a `DRY_RUN` env var that makes `place_order` and `sell_position` log the order they *would* send and return without calling Kalshi. Adding that flag is itself an order-logic change, so get approval.
4. **Plan before editing.** Before changing code, write a short plan: which files, which functions, what changes, what could go wrong, how you'll test it. Wait for Ryan's OK on anything touching rule 1 or deployment.
5. **Check your work after editing.** Re-read the changed lines, compile every changed file, and confirm the change does what the plan said and nothing more. Small, targeted edits only; no drive-by refactors.
6. **Pushing to `main` is a deploy.** Don't push without telling Ryan it will go live and getting a go-ahead.
7. **Never invent "live" data.** If a data source isn't available, say so in the analyzer context. Don't hardcode snapshot numbers.
8. **Never add a series ticker you haven't verified** against Kalshi's official fee schedule or a live API call that returns markets.

---

## 3. Workflow, step by step

1. **Sync.** `git pull origin main`. Confirm you're on `main` and the remote is `ryanlorenz28/kalshi-bot`.
2. **Locate before editing.** Use `grep -n "<pattern>" <file>.py` to find the spot, then view only that line range. Don't rewrite whole files.
3. **Plan** (rule 4). Get approval if needed.
4. **Edit** with targeted replacements.
5. **Compile every changed file:** `python3 -m py_compile <file>.py && echo OK`. Fix anything that fails before going further.
6. **Dry-run test** (rule 3) for any trading-behavior change: run one cycle locally with `DRY_RUN` on and check the logs for the expected decisions and order payloads. Never run locally in live mode.
7. **Bump the version marker** in the startup log for significant changes (e.g. `[v2.2 — <short description>]`).
8. **Commit** with a plain message describing what changed and why.
9. **Push** to `main` only after Ryan's go-ahead.
10. **Confirm the deploy.** Ask Ryan to check Railway logs for the new version marker. If the old marker still shows, Railway served a cached build: make a trivial commit to a non-Python file (e.g. `nixpacks.toml`) and push again.
11. **Confirm health.** First live cycle should show markets fetched, analysis running, and no 4xx errors. `/status` should respond.

---

## 4. Past bugs and lessons

Check this list before working in any of these areas.

**Kalshi API**
- The v1 order endpoint was deprecated and returned a quiet 410 for ~3 months before anyone noticed. Orders now go to V2 `/portfolio/events/orders`. Treat any 404/410 from Kalshi as a possible deprecation and say so loudly.
- V2 order shape: prices and counts are **strings**, orders are `bid`/`ask` on the **YES leg only**. `_to_book_order` maps yes/no + buy/sell onto that. Don't bypass it.
- `count` must be a float (V2 requires `>= 0.01`), not an int.
- Don't send `time_in_force`; it caused repeated 400/409s and was removed. (Note: `_submit_order_v2` currently sends `"time_in_force": "immediate_or_cancel"`, and that has been working live. Leave it unless orders start failing.)
- Sells (v2.7+) are priced off the side's **bid** (NO bid = 1 − YES ask) minus `SELL_BUFFER` (2¢). Before v2.7, sells were priced off the ask, so they almost never filled ("Sell order not filled").
- `sell_position` first checks Kalshi holdings and only sells the side actually held, capped at the contracts held. On the YES-only book, "selling" a side you don't hold *opens* the opposite position: that's how a 1-contract Gemini YES got bought. If the holdings lookup fails, it doesn't sell. `reduce_only` in the payload is still `False`; its behavior on NO positions hasn't been verified.
- Signing: RSA-PSS with DIGEST_LENGTH salt (not PKCS1v15), and the signed path must include the full `/trade-api/v2` prefix.
- Private key from Railway env must go through the normalization function (handles literal `\n`, missing PEM headers, single-line base64). Don't remove it.
- Authenticated endpoints fail on an unfunded account. Rule this out before debugging auth.

**YES/NO asymmetry** (the most repeated bug source)
- The price to buy NO is the NO ask, which equals `1 - yes_bid`. It is **not** `1 - yes_ask`, which understates NO's cost by the spread and overstated NO edge until v2.3. `_normalize` uses `_no_buy_price` for this; don't reintroduce `1 - yes_price`.
- `my_probability` is always relative to YES. When logging, computing edge, or sizing a NO trade, use `1 - my_probability` and the NO price. The analyzer re-checks NO-trade edge against the real NO price.

**Bet sizing**
- Before v2.4, size came from Claude's *confidence* versus 50%, ignoring the market price, so nearly every trade hit the $15 max. Size must come from the side's probability versus the price actually paid.
- With a ~$160 balance, 10% Kelly needs roughly 12+ points of edge (after the 3¢ buffer) to clear the $3 minimum. Few trades placed is expected, not a bug.
- `place_order` rounds contracts down, so actual cost can land a few cents under the sized amount.
- Any change touching side logic needs a dry-run check of one YES trade and one NO trade.

**Risk logic**
- `daily_loss` counts **realized losses only** (stop-loss exits), never new trade spend. It used to trip the daily limit on busy days.
- Hard -70% stop loss fires regardless of days to expiry.
- Dead positions (down 70%+ and price < $0.10) are removed from active tracking and never sold. Repeated sells on illiquid near-zero contracts just waste cycles. `KXFED-27APR-T4.00` is a known dead position; leave it.
- The per-series cap exists because one Tesla series once absorbed $110+ and lost it all. Never weaken it without approval.
- `fill_count=0` tickers are auto-blacklisted to `auto_blacklist.txt`.
- The $450 `REAL_MONEY_LIMIT` means **money at risk at once** (Ryan's choice), not lifetime spend. On startup, positions load from Kalshi using `market_exposure_dollars` (cost of contracts held now). Before v2.5 the bot used `total_traded_dollars` (lifetime volume) and also loaded markets with 0 contracts held (sold or settled). That kept the position limit and money caps maxed, so no new trades ran. Take-profit and stop-loss sales both subtract their cost from `real_money_spent`. Dead positions stay counted because they're still held.

**Market selection**
- Guessed tickers (`KXAAPL`, `KXCLIM`, `KXVIX`) silently return nothing and waste the fetch budget.
- CPI is last in the series order because its cap fills fast and was crowding out all analysis slots.
- `KXMVE` combo bundles must stay filtered on both fetch paths.

**Data**
- Hardcoded "live data" is worse than no data. It once fed the AI June figures labeled as current.
- No free live feed exists for Tesla deliveries/production, which is why that series is out.
- A mid-cycle "likely losing" read can be wrong (a GDP position called a loser paid +115%). Don't recommend selling on a hunch.
- Don't hardcode release dates (a stale CPI date was removed from the analyzer).

**Railway**
- Railway once watched `master` while edits went to `main`, so deploys silently didn't happen. If changes don't show up, check the deploy branch first.
- Cached builds: see workflow step 10.
- `status_server.py` must use `int(os.environ.get("PORT", 8080))`; a hardcoded port gave a 404.
- Redeploys wipe in-process state. Anything that must survive goes in a file or external store.
- Durable storage is a Railway volume at `$DATA_DIR` (`/data`). `prediction_log.py` (v2.8) writes every Claude prediction to `$DATA_DIR/predictions.jsonl` and settlements to `results.json`, then scores Claude vs the market mid price every 6h (logs + `/status` → `predictions`). `trades.csv` and `daily_loss` are still in-container and get wiped on redeploy.

---

## 5. Reporting back to Ryan

- Short and plain. No filler, no preamble, no pep talk.
- Lead with the result: what changed, whether it's live, whether anything needs his action.
- Explain technical terms in one plain sentence if you must use them.
- Give exact steps when he has to do something (e.g. "In Railway → Deployments → View logs, look for `[v2.2`").
- Say clearly when something failed or you're unsure. Never call something fixed until it's confirmed in logs.
- Ask for approval as a direct yes/no question with the exact values involved.

Example:
> Fixed: NO trades were logging the wrong edge. Changed 2 lines in `main.py`, compiled OK, dry-run showed correct edge on 1 YES and 1 NO trade. Not pushed yet. Push to `main` and go live? (yes/no)

---

## 6. Paper-testing a whale-tracking signal

Goal: find out whether copying top Polymarket/Kalshi leaderboard traders would have made money, **without placing a single real order**.

**Isolation (non-negotiable)**
- Build it as a separate module (e.g. `whale_signal.py`) that never imports or calls `place_order` or `sell_position`.
- It records "shadow trades" only. It does not feed the live sizing, edge, or trade decision until Ryan explicitly approves after reviewing results.
- Gate it behind its own env flag (e.g. `WHALE_SHADOW=on`), off by default.

**Before writing code, verify data sources**
- Confirm which leaderboard and per-trader trade data is actually available and allowed (Polymarket's public data and on-chain activity; whatever Kalshi publicly exposes, which may be limited). Check current docs and terms; don't assume endpoints exist. Report findings to Ryan before building.
- Polymarket markets won't map cleanly to Kalshi markets. Only shadow-trade cases where the event, resolution source, and date clearly match, or keep Polymarket shadow trades scored on Polymarket's own resolution.

**What to record for each shadow trade**
- Time the whale's trade was detected (not when it happened), trader ID, market, side, whale's price, and **the price you could have gotten at detection time**. Copy-traders always arrive late; scoring at the whale's price overstates results.
- Hypothetical size using the bot's normal sizing rules, with Kalshi fees included.
- Final resolution and P&L once the market settles.

**Persistence**
- Railway state resets on redeploy, so shadow trades must be written somewhere durable (a Railway volume, or a small external store). Ask Ryan which before building.
- Add a read-only section to `/status` so shadow results show on the dashboard.

**Deciding whether it works**
- Compare against two baselines on the same markets: the bot's own picks, and simply doing nothing.
- Wait for a meaningful number of **resolved** shadow trades across several series and weeks before drawing conclusions. A handful of wins is noise.
- Watch for the signal only working on one trader or one series; that's concentration risk, not an edge.
- Present Ryan a plain summary: number resolved, win rate, net P&L after fees and slippage, versus baselines.

**Going live (only after all of the above)**
- Requires Ryan's explicit approval, as a separate change.
- Start as a small input to the existing analysis (e.g. context for the AI) or with its own tiny dollar cap, inside all existing limits. Never as a way around them.

"""
ai_analyzer.py — Uses Claude AI to analyze Kalshi markets.

Features:
  - System prompt establishes Claude as a calibrated superforecaster
  - Chain-of-thought reasoning before final answer
  - Reddit + DuckDuckGo news sources
  - Category-specific specialist context with LIVE data injection
  - Safety rules blocking near-zero/near-100% markets
  - Today's date injected for timing accuracy
"""

import re
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional
import time
import anthropic
from polymarket_signal import PolymarketSignal

# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an elite prediction market analyst and calibrated superforecaster
specializing in Kalshi markets. You have studied thousands of binary markets and understand
exactly how crowds misprice events.

YOUR CORE PHILOSOPHY:
- Calibration above all: a 70% confidence call should be right ~70% of the time
- Never chase longshots: markets priced below 4% are almost always correctly priced
- Never fade near-certainties: markets priced above 96% are almost always correctly priced
- The crowd is often right — you need a specific, concrete reason to disagree
- Recency bias is your enemy: one headline does not override weeks of market wisdom
- Kalshi markets are legally regulated and resolve precisely — read resolution criteria carefully

YOUR EDGE COMES FROM:
1. Recognizing when news is already priced in vs genuinely new information
2. Knowing base rates for recurring event types (Fed meetings, economic reports, sports)
3. Spotting emotional overreactions to recent news
4. Understanding exact resolution criteria — Kalshi markets often resolve on technicalities

CALIBRATION RULES:
- If you have NO strong view, set confidence to 0.45 and recommend SKIP
- Only recommend YES/NO if you have a SPECIFIC reason the crowd is wrong
- Edge alone is not enough — you need to understand WHY the crowd is wrong
- When uncertain, the market price is probably correct

You always reason step by step before giving your final answer.
You respond only in the exact format requested — no extra text."""


class AIAnalyzer:

    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.today  = datetime.now(timezone.utc).strftime("%B %d, %Y")
        self.poly   = PolymarketSignal()

        # Decision cache + usage counters (see _cache_lookup / pop_stats)
        self._cache      = {}
        self.cycle_calls = self.cycle_hits = self.cycle_in = self.cycle_out = 0
        self.total_calls = self.total_hits = 0
        self._live_data_cache = {}  # cache live data per cycle to avoid redundant fetches

    def analyze_market(self, market: dict) -> dict:
        """Full analysis pipeline for one market, with a decision cache.

        Claude is only called when there is no usable earlier answer. An earlier
        answer is reused while: the YES price has moved less than CACHE_PRICE_MOVE,
        it is younger than its time limit (shorter as resolution gets closer), and —
        for macro series — the underlying FRED data has not changed (i.e. no new release).
        """
        try:
            live  = None
            entry = self._cache_lookup(market)
            if entry is not None and self._is_macro(market):
                live   = self._fetch_live_data(market)
                fp_now = self._fingerprint(live)
                # If the data fetch failed (empty), keep the cached answer rather than pay to re-ask
                if fp_now and fp_now != entry.get("fp"):
                    entry = None
            if entry is not None:
                return self._decision_from_cache(entry, market)

            news = self._fetch_news(market)
            if live is None:
                live = self._fetch_live_data(market)
            # Polymarket signal — isolated so any failure never kills analysis
            try:
                poly_sig = self.poly.get_signal(
                    market.get("question", ""),
                    our_side="YES"
                )
            except Exception:
                poly_sig = {"signal": "NEUTRAL", "detail": "Polymarket signal unavailable"}
            prompt   = self._build_prompt(market, news, live, poly_sig)
            raw      = self._call_claude(prompt)
            fields   = self._parse_fields(raw)
            decision = self._apply_rules(dict(fields), market)
            decision["poly_signal"] = poly_sig.get("signal", "NEUTRAL")
            self._cache_store(market, raw, fields, live, poly_sig)
            return decision
        except Exception as e:
            self.logger.error(f"Analysis error: {e}")
            return {"should_trade": False, "reason": str(e)}

    # ─── LIVE DATA FETCHING ──────────────────────────────────────────────────

    def _fetch_live_data(self, market: dict) -> dict:
        """Fetch real current data relevant to this market type."""
        question = market.get("question", "").lower()
        data = {}

        try:
            if any(x in question for x in ["cpi", "inflation"]):
                data = self._get_cpi_data()
            elif any(x in question for x in ["gdp"]):
                data = self._get_gdp_data()
            elif any(x in question for x in ["fed", "federal reserve", "interest rate", "fomc"]):
                data = self._get_fed_data()
            elif any(x in question for x in ["unemployment", "jobs", "nonfarm", "payroll"]):
                data = self._get_jobs_data()
            elif any(x in question for x in ["s&p", "s&p 500", "sp500", "spx", "inx"]):
                data = self._get_sp500_data()
            elif any(x in question for x in ["bitcoin", "btc"]):
                data = self._get_crypto_price("bitcoin")
            elif any(x in question for x in ["ethereum", "eth"]):
                data = self._get_crypto_price("ethereum")
            elif any(x in question for x in ["tesla"]):
                data = self._get_tesla_data()
        except Exception as e:
            self.logger.error(f"Live data fetch error: {e}")

        return data

    def _get_cpi_data(self) -> dict:
        """Fetch latest CPI data from FRED."""
        try:
            # Get recent CPI readings from FRED (free, no API key needed for this endpoint)
            resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": "CPIAUCSL",
                    "api_key": self.config.FRED_API_KEY,
                    "file_type": "json",
                    "limit": 3,
                    "sort_order": "desc",
                    "observation_start": "2025-01-01",
                },
                timeout=8,
            )
            if resp.status_code == 200:
                obs = resp.json().get("observations", [])
                if obs:
                    readings = [f"{o['date']}: {o['value']}" for o in obs]
                    return {
                        "label": "Latest CPI readings (FRED)",
                        "data": " | ".join(readings),
                        "note": "Monthly CPI index values — compare trend to market threshold"
                    }
        except Exception:
            pass

        # Fallback if FRED is unreachable this cycle
        return {
            "label": "CPI data",
            "data": "Live CPI data unavailable this cycle.",
            "note": "No reliable current reading — weight the market price heavily and require a strong independent reason to disagree."
        }

    def _get_gdp_data(self) -> dict:
        """Fetch the most recent real GDP growth reading from FRED.
        Note: Atlanta Fed's GDPNow (an intra-quarter running estimate) has no
        free public API, so this uses the last officially released GDP print
        instead. It will lag the current quarter — the AI is told this
        explicitly so it doesn't overweight it as if it were today's number."""
        try:
            resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": "A191RL1Q225SBEA",  # real GDP, % change, annualized
                    "api_key": self.config.FRED_API_KEY,
                    "file_type": "json",
                    "limit": 2,
                    "sort_order": "desc",
                },
                timeout=8,
            )
            if resp.status_code == 200:
                obs = resp.json().get("observations", [])
                if obs:
                    readings = [f"{o['date']}: {o['value']}% annualized" for o in obs]
                    return {
                        "label": "Latest released real GDP growth (FRED, quarterly)",
                        "data": " | ".join(readings),
                        "note": ("This is the last OFFICIALLY RELEASED quarter, not a live "
                                 "intra-quarter tracker — it will lag the market's current quarter "
                                 "by one or more releases. Do not treat it as today's running estimate; "
                                 "use it only as a recent-trend anchor and weight the market price "
                                 "accordingly if this data is old relative to the question's period.")
                    }
        except Exception:
            pass
        return {
            "label": "GDP data",
            "data": "Live GDP data unavailable this cycle.",
            "note": "No reliable current reading — weight the market price heavily and require a strong independent reason to disagree."
        }

    def _get_fed_data(self) -> dict:
        """Fetch the current Fed funds target range from FRED."""
        try:
            resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": "DFEDTARU",  # upper bound of fed funds target range
                    "api_key": self.config.FRED_API_KEY,
                    "file_type": "json",
                    "limit": 1,
                    "sort_order": "desc",
                },
                timeout=8,
            )
            upper = None
            as_of = None
            if resp.status_code == 200:
                obs = resp.json().get("observations", [])
                if obs:
                    upper = obs[0]["value"]
                    as_of = obs[0]["date"]
            if upper:
                return {
                    "label": "Federal Reserve data (FRED, upper bound of target range)",
                    "data": f"Fed funds target upper bound as of {as_of}: {upper}%",
                    "note": ("This reflects the rate as of the FRED observation date above, not "
                             "necessarily today — check whether an FOMC meeting has occurred since "
                             "then before assuming the rate is unchanged. Compare directly to the "
                             "market's threshold.")
                }
        except Exception:
            pass
        return {
            "label": "Federal Reserve data",
            "data": "Live Fed rate data unavailable this cycle.",
            "note": "No reliable current reading — weight the market price heavily and require a strong independent reason to disagree."
        }

    def _get_jobs_data(self) -> dict:
        """Fetch the latest unemployment rate and nonfarm payrolls from FRED."""
        try:
            unrate_resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": "UNRATE",
                    "api_key": self.config.FRED_API_KEY,
                    "file_type": "json",
                    "limit": 1,
                    "sort_order": "desc",
                },
                timeout=8,
            )
            payems_resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": "PAYEMS",
                    "api_key": self.config.FRED_API_KEY,
                    "file_type": "json",
                    "limit": 2,
                    "sort_order": "desc",
                },
                timeout=8,
            )
            parts = []
            if unrate_resp.status_code == 200:
                obs = unrate_resp.json().get("observations", [])
                if obs:
                    parts.append(f"Unemployment rate as of {obs[0]['date']}: {obs[0]['value']}%")
            if payems_resp.status_code == 200:
                obs = payems_resp.json().get("observations", [])
                if len(obs) >= 2:
                    change_k = (float(obs[0]["value"]) - float(obs[1]["value"])) * 1  # PAYEMS is already in thousands
                    parts.append(f"Nonfarm payrolls change ({obs[1]['date']}→{obs[0]['date']}): {change_k:+.0f}k")
            if parts:
                return {
                    "label": "Jobs/unemployment data (FRED)",
                    "data": " | ".join(parts),
                    "note": "Compare market threshold to this trend — these are the two headline monthly figures."
                }
        except Exception:
            pass
        return {
            "label": "Jobs/unemployment data",
            "data": "Live jobs data unavailable this cycle.",
            "note": "No reliable current reading — weight the market price heavily and require a strong independent reason to disagree."
        }

    def _get_crypto_price(self, coin: str) -> dict:
        """Fetch current crypto price from CoinGecko (free, no key needed)."""
        try:
            resp = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin, "vs_currencies": "usd", "include_24hr_change": "true"},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json().get(coin, {})
                price = data.get("usd", "unknown")
                change = data.get("usd_24h_change", 0)
                return {
                    "label": f"Live {coin.title()} price",
                    "data": f"Current price: ${price:,.0f} | 24h change: {change:+.1f}%",
                    "note": "Compare current price to market threshold to assess probability"
                }
        except Exception:
            pass
        return {}

    def _get_sp500_data(self) -> dict:
        """Fetch S&P 500 level from Yahoo Finance (free, no key needed)."""
        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
                params={"interval": "1d", "range": "5d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            if resp.status_code == 200:
                result = resp.json().get("chart", {}).get("result", [{}])[0]
                meta = result.get("meta", {})
                price = meta.get("regularMarketPrice") or meta.get("previousClose")
                prev  = meta.get("chartPreviousClose") or meta.get("previousClose")
                if price:
                    change_pct = ((price - prev) / prev * 100) if prev else 0
                    return {
                        "label": "Live S&P 500 level (Yahoo Finance)",
                        "data":  f"Current S&P 500: {price:,.2f} | Change: {change_pct:+.2f}%",
                        "note":  "Compare current level to market threshold directly"
                    }
        except Exception:
            pass
        return {
            "label": "S&P 500 data",
            "data":  "Live S&P 500 data unavailable this cycle.",
            "note":  "Weight the market price heavily without current level data."
        }

    def _get_tesla_data(self) -> dict:
        """Tesla delivery/production numbers change every quarter and there's no
        free real-time API for them, so — unlike CPI/Fed/jobs above — this can't
        be fetched live. Rather than assert a specific hardcoded number that will
        silently go stale (as the old version did), this is explicit about the
        limitation so the AI doesn't treat guessed figures as current fact."""
        return {
            "label": "Tesla delivery/production data",
            "data": "No live delivery/production feed available for this cycle.",
            "note": ("Do not assume specific delivery/production numbers from training data are "
                     "still current — Tesla reports new quarterly figures every ~3 months and any "
                     "number you recall may already be superseded. Treat this market with extra "
                     "caution and require a genuinely strong, current, articulable reason before "
                     "trading against the market price; when unsure, prefer SKIP.")
        }

    # ─── NEWS FETCHING ───────────────────────────────────────────────────────

    def _fetch_news(self, market: dict) -> List[Dict]:
        keywords = self._keywords(market.get("question", ""))
        results  = []
        results += self._reddit(keywords, market)
        if not results:
            results += self._duckduckgo(keywords)
        return results[:self.config.NEWS_ARTICLES_TO_FETCH + 2]

    def _reddit(self, keywords: str, market: dict) -> List[Dict]:
        question = market.get("question", "").lower()
        if any(x in question for x in ["fed", "rate", "inflation", "gdp", "unemployment"]):
            subreddit = "economics"
        elif any(x in question for x in ["bitcoin", "crypto", "btc", "eth"]):
            subreddit = "CryptoCurrency"
        elif any(x in question for x in ["election", "president", "congress", "senate"]):
            subreddit = "politics"
        elif any(x in question for x in ["stock", "market", "s&p", "nasdaq"]):
            subreddit = "investing"
        else:
            subreddit = "worldnews"
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{subreddit}/search.json",
                params={"q": keywords, "sort": "new", "limit": 4, "t": "week", "restrict_sr": "1"},
                headers={"User-Agent": "KalshiBot/1.0"},
                timeout=8,
            )
            if resp.status_code != 200:
                return []
            posts = resp.json().get("data", {}).get("children", [])
            results = []
            for post in posts:
                d = post.get("data", {})
                title = d.get("title", "")
                score = d.get("score", 0)
                if title and len(title) > 10 and score >= 5:
                    results.append({
                        "source":  f"Reddit r/{subreddit} ({score:,} upvotes)",
                        "date":    datetime.fromtimestamp(d.get("created_utc", 0)).strftime("%Y-%m-%d"),
                        "title":   title,
                        "summary": d.get("selftext", "")[:150],
                    })
            return results
        except Exception:
            return []

    def _duckduckgo(self, keywords: str) -> List[Dict]:
        try:
            resp = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": keywords, "format": "json", "no_html": "1"},
                timeout=8,
            )
            resp.raise_for_status()
            data    = resp.json()
            results = []
            abstract = data.get("AbstractText", "")
            if abstract:
                results.append({
                    "source":  data.get("AbstractSource", "Wikipedia"),
                    "date":    datetime.now().strftime("%Y-%m-%d"),
                    "title":   data.get("Heading", keywords),
                    "summary": abstract[:250],
                })
            return results
        except Exception:
            return []

    # ─── PROMPT BUILDING ─────────────────────────────────────────────────────

    def _build_prompt(self, market: dict, news: List[Dict], live: dict, poly_sig: dict = None) -> str:
        question  = market.get("question", "")
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)
        no_price  = market.get("outcomes", [{}, {}])[1].get("price", round(1 - yes_price, 4))
        category  = market.get("category", "General")
        days_left = market.get("days_to_resolve", 1)
        volume    = market.get("volume", 0)
        liquidity = market.get("liquidity", 0)
        desc      = market.get("description", "")

        if yes_price < 0.05:
            sentiment = "Market considers this VERY UNLIKELY (<5%) — crowd is almost certainly right"
        elif yes_price > 0.95:
            sentiment = "Market considers this NEAR CERTAIN (>95%) — crowd is almost certainly right"
        elif yes_price > 0.70:
            sentiment = "Market leans YES strongly"
        elif yes_price < 0.30:
            sentiment = "Market leans NO strongly"
        else:
            sentiment = "Market is genuinely uncertain"

        if news:
            news_block = f"RECENT NEWS (as of {self.today}):\n"
            for i, a in enumerate(news, 1):
                news_block += f"  {i}. [{a['source']} | {a['date']}] {a['title']}\n"
                if a.get("summary"):
                    news_block += f"     -> {a['summary']}\n"
        else:
            news_block = "RECENT NEWS: No articles found. Rely on base rates and market data.\n"

        # ── LIVE DATA BLOCK ──────────────────────────────────────────────────
        if live:
            live_block = f"LIVE MARKET DATA — {live.get('label', 'Current data')}:\n"
            live_block += f"  {live.get('data', '')}\n"
            live_block += f"  Note: {live.get('note', '')}\n"
        else:
            live_block = "LIVE DATA: Not available for this market type.\n"

        # ── POLYMARKET SIGNAL BLOCK ──────────────────────────────────────────
        if poly_sig and poly_sig.get("signal") != "NEUTRAL":
            sig = poly_sig["signal"]
            detail = poly_sig["detail"]
            if sig == "AGREE":
                poly_block = f"POLYMARKET SMART MONEY SIGNAL: ✅ CONFIRMS — {detail}\n  → Top Polymarket traders are positioned the same way. This is a mild confidence booster but not a guarantee.\n"
            else:
                poly_block = f"POLYMARKET SMART MONEY SIGNAL: ⚠️  CONTRADICTS — {detail}\n  → Top Polymarket traders are on the OPPOSITE side. Require stronger evidence before trading.\n"
        else:
            poly_block = "POLYMARKET SIGNAL: Neutral or unavailable — no confirmation from top traders.\n"

        specialist = self._specialist_context(market)

        prompt = f"""Today's date: {self.today}

KALSHI MARKET TO ANALYZE:
"{question}"

{desc}

{'─' * 55}
MARKET DATA:
  YES price:      {yes_price:.2%} implied probability
  NO price:       {no_price:.2%} implied probability
  Volume:         ${volume:,.0f}
  Liquidity:      ${liquidity:,.0f}
  Days remaining: {days_left} days
  Category:       {category}
  Sentiment:      {sentiment}
{'─' * 55}
{live_block}
{'─' * 55}
{news_block}
{'─' * 55}
{specialist}
{'─' * 55}
{poly_block}
{'─' * 55}
STEP-BY-STEP REASONING REQUIRED:

  Step 1 — BASE RATE: How often do events like this actually happen historically?
  Step 2 — LIVE DATA CHECK: Does the live data above directly answer or constrain the probability?
  Step 3 — NEWS IMPACT: Does the news actually change the probability, or is it already priced in?
  Step 4 — RESOLUTION CHECK: How exactly does this Kalshi market resolve?
  Step 5 — EDGE CHECK: Is my estimate different from the market by at least {self.config.MIN_EDGE_TO_TRADE:.0%}? Why is the crowd wrong?
  Step 6 — CONFIDENCE CHECK: Am I genuinely confident (60%+) or just guessing?

{'─' * 55}
Respond in EXACTLY this format with no extra text:

TRADE: YES or NO or SKIP
CONFIDENCE: 0.0 to 1.0
MY_PROBABILITY: 0.0 to 1.0
EDGE: your probability minus market price as decimal
REASONING: one paragraph explaining your probability estimate and why the crowd may be wrong
KEY_RISKS: one sentence on the biggest risk to this trade

Only recommend YES or NO if you have at least {self.config.MIN_EDGE_TO_TRADE:.0%} edge AND 60%+ confidence AND a specific reason the crowd is wrong."""

        return prompt

    def _specialist_context(self, market: dict) -> str:
        question = market.get("question", "").lower()

        if any(x in question for x in ["fed", "federal reserve", "interest rate", "fomc"]):
            return """SPECIALIST CONTEXT — Federal Reserve:
  - CME FedWatch Tool is the gold standard — market price likely already reflects it
  - Fed decisions are telegraphed weeks ahead through speeches and minutes
  - Base rate: Fed follows through on heavily priced-in moves ~90% of the time
  - See LIVE DATA above for the current target range and its as-of date — do not assume a
    specific meeting date or rate from memory, as FOMC meetings happen roughly every 6 weeks
  - If priced above 85% or below 15%, it is almost certainly correct"""

        elif any(x in question for x in ["s&p", "nasdaq", "dow", "stock"]):
            return """SPECIALIST CONTEXT — Financial Markets:
  - Same-day price target markets are very hard to beat
  - Base rate: markets close up ~53% of days historically
  - Key drivers: pre-market futures, overnight news, macro data releases"""

        elif any(x in question for x in ["bitcoin", "btc", "crypto", "ethereum", "eth"]):
            return """SPECIALIST CONTEXT — Crypto:
  - Crypto price targets are highly volatile and hard to predict short-term
  - Key drivers: macro sentiment, ETF flows, regulatory news
  - Check LIVE DATA above — current price vs target is the most important factor"""

        elif any(x in question for x in ["inflation", "cpi"]):
            return """SPECIALIST CONTEXT — CPI/Inflation:
  - Bloomberg consensus forecasts are already baked into market prices
  - Base rate: actual CPI comes within 0.1% of consensus ~60% of the time
  - CPI is released monthly around the 10th-15th — check LIVE DATA above for the latest readings
  - Best edge: when recent monthly trend strongly diverges from the market threshold"""

        elif any(x in question for x in ["gdp"]):
            return """SPECIALIST CONTEXT — GDP:
  - No free real-time GDPNow feed is available — see LIVE DATA above for the last officially
    released quarter, which will lag the market's current quarter
  - Do not assume a specific quarter's release date or estimate from memory — GDP data ages fast
  - Base rate: quarterly GDP prints are revised more than people expect; treat any single
    estimate (including your own) with humility"""

        elif any(x in question for x in ["unemployment", "jobs", "nonfarm", "payroll"]):
            return """SPECIALIST CONTEXT — Jobs:
  - ADP report (released Wednesday before NFP) is a useful leading indicator
  - Base rate: NFP comes within 50k of consensus ~55% of the time — high variance
  - Unemployment rate is stickier — changes of >0.2% in one month are rare"""

        elif any(x in question for x in ["election", "president", "senate", "congress"]):
            return """SPECIALIST CONTEXT — Politics:
  - Polling averages beat individual polls — RealClearPolitics is the benchmark
  - Base rate: candidates leading by 5%+ in polls win ~80% of the time"""

        elif any(x in question for x in ["tesla", "nvidia", "apple", "earnings", "production", "deliveries"]):
            return """SPECIALIST CONTEXT — Companies:
  - Analyst consensus estimates are already priced in
  - No live delivery/production feed is available for this cycle — see LIVE DATA above
  - Do not assume specific delivery/production numbers or report dates from memory; they are
    almost certainly outdated by the time this market is being evaluated
  - This series has produced losing trades before — require an unusually strong, current,
    articulable reason before trading against the market price; prefer SKIP when in doubt"""

        else:
            return """SPECIALIST CONTEXT — General:
  - With no specialist data available, weight the market price heavily
  - Only trade if you have a specific, articulable reason the crowd is wrong
  - Default to SKIP unless confidence is very high"""

    # ─── CLAUDE API CALL ─────────────────────────────────────────────────────

    def _call_claude(self, prompt: str) -> str:
        message = self.client.messages.create(
            model=self.config.CLAUDE_MODEL,
            max_tokens=self.config.MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        self.cycle_calls += 1
        self.total_calls += 1
        usage = getattr(message, "usage", None)
        if usage is not None:
            self.cycle_in  += getattr(usage, "input_tokens", 0) or 0
            self.cycle_out += getattr(usage, "output_tokens", 0) or 0
        return message.content[0].text

    # ─── RESPONSE PARSING ────────────────────────────────────────────────────

    def _parse(self, text: str, market: dict) -> dict:
        return self._apply_rules(self._parse_fields(text), market)

    def _parse_fields(self, text: str) -> dict:
        lines  = text.strip().split("\n")
        result = {
            "should_trade":   False,
            "outcome_to_buy": None,
            "confidence":     0.0,
            "my_probability": 0.5,
            "edge":           0.0,
            "reasoning":      "",
            "key_risks":      "",
            "reason":         "low confidence",
        }

        for line in lines:
            if line.startswith("TRADE:"):
                val = line.split(":", 1)[1].strip()
                if val in ("YES", "NO"):
                    result["outcome_to_buy"] = val.capitalize()
            elif line.startswith("CONFIDENCE:"):
                try:
                    result["confidence"] = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("MY_PROBABILITY:"):
                try:
                    result["my_probability"] = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("EDGE:"):
                try:
                    result["edge"] = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("REASONING:"):
                result["reasoning"] = line.split(":", 1)[1].strip()
            elif line.startswith("KEY_RISKS:"):
                result["key_risks"] = line.split(":", 1)[1].strip()

        # Clamp values
        result["confidence"]     = max(0.0, min(1.0, result["confidence"]))
        result["my_probability"] = max(0.0, min(1.0, result["my_probability"]))

        return result

    def _apply_rules(self, result: dict, market: dict) -> dict:
        """Safety rules + thresholds. Shared by fresh answers and cached ones."""
        # ── SAFETY RULES ──────────────────────────────────────────────────────
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)

        if yes_price < 0.04 or yes_price > 0.96:
            return {**result,
                    "should_trade": False,
                    "reason": f"Market price {yes_price:.1%} is near limit — crowd is almost certainly correct"}

        if abs(result["edge"]) < self.config.MIN_EDGE_TO_TRADE:
            return {**result,
                    "should_trade": False,
                    "reason": f"Edge {abs(result['edge']):.1%} is below {self.config.MIN_EDGE_TO_TRADE:.0%} minimum threshold"}

        if result["confidence"] < self.config.MIN_CONFIDENCE_TO_TRADE:
            return {**result,
                    "should_trade": False,
                    "reason": f"Confidence {result['confidence']:.0%} below threshold {self.config.MIN_CONFIDENCE_TO_TRADE:.0%}"}

        # NO trades: re-check edge against the real NO price (Claude's EDGE is
        # relative to YES and ignores the bid/ask spread).
        if result["outcome_to_buy"] == "No":
            no_price = market.get("outcomes", [{}, {}])[1].get("price", 1 - yes_price)
            no_edge  = (1 - result["my_probability"]) - no_price
            if no_edge < self.config.MIN_EDGE_TO_TRADE:
                return {**result,
                        "should_trade": False,
                        "reason": f"NO edge {no_edge:.1%} at NO price {no_price:.2f} is below {self.config.MIN_EDGE_TO_TRADE:.0%} minimum threshold"}

        if result["outcome_to_buy"]:
            result["should_trade"] = True
            result["reason"]       = "opportunity found"
        else:
            result["reason"] = "Claude recommended SKIP"

        return result

    # ─── DECISION CACHE ──────────────────────────────────────────────────────

    MACRO_PREFIXES = ("KXPAYROLLS", "KXU3", "KXFED", "KXRATECUTCOUNT",
                      "KXGDP", "KXCPI", "KXPCE")

    def _is_macro(self, market: dict) -> bool:
        return str(market.get("id", "")).split("-")[0].startswith(self.MACRO_PREFIXES)

    @staticmethod
    def _fingerprint(live) -> str:
        """Short signature of the live data. FRED data only changes on a new release.
        A failed fetch returns "" (the fallback text says "unavailable this cycle"),
        so an outage keeps the cached answer instead of re-asking."""
        if isinstance(live, dict):
            data = str(live.get("data", "") or "")
            return "" if "unavailable this cycle" in data else data
        return ""

    def _ttl_minutes(self, market: dict) -> float:
        cfg  = self.config
        days = market.get("days_to_resolve", 999)
        near = getattr(cfg, "CACHE_TTL_NEAR_MIN", 30)    # resolves within a day
        week = getattr(cfg, "CACHE_TTL_WEEK_MIN", 120)   # resolves within a week
        far  = getattr(cfg, "CACHE_TTL_FAR_MIN", 360)    # everything else
        ttl  = near if days <= 1 else (week if days <= 7 else far)
        # Macro markets only move on data releases, which the fingerprint check catches
        if self._is_macro(market) and days > 1:
            ttl = max(ttl, far)
        return ttl

    def _cache_lookup(self, market: dict):
        if not getattr(self.config, "CACHE_ENABLED", True):
            return None
        entry = self._cache.get(market.get("id"))
        if not entry:
            return None
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)
        if abs(yes_price - entry["yes_price"]) >= getattr(self.config, "CACHE_PRICE_MOVE", 0.03):
            return None
        if (time.time() - entry["ts"]) / 60 >= self._ttl_minutes(market):
            return None
        return entry

    @staticmethod
    def _looks_valid(raw: str) -> bool:
        lines = raw.strip().split("\n")
        return (any(l.startswith("MY_PROBABILITY:") for l in lines)
                and any(l.startswith("CONFIDENCE:") for l in lines))

    def _cache_store(self, market, raw, fields, live, poly_sig):
        """Remember Claude's answer — only if it was well-formed."""
        if not getattr(self.config, "CACHE_ENABLED", True) or not self._looks_valid(raw):
            return
        if len(self._cache) >= 500:
            oldest = min(self._cache, key=lambda k: self._cache[k]["ts"])
            del self._cache[oldest]
        self._cache[market["id"]] = {
            "ts":          time.time(),
            "yes_price":   market.get("outcomes", [{}])[0].get("price", 0.5),
            "fields":      dict(fields),
            "fp":          self._fingerprint(live),
            "poly_signal": poly_sig.get("signal", "NEUTRAL"),
        }

    def _decision_from_cache(self, entry: dict, market: dict) -> dict:
        """Re-apply today's price and thresholds to Claude's earlier probability."""
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)
        result = dict(entry["fields"])
        result["edge"] = result["my_probability"] - yes_price   # recomputed vs the current price
        decision = self._apply_rules(result, market)
        # _apply_rules only checks |edge|; a YES trade must have a positive edge today
        if (decision.get("should_trade") and decision.get("outcome_to_buy") == "Yes"
                and result["edge"] < self.config.MIN_EDGE_TO_TRADE):
            decision = {**decision, "should_trade": False,
                        "reason": f"YES edge {result['edge']:+.1%} at current price is below "
                                  f"{self.config.MIN_EDGE_TO_TRADE:.0%} minimum threshold"}
        decision["poly_signal"] = entry.get("poly_signal", "NEUTRAL")
        decision["cached"] = True
        self.cycle_hits += 1
        self.total_hits += 1
        return decision

    def pop_stats(self) -> dict:
        """Return this cycle's Claude usage and reset the per-cycle counters."""
        stats = {"calls": self.cycle_calls, "hits": self.cycle_hits,
                 "in_tokens": self.cycle_in, "out_tokens": self.cycle_out,
                 "total_calls": self.total_calls, "total_hits": self.total_hits}
        self.cycle_calls = self.cycle_hits = self.cycle_in = self.cycle_out = 0
        return stats

    # ─── HELPERS ─────────────────────────────────────────────────────────────

    @staticmethod
    def _keywords(question: str) -> str:
        stop = {
            "will", "the", "a", "an", "in", "of", "to", "be", "is", "are",
            "was", "were", "by", "for", "on", "at", "from", "with", "or",
            "and", "it", "its", "this", "that", "before", "after", "during",
            "most", "least", "win", "lose", "hit", "reach", "get", "above",
            "below", "close", "end", "than", "which", "who", "what", "when",
        }
        words    = question.replace("?", "").replace("–", " ").split()
        keywords = [w.strip(".,!") for w in words if w.lower() not in stop and len(w) > 3]
        return " ".join(keywords[:7])

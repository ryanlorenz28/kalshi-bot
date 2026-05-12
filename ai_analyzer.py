"""
ai_analyzer.py — Uses Claude AI to analyze Kalshi markets.

Features:
  - System prompt establishes Claude as a calibrated superforecaster
  - Chain-of-thought reasoning before final answer
  - Reddit + DuckDuckGo news sources
  - Category-specific specialist context
  - Safety rules blocking near-zero/near-100% markets
  - Today's date injected for timing accuracy
"""

import re
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional
import anthropic

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

    def analyze_market(self, market: dict) -> dict:
        """Full analysis pipeline for one market."""
        try:
            news     = self._fetch_news(market)
            prompt   = self._build_prompt(market, news)
            raw      = self._call_claude(prompt)
            decision = self._parse(raw, market)
            return decision
        except Exception as e:
            self.logger.error(f"Analysis error: {e}")
            return {"should_trade": False, "reason": str(e)}

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

    def _build_prompt(self, market: dict, news: List[Dict]) -> str:
        question  = market.get("question", "")
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)
        no_price  = round(1 - yes_price, 4)
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
{news_block}
{'─' * 55}
{specialist}
{'─' * 55}
STEP-BY-STEP REASONING REQUIRED:

  Step 1 — BASE RATE: How often do events like this actually happen historically?
  Step 2 — MARKET CHECK: Is the current price reasonable given the base rate?
  Step 3 — NEWS IMPACT: Does the news actually change the probability, or is it already priced in?
  Step 4 — RESOLUTION CHECK: How exactly does this Kalshi market resolve?
  Step 5 — EDGE CHECK: Is my estimate different from the market by at least 6%? Why is the crowd wrong?
  Step 6 — CONFIDENCE CHECK: Am I genuinely confident (60%+) or just guessing?

{'─' * 55}
Respond in EXACTLY this format with no extra text:

TRADE: YES or NO or SKIP
CONFIDENCE: 0.0 to 1.0
MY_PROBABILITY: 0.0 to 1.0
EDGE: your probability minus market price as decimal
REASONING: one paragraph explaining your probability estimate and why the crowd may be wrong
KEY_RISKS: one sentence on the biggest risk to this trade

Only recommend YES or NO if you have at least 6% edge AND 60%+ confidence AND a specific reason the crowd is wrong."""

        return prompt

    def _specialist_context(self, market: dict) -> str:
        question = market.get("question", "").lower()

        if any(x in question for x in ["fed", "federal reserve", "interest rate", "fomc"]):
            return """SPECIALIST CONTEXT — Federal Reserve:
  - CME FedWatch Tool is the gold standard — market price likely already reflects it
  - Fed decisions are telegraphed weeks ahead through speeches and minutes
  - Base rate: Fed follows through on heavily priced-in moves ~90% of the time
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
  - Check current price vs target — if far away with little time, crowd is right"""

        elif any(x in question for x in ["inflation", "cpi", "gdp", "unemployment", "jobs"]):
            return """SPECIALIST CONTEXT — Economic Data:
  - Bloomberg consensus forecasts are already baked into market prices
  - Base rate: actual data comes within 0.2% of consensus ~65% of the time
  - Best edge: when recent trend strongly diverges from consensus expectation"""

        elif any(x in question for x in ["election", "president", "senate", "congress"]):
            return """SPECIALIST CONTEXT — Politics:
  - Polling averages beat individual polls — RealClearPolitics is the benchmark
  - Base rate: candidates leading by 5%+ in polls win ~80% of the time"""

        elif any(x in question for x in ["tesla", "nvidia", "apple", "earnings", "production", "deliveries"]):
            return """SPECIALIST CONTEXT — Companies:
  - Analyst consensus estimates are already priced in
  - Look for recent supply chain news, guidance revisions, or macro headwinds
  - Delivery/production numbers: compare to prior quarter trends and guidance"""

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
        return message.content[0].text

    # ─── RESPONSE PARSING ────────────────────────────────────────────────────

    def _parse(self, text: str, market: dict) -> dict:
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

        # ── SAFETY RULES ──────────────────────────────────────────────────────
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)

        if yes_price < 0.04 or yes_price > 0.96:
            return {**result,
                    "should_trade": False,
                    "reason": f"Market price {yes_price:.1%} is near limit — crowd is almost certainly correct"}

        if abs(result["edge"]) < 0.06:
            return {**result,
                    "should_trade": False,
                    "reason": f"Edge {abs(result['edge']):.1%} is below 6% minimum threshold"}

        if result["confidence"] < self.config.MIN_CONFIDENCE_TO_TRADE:
            return {**result,
                    "should_trade": False,
                    "reason": f"Confidence {result['confidence']:.0%} below threshold {self.config.MIN_CONFIDENCE_TO_TRADE:.0%}"}

        if result["outcome_to_buy"]:
            result["should_trade"] = True
            result["reason"]       = "opportunity found"
        else:
            result["reason"] = "Claude recommended SKIP"

        return result

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

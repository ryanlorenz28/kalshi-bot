"""
ai_analyzer.py — Uses Claude AI to analyze Kalshi markets.

Upgrades over v1:
  - System prompt establishes Claude as a calibrated superforecaster
  - Chain-of-thought reasoning before final answer
  - NewsAPI + Reddit news sources
  - Category-specific specialist context
  - Safety rules blocking near-zero/near-100% markets
  - Today's date injected for timing accuracy
  - Google Trends spike detection
"""

import re
import json
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
        """Fetch from NewsAPI if available, otherwise Reddit + DuckDuckGo."""
        keywords = self._keywords(market.get("question", ""))
        results  = []

        if self.config.NEWS_API_KEY:
            results += self._newsapi(keywords)

        results += self._reddit(keywords, market)

        if not results:
            results += self._duckduckgo(keywords)

        # Check Google Trends for spike
        trend = self._trends_spike(keywords)
        if trend:
            results.insert(0, trend)

        return results[:self.config.NEWS_ARTICLES_TO_FETCH + 2]

    def _newsapi(self, keywords: str) -> List[Dict]:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q":        keywords,
                    "sortBy":   "publishedAt",
                    "pageSize": 5,
                    "language": "en",
                    "apiKey":   self.config.NEWS_API_KEY,
                },
                timeout=10,
            )
            resp.raise_for_status()
            articles = resp.json().get("articles", [])
            return [
                {
                    "source":  f"📰 {a.get('source', {}).get('name', 'News')}",
                    "date":    a.get("publishedAt", "")[:10],
                    "title":   a.get("title", ""),
                    "summary": (a.get("description") or "")[:200],
                }
                for a in articles
                if a.get("title") and "[Removed]" not in str(a.get("title"))
            ]
        except Exception:
            return []

    def _reddit(self, keywords: str, market: dict) -> List[Dict]:
        """Fetch relevant Reddit posts — no API key needed."""
        category = market.get("category", "").lower()
        question = market.get("question", "").lower()

        # Pick best subreddit for this market type
        if any(x in question for x in ["fed", "rate", "inflation", "gdp", "unemployment"]):
            subreddit = "economics"
        elif any(x in question for x in ["bitcoin", "crypto", "btc", "eth"]):
            subreddit = "CryptoCurrency"
        elif any(x in question for x in ["nba", "nfl", "mlb", "nhl", "soccer"]):
            subreddit = "sports"
        elif any(x in question for x in ["election", "president", "congress", "senate"]):
            subreddit = "politics"
        elif any(x in question for x in ["stock", "market", "s&p", "nasdaq", "dow"]):
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
                        "source":  f"🟠 Reddit r/{subreddit} ({score:,} upvotes)",
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
                    "source":  f"📖 {data.get('AbstractSource', 'Wikipedia')}",
                    "date":    datetime.now().strftime("%Y-%m-%d"),
                    "title":   data.get("Heading", keywords),
                    "summary": abstract[:250],
                })
            for topic in data.get("RelatedTopics", [])[:3]:
                text = topic.get("Text", "")
                if text and len(text) > 20:
                    results.append({
                        "source":  "🔍 DuckDuckGo",
                        "date":    datetime.now().strftime("%Y-%m-%d"),
                        "title":   text[:150],
                        "summary": "",
                    })
            return results
        except Exception:
            return []

    def _trends_spike(self, keywords: str) -> Optional[Dict]:
        """Check if topic is trending on Google today."""
        try:
            resp = requests.get(
                "https://trends.google.com/trends/trendingsearches/daily/rss",
                params={"geo": "US"},
                timeout=8,
            )
            if resp.status_code != 200:
                return None
            content  = resp.text.lower()
            kw_words = [w for w in keywords.lower().split() if len(w) > 4]
            matches  = [w for w in kw_words if w in content]
            if len(matches) >= 2:
                return {
                    "source":  "📈 Google Trends",
                    "date":    datetime.now().strftime("%Y-%m-%d"),
                    "title":   f"🔥 TRENDING NOW: '{keywords}' is spiking on Google Search today",
                    "summary": "High search interest may indicate breaking news not yet priced into the market.",
                }
        except Exception:
            pass
        return None

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

        # Market sentiment line
        if yes_price < 0.05:
            sentiment = "⚠️  Market considers this VERY UNLIKELY (<5%) — crowd is almost certainly right"
        elif yes_price > 0.95:
            sentiment = "⚠️  Market considers this NEAR CERTAIN (>95%) — crowd is almost certainly right"
        elif yes_price > 0.70:
            sentiment = "Market leans YES strongly"
        elif yes_price < 0.30:
            sentiment = "Market leans NO strongly"
        else:
            sentiment = "Market is genuinely uncertain"

        # News block
        if news:
            news_block = f"RECENT NEWS (as of {self.today}):\n"
            for i, a in enumerate(news, 1):
                news_block += f"  {i}. [{a['source']} | {a['date']}] {a['title']}\n"
                if a.get("summary"):
                    news_block += f"     → {a['summary']}\n"
        else:
            news_block = "RECENT NEWS: No articles found. Rely on base rates and market data.\n"

        # Category specialist context
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
STEP-BY-STEP REASONING REQUIRED — think through these before answering:

  Step 1 — BASE RATE: How often do events like this actually happen historically?
  Step 2 — MARKET CHECK: Is the current price reasonable given the base rate?
  Step 3 — NEWS IMPACT: Does the news actually change the probability, or is it already priced in?
  Step 4 — RESOLUTION CHECK: How exactly does this Kalshi market resolve? Any technicalities?
  Step 5 — EDGE CHECK: Is my estimate different from the market by at least 8%? Why is the crowd wrong?
  Step 6 — CONFIDENCE CHECK: Am I genuinely confident or just guessing?

{'─' * 55}
Respond in EXACTLY this format with no extra text:

TRADE: YES or NO or SKIP
CONFIDENCE: 0.0 to 1.0
MY_PROBABILITY: 0.0 to 1.0
EDGE: your probability minus market price as decimal
REASONING: one paragraph explaining your probability estimate and why the crowd may be wrong
KEY_RISKS: one sentence on the biggest risk to this trade

Only recommend YES or NO if you have at least 8% edge AND 65%+ confidence AND a specific reason the crowd is wrong."""

        return prompt

    def _specialist_context(self, market: dict) -> str:
        """Inject domain-specific knowledge based on market type."""
        question = market.get("question", "").lower()
        category = market.get("category", "").lower()

        if any(x in question for x in ["fed", "federal reserve", "interest rate", "fomc", "bps"]):
            return """SPECIALIST CONTEXT — Federal Reserve:
  • CME FedWatch Tool is the gold standard — market price likely already reflects it
  • Fed decisions are telegraphed weeks ahead through speeches and minutes
  • Base rate: Fed follows through on heavily priced-in moves ~90% of the time
  • Only trade if you have specific speech/data that contradicts current pricing
  • If priced above 85% or below 15%, it is almost certainly correct"""

        elif any(x in question for x in ["s&p", "nasdaq", "dow", "stock", "market close"]):
            return """SPECIALIST CONTEXT — Financial Markets:
  • Same-day price target markets are very hard to beat — prices move fast
  • Base rate: markets close up ~53% of days historically
  • Key drivers: pre-market futures, overnight news, macro data releases
  • If market is already open, current price gives strong signal
  • These markets have very tight edges — be very selective"""

        elif any(x in question for x in ["bitcoin", "btc", "crypto", "ethereum", "eth"]):
            return """SPECIALIST CONTEXT — Crypto:
  • Crypto price targets are highly volatile and hard to predict short-term
  • Base rate: specific price targets fail more often than not
  • Key drivers: macro sentiment, ETF flows, whale movements, regulatory news
  • Check current price vs target — if far away with little time, crowd is right"""

        elif any(x in question for x in ["inflation", "cpi", "gdp", "unemployment", "jobs", "payroll"]):
            return """SPECIALIST CONTEXT — Economic Data:
  • Bloomberg consensus forecasts are already baked into market prices
  • Base rate: actual data comes within 0.2% of consensus ~65% of the time
  • Best edge: when recent trend strongly diverges from consensus expectation
  • Watch for: recent monthly trends, seasonal adjustments, revision patterns"""

        elif any(x in question for x in ["election", "president", "senate", "congress", "governor"]):
            return """SPECIALIST CONTEXT — Politics/Elections:
  • Polling averages beat individual polls — RealClearPolitics is the benchmark
  • Base rate: candidates leading by 5%+ in polls win ~80% of the time
  • Watch for: early voting data, fundraising numbers, late-breaking news
  • Within 3 points = genuine toss-up, treat as 50/50"""

        elif any(x in question for x in ["nba", "nfl", "mlb", "nhl", "world cup", "champion"]):
            return """SPECIALIST CONTEXT — Sports:
  • Sports markets are efficient — injury news is the main source of edge
  • Base rate: favorites cover spread ~52% of the time
  • Check: any key player injuries, home/away, recent form (last 5 games)
  • Season-long markets: consider current standings and schedule strength"""

        else:
            return """SPECIALIST CONTEXT — General:
  • With no specialist data available, weight the market price heavily
  • Only trade if you have a specific, articulable reason the crowd is wrong
  • Default to SKIP unless confidence is very high"""

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
        """Parse Claude's response into a structured trading decision."""
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
        result["confidence"]      = max(0.0, min(1.0, result["confidence"]))
        result["my_probability"]  = max(0.0, min(1.0, result["my_probability"]))

        # ── SAFETY RULES ──────────────────────────────────────────────────────
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)

        # Block near-certain / near-impossible markets
        if yes_price < 0.04 or yes_price > 0.96:
            return {**result,
                    "should_trade": False,
                    "reason": f"Market price {yes_price:.1%} is near limit — crowd is almost certainly correct"}

        # Block insufficient edge
        if abs(result["edge"]) < 0.08:
            return {**result,
                    "should_trade": False,
                    "reason": f"Edge {abs(result['edge']):.1%} is below 8% minimum threshold"}

        # Block low confidence
        if result["confidence"] < self.config.MIN_CONFIDENCE_TO_TRADE:
            return {**result,
                    "should_trade": False,
                    "reason": f"Confidence {result['confidence']:.0%} below threshold {self.config.MIN_CONFIDENCE_TO_TRADE:.0%}"}

        # All checks passed — trade!
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

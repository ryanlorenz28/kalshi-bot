"""
ai_analyzer.py — Uses Claude AI to analyze Kalshi markets.
"""

import anthropic


class AIAnalyzer:

    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def analyze_market(self, market: dict) -> dict:
        """Send market to Claude and get a trading recommendation."""
        question  = market.get("question", "")
        yes_price = market.get("outcomes", [{}])[0].get("price", 0.5)
        no_price  = 1 - yes_price
        category  = market.get("category", "General")
        days_left = market.get("days_to_resolve", 1)

        prompt = f"""You are an expert prediction market trader analyzing a Kalshi market.

Market: {question}
Category: {category}
Days until resolution: {days_left}
Current YES price: {yes_price:.2%} (implied probability)
Current NO price: {no_price:.2%} (implied probability)

Analyze this market carefully:
1. What is the true probability based on your knowledge?
2. Is there meaningful mispricing (at least 8% edge)?
3. Which side has better value — YES or NO?

Respond in EXACTLY this format:
TRADE: YES or NO or SKIP
CONFIDENCE: 0.0 to 1.0
MY_PROBABILITY: 0.0 to 1.0
EDGE: difference between your probability and market price as decimal
REASONING: one paragraph explanation
KEY_RISKS: one sentence on biggest risk

Only recommend YES or NO if you have at least 8% edge and 65%+ confidence."""

        try:
            message = self.client.messages.create(
                model=self.config.CLAUDE_MODEL,
                max_tokens=self.config.MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )
            return self._parse(message.content[0].text, market)
        except Exception as e:
            self.logger.error(f"Analysis error: {e}")
            return {"should_trade": False, "reason": str(e)}

    def _parse(self, text: str, market: dict) -> dict:
        """Parse Claude's response into a structured result."""
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

        if (result["outcome_to_buy"] and
                result["confidence"] >= self.config.MIN_CONFIDENCE_TO_TRADE and
                abs(result["edge"]) >= 0.08):
            result["should_trade"] = True
            result["reason"]       = "opportunity found"
        else:
            result["reason"] = f"Edge {abs(result['edge']):.1%} or confidence {result['confidence']:.0%} too low"

        return result
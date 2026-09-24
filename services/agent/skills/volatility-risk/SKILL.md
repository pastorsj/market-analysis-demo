---
name: volatility-risk
description: Estimate how volatile a stock is likely to be over the next five sessions after a move, using a model trained before the cutoff. Use for risk, volatility, or "what happens next" questions.
allowed-tools: detect_market_shock predict_volatility_risk search_news
---

# Volatility risk

1. Call `detect_market_shock` to describe the triggering session.
2. Call `predict_volatility_risk`. Report the estimate, its horizon, its inputs, and
   the model's training cutoff.
3. Add `search_news` only if the user asks about risk factors in the sources.

Keep in mind:
- The estimate is expected volatility, not price direction or a probability of loss.
  Do not turn it into a forecast or advice.
- If the model was trained after the cutoff it is unavailable; say so.

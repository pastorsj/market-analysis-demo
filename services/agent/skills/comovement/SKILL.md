---
name: comovement
description: Show which related stocks and ETFs usually move with this company and how they moved on the date, to judge whether a shock was company-specific or shared. Use for contagion, spillover, exposure, or "did it spread" questions.
allowed-tools: get_price_context detect_market_shock map_comovement search_news
---

# Co-movement

1. Call `detect_market_shock` for the primary ticker.
2. Call `map_comovement`. It links instruments whose daily returns were correlated
   before the session and reports each one's move on the session.
3. Call `search_news` only if the user asks what sources said about spillover.

Interpreting:
- Correlation shows shared exposure, not that one company's shock caused another's.
  Say "moved with" or "moved against", not "spread to".
- A linked instrument that moved sharply the same way suggests a shared driver; one
  that did not move suggests a company-specific shock. Both are inferences.
- Distinguish one-link neighbours from two-link ones, and say if nothing was linked.

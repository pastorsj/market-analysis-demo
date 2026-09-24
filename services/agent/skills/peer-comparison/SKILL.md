---
name: peer-comparison
description: Compare two to five companies on the same date - relative returns, benchmark-adjusted moves, and volume - and separate shared moves from company-specific ones.
allowed-tools: get_price_context detect_market_shock search_news
---

# Peer comparison

1. Call `get_price_context` and `detect_market_shock` for every company in scope.
2. Call `search_news` for a company only if the question asks about causes or sources.
3. Compare like with like: the same session, the same metric, and each company's own
   sector benchmark.

When ranking:
- State the metric. For "most abnormal", rank by the absolute benchmark-relative
  return in percentage points; rank volume ratios separately.
- A negative benchmark-relative return is underperformance even if the stock rose.
- Check that every "largest" or "smallest" claim matches the numbers.

Interpreting:
- Moving together is not evidence of a shared cause, and moving apart does not prove a
  company-specific one. Say what the evidence supports and what it leaves open.
- Name any company with missing data instead of leaving it out.

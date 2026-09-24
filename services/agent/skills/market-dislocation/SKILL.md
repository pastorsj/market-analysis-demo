---
name: market-dislocation
description: Explain what happened to one stock on a date - how large and unusual the move was and what sources say about why. Use for "what happened", "why did it move", "how unusual was it", or a single price or volume fact.
allowed-tools: get_price_context detect_market_shock search_news
---

# Market dislocation

1. Call `get_price_context` and `detect_market_shock` for the primary ticker.
2. If the question asks why, what drove the move, or what sources said, call
   `search_news` once with a query describing the event. Use `lookback_days=1`
   when only same-day sources matter.
3. Answer the question that was asked. A price or volume fact needs no causal story.

Reading the numbers:
- `return_1d_pct` is prior close to close, `opening_gap_pct` is prior close to open,
  and `open_to_close_pct` is the intraday move. Use the one that matches the question.
- Benchmark-relative moves are in percentage points, not percent.
- `is_shock` is a size threshold, not a cause or a sign of a sector-wide event.

Explaining the move:
- Attribute reasons to the source that states them. A same-day story is context, not
  proof of cause; a filing's metadata shows it exists, not what it says.
- If no source explains the move, say so and name what evidence would settle it.

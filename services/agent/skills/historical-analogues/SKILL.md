---
name: historical-analogues
description: Find earlier sessions that looked like this one (similar return, benchmark-relative move, and volume) and explain where the comparison holds and where it breaks down.
allowed-tools: get_price_context detect_market_shock find_historical_analogues
---

# Historical analogues

1. Call `detect_market_shock` for the target to describe the session itself.
2. Call `find_historical_analogues`. Use `scope="all_targets"` only if the user wants
   other companies' episodes too.
3. For each analogue, say what matched (direction, size, relative move, volume) and
   what differed, using the returned numbers.

Keep in mind:
- Similar measurements do not mean a similar cause, regime, or outcome. Do not claim
  what happened after an analogue; the tools do not return later prices.
- The method section of the result explains the distance measure; describe it
  plainly if asked instead of guessing.
- If fewer candidates came back than requested, say so.

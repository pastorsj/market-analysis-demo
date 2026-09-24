---
name: narrative-map
description: Map the themes in documents available about a company before the cutoff and note agreements, conflicts, and gaps. Use for topics, themes, narratives, or "what were people saying".
allowed-tools: search_news project_news_topics get_price_context
---

# Narrative map

1. Call `project_news_topics` to see the newest documents and how they group.
2. Call `search_news` with a focused query for the themes the user asks about.
3. Add `get_price_context` only if the user asks how the narrative lined up with the
   price move.

Keep in mind:
- Points close together share wording; that does not make them true, independent, or
  important.
- Most filings are metadata only. Say when a theme rests on titles rather than content.
- Point out conflicting accounts and missing source types.

---
name: research-guide
description: Help when the question is not a research question yet - greetings, questions about the app, requests with no company or date, or requests outside its scope (advice, trading, other topics).
allowed-tools: ""
---

# Research guide

Do not call evidence tools. Instead:

1. Answer briefly and honestly using the "About this application" facts in your
   instructions. Do not invent companies, dates, data sources, or features.
2. If a company or date is missing, say which one, and why the tools need it.
3. If the request is out of scope (investment advice, trades, live prices, other
   topics), say what the app does instead.
4. Put up to three concrete questions in `suggested_questions`. Each names a supported
   company and one date inside the data coverage.

When asked what leaves the machine: the local model and tools run on the DGX Spark,
but routing sends the conversation and tool evidence to the remote judge and, when
escalated, the remote capable model. Traces may be exported to LangSmith if configured.

# Demo walkthrough

A five-minute presenter script. The app has three views: **Dashboard**,
**Research**, and **Built Together**. Before visitors arrive, confirm that
`./demo start` printed `READY FOR DEMO` and that `./demo doctor` passes. If it
printed `PREPARED, RESEARCH DISABLED`, you can show the Dashboard and Built
Together views, but you cannot run live research.

## 1. Dashboard (about 1 minute)

Open <http://localhost:3000>.

- Walk through the watchlist of the supported companies and the selected
  company's historical price chart against its benchmark.
- Show "Comparable moves", which ranks sessions by benchmark-adjusted move, and
  "Company documents", which lists only documents available by the cutoff.
- Say it plainly: this is a prepared historical snapshot, not live market data.
  The prices are a later reconstruction of daily bars.

## 2. Research (about 3 minutes)

Open **Research** and pick a curated event, for example the September 16, 2026
Federal Reserve rate increase or NVIDIA's fiscal 2027 second-quarter repricing.
**Set up scenario** fills in the companies, market session, and evidence cutoff.
It does not start a run. Choose a suggested question or type one, then press
**Investigate**.

While it runs, point out:

1. **The agent picked the skill.** The turn shows "Skill chosen by the agent".
   The Deep Agent saw every skill's description and read the one that fits. The
   application does not route questions to skills.
2. **Every step is routed.** Local Nemotron 3.5 Lightning produced each step. A
   remote judge decided whether Nemotron 3 Ultra should redo it. The timeline
   shows each "Agent reasoning" and "Routing judge" call with its model and
   latency.
3. **The tools are GPU work.** Tool spans show the ticker, the outcome, and the
   number of citations. Their receipts name the engine used: cuDF, cuVS,
   cuGraph, cuML, or XGBoost.
4. **The answer is grounded.** Citations are only sources the tools returned
   for this turn, and none is dated after the cutoff. Uncertainty and tool
   limitations are listed, not hidden.

Then ask a follow-up in **Continue this investigation**. For example, "How
unusual was that move compared with history?" or "Which related stocks moved
with it?". The scope carries forward, and an investigation holds up to four
questions. To show the guardrails, ask something out of scope, such as a trade
recommendation. The research guide skill explains what the agent can answer
instead.

If a turn fails, show the error message and use **Retry**. Do not re-run it
until it happens to succeed and present that as the first result.

## 3. Built Together (about 1 minute)

Open **Built Together** and walk through the four stages:

1. **A Deep Agent chooses how to research.** LangChain Deep Agents, skills, and
   a typed answer.
2. **Every step is routed on purpose.** NeMo Switchyard, the local Lightning
   model, the remote judge, and Nemotron 3 Ultra.
3. **GPU tools measure the evidence.** MCP tools on RAPIDS and XGBoost, bounded
   by the cutoff.
4. **The agent runs sandboxed and traced.** OpenShell containment, NeMo Relay
   traces, and optional LangSmith export.

The **GitHub** link in the header opens this repository. Useful files to show
are `services/agent/src/market_agent/agent.py` (the Deep Agent),
`services/agent/skills/` (the skills), `services/agent/src/market_agent/routing.py`
(Switchyard), `services/tools/src/market_tools/` (the tools), and
`scripts/spark/openshell/policy.yaml` (the sandbox policy).

## What to say and not say

- Research needs the remote endpoint, because the judge reviews every step.
  Startup is offline; investigations are not. Do not call the demo "fully
  local".
- Remote calls send the conversation and tool evidence to the configured
  endpoint.
- The data is small: five target companies plus one peer, about 10.6k daily
  bars, and 494 documents, mostly SEC filing metadata.
- A cutoff controls which evidence the agent can use. It does not turn
  reconstructed data into a historical archive.
- Answers are historical research, not investment advice.

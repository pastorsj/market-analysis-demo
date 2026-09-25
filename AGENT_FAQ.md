# Market Shock Research Agent — FAQ

## What does the agent do?

It answers point-in-time questions about how a stock moved around a historical
date. It covers how unusual the move was, what sources available at the time
said, how it compared with peers and benchmarks, which earlier sessions looked
similar, and which instruments usually move with it. It is a research demo, not
a trading system, and nothing it says is investment advice.

## How does an investigation work?

1. **Scope.** The app works out the companies (up to five) and the evidence
   cutoff. It uses a curated event, the UI's ticker and date controls, or
   tickers, company names, and dates written in the question. If the company or
   date is missing, the agent asks for it and does not call evidence tools.
2. **Skill.** The model reads the list of skill descriptions and opens the
   `SKILL.md` that best fits the question. Python code does not pick the skill.
3. **Evidence.** The model calls evidence tools. Each tool is locked to the
   investigation's companies and cutoff.
4. **Routing.** NeMo Switchyard reviews every model step (see below).
5. **Answer.** The agent returns a structured answer: Markdown text, the
   citation IDs that support it, uncertainties, and suggested questions when it
   cannot answer. The UI shows a live timeline of skill, model, and tool spans.

One investigation can hold up to four questions. A follow-up keeps the scope,
and it can add a newly named company or a new date.

## What is the difference between a skill and a tool?

A **skill** is a short Markdown playbook in `services/agent/skills/<name>/SKILL.md`.
It tells the model which tools to call, in what order, and how to read the
results without overclaiming. Skills are *offered*, not forced. Deep Agents
lists every skill's description, and the model decides which one to read. A
skill's `allowed-tools` line is guidance. The code does not enforce it, and all
seven tools are available on every turn.

A **tool** is a read-only MCP operation on the `tools` service. It computes or
retrieves evidence from the prepared data. It returns an outcome, data,
citations, coverage, limitations, and a GPU execution receipt.

## What skills does it have?

| Skill | Used for | Key boundary |
| --- | --- | --- |
| `market-dislocation` | "What happened?", "Why did it move?", a single price or volume fact | Same-day sources are context, not proof of cause. `is_shock` is a size threshold. |
| `peer-comparison` | Two to five companies on the same date | Uses the same session and metric for every company. Moving together does not show a shared cause. |
| `historical-analogues` | Earlier sessions that looked like this one | Similar numbers do not mean a similar cause or outcome. It never says what happened after an analogue. |
| `comovement` | Contagion, spillover, exposure, "did it spread?" | Correlation shows shared exposure. The agent says "moved with", not "spread to". |
| `volatility-risk` | Risk or "what happens next" questions | Gives expected volatility, not a price direction, a probability of loss, or advice. |
| `narrative-map` | Themes, topics, "what were people saying" | Points that sit close together share wording. That does not make them true or important. |
| `research-guide` | Greetings, app questions, missing company or date, out-of-scope requests | Calls no evidence tools. It suggests up to three concrete questions the app can answer. |

## What tools can the agent call?

The agent sees seven tools (`services/agent/src/market_agent/tools.py`). The
model chooses only the ticker and a few arguments. The wrapper adds the cutoff,
rejects tickers outside the investigation, and blocks repeat calls. Each
question allows 16 distinct calls and 3 calls per tool per ticker. The wrapper
also rejects any result that contains evidence dated after the cutoff.

| Tool | What it returns | GPU work |
| --- | --- | --- |
| `get_price_context` | Price, 1- and 5-day returns, opening gap, intraday return, volume ratio, sector benchmarks | cuDF |
| `detect_market_shock` | Return versus benchmark, volume ratio, and `is_shock` (a benchmark-relative move of at least 5 pp or at least 2x normal volume) | cuDF |
| `search_news` | Documents available before the cutoff, ranked by similarity to a query | Nemotron 3 Embed + cuVS |
| `find_historical_analogues` | Earlier sessions closest by signed return, benchmark-relative return, and volume ratio, for the same ticker or all targets | cuDF / CuPy |
| `map_comovement` | Instruments whose prior daily returns were correlated with the ticker (up to two links), and how each moved on the session | cuGraph BFS |
| `predict_volatility_risk` | Annualized volatility expected over the next five sessions, from a model trained before the cutoff | XGBoost (GPU) |
| `project_news_topics` | A 2-D map of the newest available documents, grouped by embedding similarity | cuML UMAP |

The dashboard also reads one MCP resource (`market://dashboard/{ticker}/{as_of}`)
to draw its charts. That resource is not agent evidence.

## How are answers checked?

- The final answer is a typed structure, not free text.
- The answer must cite evidence that the tools returned during the current
  question. The app drops citation IDs that the tools never returned. If the
  tools returned evidence and the answer cites none of it, the question fails
  with `uncited_answer`.
- Tool limitations, warnings, and failures are shown with the report. A tool
  failure is shown to the user, not hidden.

## How does Switchyard routing work?

- **Nemotron 3.5 Lightning** runs locally on the DGX Spark and proposes each
  agent step.
- A remote **judge**, `openai/openai/gpt-5.6-luna`, checks that step against the
  question and the tool evidence. It decides whether the step should be redone.
- If the judge escalates, the remote **Nemotron 3 Ultra**
  (`nvidia/nvidia/nemotron-3-ultra`) redoes the step.
- Escalation is scoped to the current question and does not carry over to the
  next one.
- If the judge's verdict cannot be read, the step fails
  (`judge_verdict_invalid`). The app does not quietly keep the local answer.
- The app checks every response for model identity. A mismatch fails the step,
  and there is no hidden fallback model.

## Does anything leave the machine?

Yes. Research requires remote routing because the judge reviews every step. The
conversation and the tool evidence are therefore sent to the remote endpoint.
With `REMOTE_ROUTING_ENABLED=false`, the agent does not build, investigations
return 503, and `/api/status` explains why. The dashboard and the event catalog
still work. The local model, embeddings, GPU tools, data, and state stay on the
Spark. Traces go to LangSmith only if export is configured.

## How is it traced?

NeMo Relay records one span per agent step and one span per physical model call
(local, judge, or Ultra). Traces are written to `/srv/market-shock/traces`. When
LangSmith tracing is enabled, Relay exports the same trace hierarchy there. The
app records each model call's model, latency, and token counts.

## Where does the agent run?

The agent runs inside an NVIDIA OpenShell 0.0.116 sandbox, and there is no
unsandboxed fallback. The policy is in `scripts/spark/openshell/policy.yaml`.
The API keys are stored in endpoint-bound OpenShell providers. Only the
sandboxed agent receives them. They are never passed to the web, tools, or
model services.

## What data does it use?

The dataset is small and prepared in advance:

- Five target companies (NVDA, AMD, JPM, GS, SCHW) plus AVGO as a peer. That is
  about 10.6k daily bars. The prices are a **later reconstruction** from a CC0
  Kaggle dataset, not an archive captured on each date.
- 494 documents. Most are SEC filing metadata (a filing's existence and date,
  not its contents), plus a few company releases and 12 curated one-sentence
  news summaries.
- 12 curated shock events in `data/events/catalog.yaml`.

The GPU work is real but small in scale. The agent does not browse the web or
use live news.

## What does the point-in-time cutoff guarantee?

Every tool call runs as of the investigation's cutoff. Documents must have been
*available* by then. The volatility model reports no data if it was trained
after the cutoff. The agent wrapper rejects any result with evidence dated
after the cutoff. The cutoff controls what the agent can see. It cannot make
reconstructed prices historical.

## What will it not do?

It will not recommend trades, give investment advice, quote live prices, or
claim that a same-day story or a correlation caused a move. When evidence is
missing, it says so and names the evidence that would settle the question.

# Demo walkthrough

The story is a personal market research workstation that combines LangChain's
agent framework with NVIDIA's local inference, GPU data processing, governed
routing, and containment.

## 1. Begin on the dashboard

Open <http://localhost:3000>. Point out the watchlist, historical market context,
and company evidence panel. The optional intraday widget makes the dashboard
feel current, but it is display-only and never becomes agent evidence.

The important distinction is visible in the UI: the dashboard can show what is
happening now, while Research uses the prepared, cutoff-qualified historical
snapshot.

## 2. Set up a recent scenario

Open **Research** and select one of the first two recent events:

- the September 16, 2026 Federal Reserve rate increase and financial-sector
  repricing; or
- NVIDIA's August 26–27, 2026 fiscal 2027 second-quarter repricing.

Selecting **Set up scenario** fills the supported companies, market window, and
evidence cutoff. Choose one of the suggested questions or edit the question in
the composer, then press **Investigate**. Scenario selection prepares the form;
it does not silently start a model run.

## 3. Follow the investigation

As the answer develops, open **Research details**. The timeline shows policy,
model, and tool spans on one Gantt view. Hover a span to see its timing and role.

Call out three behaviors:

1. The Deep Agent reads a skill and chooses its own approved evidence tools.
2. Switchyard evaluates each logical call and keeps work local when Lightning is
   sufficient, escalating to Ultra only when the configured route selects it.
3. The final answer carries sources and uncertainty instead of hiding evidence
   gaps.

Use a follow-up question to show that LangGraph-backed state preserves the event
scope and earlier turn without turning the application into a general-purpose
assistant.

## 4. Explain what is built together

Open **Built Together**. Walk left to right through the four stages:

1. LangChain Deep Agents and LangGraph organize the investigation.
2. DGX Spark and Nemotron 3.5 Lightning produce the efficient local result;
   Switchyard governs escalation to Nemotron 3 Ultra.
3. RAPIDS, CUDA-X, Nemotron 3 Embed, and OpenShell provide evidence work and a
   constrained runtime.
4. NeMo Relay and LangSmith make the run observable so the team can improve the
   next version.

The central partnership point is simple: faster local work improves the user
experience, and observable runs shorten the team's iteration cycle.

## 5. Open the source

The **GitHub** link in the top-right header opens this repository in a new tab.
Use it to show the Deep Agent initialization, versioned skills, Switchyard
middleware, OpenShell policy, typed MCP tools, and the React experience without
exposing credentials or the private qualification corpus.

## Presenter boundaries

- Describe answers as historical research, not investment advice.
- Do not claim that reconstructed data is an archive captured on the original
  date.
- Do not describe the live widget as agent evidence.
- Do not claim a fully offline investigation; the approved routing endpoint is
  required.
- If a live run fails, use the human-readable recovery guidance and preserve the
  trace rather than swapping models or inventing an answer.

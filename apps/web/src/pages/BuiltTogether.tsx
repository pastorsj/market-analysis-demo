import type { SystemStatus } from "../api/types";
import "./built-together.css";

const nvidiaLogo = new URL("../assets/partners/nvidia_logo.png", import.meta.url).href;
const langchainLogo = new URL("../assets/partners/langchain_logo.png", import.meta.url).href;
const deepagentsLogo = new URL("../assets/partners/deepagents_logo.png", import.meta.url).href;
const langgraphLogo = new URL("../assets/partners/langgraph_logo.png", import.meta.url).href;

type Owner = "nvidia" | "langchain";
interface Mark { readonly name: string; readonly owner: Owner; readonly logo?: string; readonly qualifier?: string }
interface Stage { readonly title: string; readonly body: string; readonly marks: readonly Mark[]; readonly files: readonly string[] }

const STAGES: readonly Stage[] = [
  {
    title: "A Deep Agent chooses how to research.",
    body: "The agent is built with LangChain Deep Agents. It sees a short description of every research skill and reads the one that fits the question — the agent chooses the skill, not the application. It then calls the evidence tools and returns a typed, structured report. LangGraph checkpoints keep each investigation's state so follow-up questions build on earlier turns.",
    marks: [
      { name: "Deep Agents", owner: "langchain", logo: deepagentsLogo },
      { name: "LangGraph", owner: "langchain", logo: langgraphLogo },
    ],
    files: ["services/agent/src/market_agent/agent.py", "services/agent/skills/"],
  },
  {
    title: "Every step is routed on purpose.",
    body: "Nemotron runs the agent locally on DGX Spark. Before each reasoning step, NeMo Switchyard asks a remote judge model whether the local model is enough; when the judge asks for more capability, that step escalates to Nemotron 3 Ultra. Each call's model, tier, and latency is shown with the answer.",
    marks: [
      { name: "NeMo Switchyard", owner: "nvidia" },
      { name: "Nemotron", owner: "nvidia", qualifier: "Local" },
      { name: "Nemotron 3 Ultra", owner: "nvidia", qualifier: "Escalation" },
    ],
    files: ["services/agent/src/market_agent/routing.py"],
  },
  {
    title: "GPU tools measure the evidence.",
    body: "Tools served over MCP compute returns, analogues, co-movement networks, and document maps with RAPIDS cuDF, cuVS, cuGraph, and cuML, plus XGBoost on the GPU. Every result carries a receipt with the engine, device, and duration, and only uses data available at the evidence cutoff.",
    marks: [
      { name: "RAPIDS", owner: "nvidia", qualifier: "cuDF · cuVS · cuGraph · cuML" },
      { name: "XGBoost", owner: "nvidia", qualifier: "GPU" },
      { name: "DGX Spark", owner: "nvidia" },
    ],
    files: ["services/agent/src/market_agent/tools.py", "services/tools/src/market_tools/"],
  },
  {
    title: "The agent runs sandboxed and traced.",
    body: "NVIDIA OpenShell runs the agent in a sandbox that limits which files and network endpoints it can reach. NeMo Relay records every model, tool, and skill step as a trace, and can export it to LangSmith when a project is configured.",
    marks: [
      { name: "OpenShell", owner: "nvidia" },
      { name: "NeMo Relay", owner: "nvidia" },
      { name: "LangSmith", owner: "langchain", qualifier: "Optional" },
    ],
    files: ["services/agent/src/market_agent/relay_tracing.py"],
  },
];

function TechnologyMark({ name, owner, logo, qualifier }: Mark) {
  return (
    <span className={`technology-mark ${logo ? "technology-mark-deepagents" : "technology-mark-fallback"}`}>
      <span className="technology-logo"><img src={logo ?? (owner === "nvidia" ? nvidiaLogo : langchainLogo)} alt="" /></span>
      <span className="technology-name">{name}</span>
      {qualifier && <small>{qualifier}</small>}
    </span>
  );
}

export function BuiltTogether({ status }: { status: SystemStatus | null }) {
  return (
    <main className="partnership-page">
      <div className="partnership-story">
        <header className="story-hero">
          <div className="story-hero-copy">
            <p className="story-kicker">One agent, built together</p>
            <h1>A financial research agent built with LangChain and NVIDIA.</h1>
            <p className="story-lead">Ask a market question. The agent picks a research skill, runs local Nemotron models with per-step escalation, measures evidence with GPU tools, and returns a sourced answer.</p>
          </div>
          <aside className="story-partner-lockup" aria-label="NVIDIA and LangChain">
            <img className="story-nvidia-logo" src={nvidiaLogo} alt="" />
            <span aria-hidden="true">×</span>
            <span className="story-langchain-logo"><img src={langchainLogo} alt="" /><strong>LangChain</strong></span>
          </aside>
        </header>

        <section className="story-path" aria-labelledby="story-path-title">
          <header className="story-path-heading">
            <div><h2 id="story-path-title">From a market question to a sourced answer</h2></div>
            <p>Each stage lists where to read the implementation in the repository.</p>
          </header>
          <ol className="story-stages">
            {STAGES.map((stage, index) => (
              <li key={stage.title}>
                <span className="story-stage-number">{index + 1}</span>
                <div className="story-stage-copy"><h3>{stage.title}</h3><p>{stage.body}</p></div>
                <div className="story-stage-footer">
                  <div className="story-technologies">{stage.marks.map((mark) => <TechnologyMark key={mark.name} {...mark} />)}</div>
                  <ul className="story-files" aria-label="Source files">{stage.files.map((file) => <li key={file}><code>{file}</code></li>)}</ul>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {status && (
          <section className="story-outcomes" aria-labelledby="story-runtime-title">
            <div className="story-outcome-heading"><p className="story-section-label">This deployment</p><h2 id="story-runtime-title">Models in use right now</h2></div>
            {status.models.map((model) => (
              <article key={model.id}><span>{model.role} · {model.where}</span><p className="mono">{model.id}</p></article>
            ))}
            {status.langsmith_project_url && <p><a href={status.langsmith_project_url} target="_blank" rel="noopener noreferrer">Open the LangSmith project <span aria-hidden="true">↗</span></a></p>}
          </section>
        )}
      </div>
    </main>
  );
}

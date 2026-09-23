import { useEffect, useRef, useState } from "react";
import type { SystemStatus } from "../api/types";
import "./partnership-page.css";

const nvidiaLogo = new URL("../assets/partners/nvidia_logo.png", import.meta.url).href;
const langchainLogo = new URL("../assets/partners/langchain_logo.png", import.meta.url).href;
const deepagentsLogo = new URL("../assets/partners/deepagents_logo.png", import.meta.url).href;
const langgraphLogo = new URL("../assets/partners/langgraph_logo.png", import.meta.url).href;

type Owner = "nvidia" | "langchain";
type DetailId = "agent" | "routing" | "sandbox" | "evidence" | "observability";

interface TechnologyLink {
  readonly label: string;
  readonly href: string;
  readonly note?: string;
}

interface TechnologyDetail {
  readonly id: DetailId;
  readonly owner: Owner;
  readonly eyebrow: string;
  readonly title: string;
  readonly description: string;
  readonly source: string;
  readonly sourceNote: string;
  readonly code: string;
  readonly note?: string;
  readonly links: readonly TechnologyLink[];
}

interface TechnologyMarkProps {
  readonly name: string;
  readonly owner: Owner;
  readonly logo?: "deepagents" | "langgraph";
  readonly qualifier?: string;
}

const AGENT_CODE = `def middleware():
    return [
        SkillsMiddleware(...),
        FilesystemMiddleware(...),
        RelayHeaderCompatibilityMiddleware(),
        RequireAnswerSubmissionMiddleware(...),
        SwitchyardRoutingMiddleware(adapter),
        ModelCallLimitMiddleware(run_limit=12, exit_behavior="error"),
    ]

agent_kwargs = add_nemo_relay_integration(
    model=self.efficient,
    tools=parent_tools,
    system_prompt=_prompt(...),
    middleware=middleware(),
    subagents=[],
    skills=["/skills/"],
    permissions=permissions,
    backend=backend,
    checkpointer=self.checkpointer,
    name="market-investigator",
)
agent_kwargs["middleware"] = _relay_outside_switchyard(
    agent_kwargs["middleware"]
)
agent = create_deep_agent(**agent_kwargs)`;

const ROUTING_CODE = `self.routing_algorithm = algorithms.llm_classifier(
    LlmClassifierConfig.escalation(
        config=EscalationClassifierConfig(
            confirmations=1,
            recent_turn_window=28,
            window_message_chars=4000,
            prompt=MARKET_ESCALATION_PROMPT,
        )
    )
)

adapter = EscalationAlgorithmAdapter(
    self.routing_algorithm,
    clients=clients,
    models={
        "judge": [LUNA_MODEL],
        "efficient": [LOCAL_MODEL],
        "capable": [CAPABLE_MODEL],
        "any": [LOCAL_MODEL, CAPABLE_MODEL, LUNA_MODEL],
    },
    session_id=investigation_id,
    observe=observer,
)

SwitchyardRoutingMiddleware(adapter)`;

const OPENSHELL_POLICY = `version: 1
filesystem_policy:
  include_workdir: true
  read_only:
    - /usr
    - /lib
    - /etc
    - /proc
    - /opt/market-agent
    - /srv/market-shock/scenario
    - /srv/market-shock/events
  read_write:
    - /tmp
    - /dev/null
    - /srv/market-shock/state
    - /srv/market-shock/traces
landlock:
  compatibility: hard_requirement
process:
  run_as_user: "1000"
  run_as_group: "1000"
network_policies:
  tools:
    name: market-tools
    endpoints:
      - host: tools
        port: 8000
        protocol: rest
        enforcement: enforce
        rules:
          - {allow: {method: GET, path: /mcp}}
          - {allow: {method: POST, path: /mcp}}
          - {allow: {method: DELETE, path: /mcp}}
          - {allow: {method: GET, path: /health}}
    binaries:
      - {path: /usr/local/bin/python3.12}
  local_model:
    name: approved-local-nemotron
    endpoints:
      - host: model
        port: 8001
        protocol: rest
        enforcement: enforce
        rules:
          - {allow: {method: POST, path: /v1/chat/completions}}
          - {allow: {method: GET, path: /v1/models}}
          - {allow: {method: GET, path: /health}}
    binaries:
      - {path: /usr/local/bin/python3.12}`;

const EVIDENCE_CODE = `def prove_runtime(bundle, candidate):
    # Every production GPU family must run before readiness is published.
    result = candidate.get_price_context("NVDA", cutoff)
    _receipt(result, "cudf", bundle)

    semantic = candidate.search_news("NVDA", cutoff, "runtime readiness")
    _receipt(semantic, "cuvs", bundle)

    graph = candidate.trace_shock_propagation("NVDA", cutoff, max_depth=1)
    _receipt(graph, "cugraph", bundle)

    risk = candidate.predict_volatility_risk("NVDA", cutoff)
    _receipt(risk, "xgboost-gpu", bundle)

    topics = candidate.project_news_topics("NVDA", cutoff, dimensions=2)
    _receipt(topics, "cuml", bundle)

    return {name: "pass" for name in (
        "market", "semantic", "graph", "risk", "topics"
    )}`;

const OBSERVABILITY_CODE = `def _build_plugin_config(settings):
    otel = None
    if settings.langsmith_enabled:
        otel = OpenTelemetrySectionConfig(
            enabled=True,
            endpoints=[OpenTelemetryEndpointConfig(
                type="gen_ai",
                endpoint=settings.langsmith_endpoint,
                transport="http_binary",
                service_name=settings.service_name,
                instrumentation_scope="nemo-relay",
                header_env={
                    "x-api-key": settings.langsmith_api_key_env,
                    "Langsmith-Project": settings.langsmith_project_env,
                },
            )],
        )

    observability = ObservabilityConfig(
        atof=AtofConfig(enabled=True, sinks=[AtofFileSinkConfig(
            output_directory=str(settings.trace_directory),
            filename=settings.trace_filename,
            mode="append",
        )]),
        opentelemetry=otel,
        enable_full_payloads=False,
    )
    return PluginConfig(components=[ComponentSpec(observability)])`;

const DETAILS: Readonly<Record<DetailId, TechnologyDetail>> = {
  agent: {
    id: "agent",
    owner: "langchain",
    eyebrow: "LangChain implementation",
    title: "How the Deep Agent is created",
    description: "One Deep Agent receives the approved market tools, the selected research skill, checkpointed conversation state, and the middleware that governs every model call. The default general-purpose subagent is disabled so this remains one understandable agent.",
    source: "services/agent/src/market_agent/deep_runtime.py:306",
    sourceNote: "Abridged from the production source. Ellipses replace argument bodies; the control flow and named middleware are unchanged.",
    code: AGENT_CODE,
    links: [
      { label: "Deep Agents documentation", href: "https://docs.langchain.com/oss/python/deepagents/overview" },
      { label: "LangGraph documentation", href: "https://docs.langchain.com/oss/python/langgraph/overview" },
      { label: "Relay integration for Deep Agents", href: "https://docs.nvidia.com/nemo/relay/v0.6.0/supported-integrations/deepagents", note: "Integration contract used by this build" },
    ],
  },
  routing: {
    id: "routing",
    owner: "nvidia",
    eyebrow: "NVIDIA model routing",
    title: "How Switchyard escalates a model turn",
    description: "For each Deep Agent model turn, Lightning produces a local result. Luna judges whether that result is sufficient. Switchyard returns the local result or calls Ultra when Luna confirms escalation. If a provider fails, the turn stops instead of silently choosing another model.",
    source: "services/agent/src/market_agent/deep_runtime.py:147, 273",
    sourceNote: "Abridged from the production source. The native escalation order is also asserted in tests/agent/test_switchyard_adapter.py:540. Model constants resolve to the approved runtime IDs shown below.",
    code: ROUTING_CODE,
    note: "Nemotron 3.5 Lightning is hosted on this DGX Spark. Nemotron 3 Ultra and the Luna judge come from the approved internal inference server. The optional answer-layout step can call Ultra directly after research; that traced presentation call does not pass through Switchyard. The Ultra link describes the public model family, not the server's checkpoint revision.",
    links: [
      { label: "Nemotron 3.5 Lightning · pinned revision", href: "https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4/tree/bee7596271d1495f6992ae224aefde4410e816b8", note: "Local efficient target" },
      { label: "Nemotron 3 Ultra · model information", href: "https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b", note: "Remote capable target in this demo" },
      { label: "NeMo Switchyard source", href: "https://github.com/NVIDIA-NeMo/Switchyard" },
      { label: "Switchyard routing overview", href: "https://developer.nvidia.com/blog/route-ai-agent-workloads-across-models-with-nvidia-nemo-switchyard" },
    ],
  },
  sandbox: {
    id: "sandbox",
    owner: "nvidia",
    eyebrow: "NVIDIA OpenShell",
    title: "What the agent sandbox allows",
    description: "The agent runs as a non-root process. Application code and prepared evidence are read-only. State, traces, and temporary files are writable. The Python process can call only the approved tool and local-model methods in this base policy.",
    source: "scripts/spark/openshell/policy.yaml:1",
    sourceNote: "Complete checked-in base policy. It contains no credentials or private provider URLs.",
    code: OPENSHELL_POLICY,
    note: "During preparation, enabled inference and LangSmith endpoints are added as endpoint-bound providers. Their credentials remain in the OpenShell gateway and are not written into this policy or the web application.",
    links: [
      { label: "OpenShell security guidance", href: "https://docs.nvidia.com/openshell/latest/security/best-practices.html" },
      { label: "OpenShell 0.0.116 release", href: "https://github.com/NVIDIA/OpenShell/releases/tag/v0.0.116", note: "Pinned by this demo" },
    ],
  },
  evidence: {
    id: "evidence",
    owner: "nvidia",
    eyebrow: "GPU-accelerated evidence",
    title: "How GPU execution is verified",
    description: "The agent can call seven bounded market tools. Before the tools service reports ready, it runs every production GPU family and validates a strict receipt. The application fails closed instead of silently moving the work to a CPU path.",
    source: "services/tools/src/market_tools/server.py:55",
    sourceNote: "Abridged from the production readiness proof. Repeated ticker-window checks and result-shape checks are omitted here.",
    code: EVIDENCE_CODE,
    note: "The tool results identify the engine, device, duration, artifact checksum, and whether a fallback was used. A valid GPU receipt requires fallback_used to be false.",
    links: [
      { label: "RAPIDS documentation", href: "https://docs.rapids.ai/" },
      { label: "CUDA-X libraries", href: "https://developer.nvidia.com/gpu-accelerated-libraries" },
      { label: "Nemotron 3 Embed model", href: "https://huggingface.co/nvidia/Nemotron-3-Embed-1B-BF16", note: "Local retrieval embedding model" },
    ],
  },
  observability: {
    id: "observability",
    owner: "langchain",
    eyebrow: "Observation and evaluation",
    title: "How one run becomes evidence for the next",
    description: "NeMo Relay records the agent, model, and tool hierarchy in a local append-only trace. When tracing is enabled, the same hierarchy is exported to LangSmith so the team can inspect runs and compare evaluations across versions.",
    source: "services/agent/src/market_agent/relay_tracing.py:101",
    sourceNote: "Abridged from the production plugin configuration. It names credential environment variables but never reads or displays their values.",
    code: OBSERVABILITY_CODE,
    note: "The live status on this page reports whether LangSmith export is enabled. NeMo Platform Insights and Eval Author describe a broader next workflow and are not deployed or automatically connected here.",
    links: [
      { label: "NeMo Relay overview", href: "https://docs.nvidia.com/nemo/relay/about-nemo-relay/overview" },
      { label: "LangSmith observability", href: "https://docs.langchain.com/langsmith/observability" },
      { label: "NeMo Platform insight-driven optimization", href: "https://docs.nvidia.com/nemo-platform/v0.4.0/documentation/agents/optimize-agents/insight-driven-optimization", note: "Broader next workflow; not deployed here" },
    ],
  },
};

const logoFor = (owner: Owner, logo?: TechnologyMarkProps["logo"]) => (
  logo === "deepagents" ? deepagentsLogo
    : logo === "langgraph" ? langgraphLogo
      : owner === "nvidia" ? nvidiaLogo : langchainLogo
);

function TechnologyMark({ name, owner, logo, qualifier }: TechnologyMarkProps) {
  return (
    <span className={`technology-mark ${logo ? `technology-mark-${logo}` : "technology-mark-fallback"}`} data-logo-owner={owner}>
      <span className="technology-logo"><img src={logoFor(owner, logo)} alt="" /></span>
      <span className="technology-name">{name}</span>
      {qualifier && <small>{qualifier}</small>}
    </span>
  );
}

function TechnologyDialog({ detail, projectLink, onClose }: {
  readonly detail: TechnologyDetail;
  readonly projectLink?: string;
  readonly onClose: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    previousFocus.current = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialog.current) return;
      const focusable = [...dialog.current.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], [tabindex]:not([tabindex='-1'])")];
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocus.current?.focus();
    };
  }, [onClose]);

  const links = detail.id === "observability" && projectLink
    ? [{ label: "Open this demo's LangSmith project", href: projectLink, note: "Requires project access" }, ...detail.links]
    : detail.links;

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(detail.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="technology-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialog} className="technology-dialog" role="dialog" aria-modal="true" aria-labelledby={`technology-dialog-${detail.id}`} aria-describedby={`technology-dialog-description-${detail.id}`}>
        <header className="technology-dialog-header">
          <div className="technology-dialog-owner">
            <img src={detail.owner === "nvidia" ? nvidiaLogo : langchainLogo} alt="" />
            <span>{detail.eyebrow}</span>
          </div>
          <button ref={close} className="technology-dialog-close" type="button" onClick={onClose} aria-label={`Close ${detail.title}`}>×</button>
        </header>
        <div className="technology-dialog-scroll">
          <div className="technology-dialog-intro">
            <h2 id={`technology-dialog-${detail.id}`}>{detail.title}</h2>
            <p id={`technology-dialog-description-${detail.id}`}>{detail.description}</p>
          </div>
          <section className="technology-code" aria-label="Production source excerpt">
            <header>
              <div><span>Production source</span><code>{detail.source}</code></div>
              <button type="button" onClick={() => void copyCode()}>{copied ? "Copied" : "Copy code"}</button>
            </header>
            <pre><code>{detail.code}</code></pre>
            <p>{detail.sourceNote}</p>
          </section>
          {detail.note && <aside className="technology-detail-note"><strong>How to read this</strong><p>{detail.note}</p></aside>}
          <nav className="technology-resources" aria-label={`${detail.title} resources`}>
            <h3>Learn more</h3>
            {links.map((link) => (
              <a href={link.href} target="_blank" rel="noopener noreferrer" key={link.href}>
                <span><strong>{link.label}</strong>{link.note && <small>{link.note}</small>}</span>
                <span aria-hidden="true">↗</span>
              </a>
            ))}
          </nav>
        </div>
      </section>
    </div>
  );
}

interface PartnershipPageProps {
  readonly status: SystemStatus | null;
}

export function PartnershipPage({ status }: PartnershipPageProps) {
  const [activeDetail, setActiveDetail] = useState<DetailId | null>(null);
  const route = status?.routes.find((candidate) => candidate.mode === "switchyard_escalation");
  const routingStatus = !route ? "Routing status unavailable" : route.enabled ? "Routing is enabled" : "Routing is disabled";
  const tracingStatus = !status ? "Tracing status unavailable" : status.observability.langsmith_export_enabled ? "LangSmith export is enabled" : "LangSmith export is disabled";
  const open = (detail: DetailId) => setActiveDetail(detail);

  return (
    <main className="partnership-page">
      <div className="partnership-story">
        <header className="story-hero">
          <div className="story-hero-copy">
            <p className="story-kicker">One agent, built and improved together</p>
            <h1>A financial research agent built with LangChain and NVIDIA.</h1>
            <p className="story-lead">Ask a market question. The agent plans the research, runs local models with governed escalation, uses GPU-accelerated tools, and returns a sourced answer.</p>
          </div>
          <aside className="story-partner-lockup" aria-label="NVIDIA and LangChain">
            <img className="story-nvidia-logo" src={nvidiaLogo} alt="" />
            <span aria-hidden="true">×</span>
            <span className="story-langchain-logo"><img src={langchainLogo} alt="" /><strong>LangChain</strong></span>
          </aside>
        </header>

        <section className="story-path" aria-labelledby="story-path-title">
          <header className="story-path-heading">
            <div><h2 id="story-path-title">From a market question to a better agent</h2></div>
            <p>Select any implementation link to see the code and configuration used by this demo.</p>
          </header>
          <ol className="story-stages">
            <li>
              <span className="story-stage-number">1</span>
              <div className="story-stage-copy"><h3>The application prepares the investigation.</h3><p>The application selects the relevant research skill. The agent, built with <strong>Deep Agents</strong>, reads that skill and decides which approved tools to use. <strong>LangGraph</strong> keeps the state for follow-up questions.</p></div>
              <div className="story-stage-footer">
                <div className="story-technologies" aria-label="Agent framework technologies">
                  <TechnologyMark name="LangChain" owner="langchain" />
                  <TechnologyMark name="Deep Agents" owner="langchain" logo="deepagents" />
                  <TechnologyMark name="LangGraph" owner="langchain" logo="langgraph" />
                </div>
                <button className="story-detail-button" type="button" onClick={() => open("agent")}>View agent initialization <span aria-hidden="true">→</span></button>
              </div>
            </li>
            <li>
              <span className="story-stage-number">2</span>
              <div className="story-stage-copy"><h3>The agent runs locally and escalates when needed.</h3><p><strong>Nemotron 3.5 Lightning</strong> produces the local result. <strong>Luna</strong> judges whether it is sufficient, and <strong>Switchyard</strong> calls <strong>Nemotron 3 Ultra</strong> only when escalation is confirmed.</p></div>
              <div className="story-stage-footer">
                <div className="story-technologies" aria-label="Model and routing technologies">
                  <TechnologyMark name="DGX Spark" owner="nvidia" />
                  <TechnologyMark name="Switchyard" owner="nvidia" />
                  <TechnologyMark name="Nemotron 3.5 Lightning" owner="nvidia" qualifier="Local" />
                  <TechnologyMark name="Nemotron 3 Ultra" owner="nvidia" qualifier="Server" />
                </div>
                <button className="story-detail-button" type="button" onClick={() => open("routing")}>View routing and model details <span aria-hidden="true">→</span></button>
                <small className="story-live-state" data-route-enabled={route?.enabled === true}>{routingStatus}</small>
              </div>
            </li>
            <li>
              <span className="story-stage-number">3</span>
              <div className="story-stage-copy"><h3>The agent uses approved data and tools.</h3><p><strong>Nemotron 3 Embed</strong> supports retrieval. <strong>CUDA-X</strong> and <strong>RAPIDS</strong> calculate, compare, and trace the evidence. <strong>OpenShell</strong> limits which files and endpoints the agent can reach.</p></div>
              <div className="story-stage-footer">
                <div className="story-technologies" aria-label="Evidence and security technologies">
                  <TechnologyMark name="CUDA-X / RAPIDS" owner="nvidia" />
                  <TechnologyMark name="OpenShell" owner="nvidia" />
                  <TechnologyMark name="Nemotron 3 Embed" owner="nvidia" qualifier="Local" />
                </div>
                <div className="story-detail-actions"><button className="story-detail-button" type="button" onClick={() => open("evidence")}>View GPU checks</button><button className="story-detail-button" type="button" onClick={() => open("sandbox")}>View OpenShell policy</button></div>
              </div>
            </li>
            <li>
              <span className="story-stage-number">4</span>
              <div className="story-stage-copy"><h3>Each run helps improve the next version.</h3><p><strong>NeMo Relay</strong> records the execution. <strong>LangSmith</strong> shows traces and evaluations today; <strong>NeMo Platform</strong> is a possible next step for a broader improvement workflow.</p></div>
              <div className="story-stage-footer">
                <div className="story-technologies" aria-label="Observation and evaluation technologies">
                  <TechnologyMark name="NeMo Relay" owner="nvidia" />
                  <TechnologyMark name="LangSmith" owner="langchain" />
                  <TechnologyMark name="NeMo Platform" owner="nvidia" qualifier="Next" />
                </div>
                <button className="story-detail-button" type="button" onClick={() => open("observability")}>View trace configuration <span aria-hidden="true">→</span></button>
                <small className="story-live-state" data-langsmith-enabled={status?.observability.langsmith_export_enabled === true}>{tracingStatus}</small>
              </div>
            </li>
          </ol>
        </section>

        <section className="story-outcomes" aria-labelledby="story-outcomes-title">
          <div className="story-outcome-heading"><p className="story-section-label">Why lower latency matters</p><h2 id="story-outcomes-title">Lower latency improves two feedback loops.</h2></div>
          <article><span>For the person using it</span><p>An answer arrives sooner, so the user can review the evidence and ask the next question sooner.</p></article>
          <article><span>For the team building it</span><p>Shorter runs allow more experiments and evaluation cycles in the same amount of time.</p></article>
          <div className="story-improvement-loop"><strong>Observe in LangSmith</strong><span aria-hidden="true">→</span><strong>Evaluate changes</strong><span aria-hidden="true">→</span><strong>Choose the next improvement</strong><small>Next, not deployed: <strong>NeMo Platform Insights + Eval Author</strong></small></div>
        </section>
      </div>
      {activeDetail && <TechnologyDialog detail={DETAILS[activeDetail]} projectLink={status?.observability.project_link} onClose={() => setActiveDetail(null)} />}
    </main>
  );
}

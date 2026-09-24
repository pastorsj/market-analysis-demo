import { useEffect, useState } from "react";
import type { InvestigationSeed } from "./components/InvestigationForm";
import { useInvestigation } from "./hooks/useInvestigation";
import { useSystemStatus } from "./hooks/useSystemStatus";
import { BuiltTogether } from "./pages/BuiltTogether";
import { Dashboard } from "./pages/Dashboard";
import { Research } from "./pages/Research";

// Modified NVIDIA eye path adapted from AI-Q's Apache-2.0 Logo component.
// See THIRD_PARTY_NOTICES.md and LICENSES/Apache-2.0.txt.
function NvidiaMark() {
  return (
    <svg className="nvidia-mark" viewBox="0 0 71 47" role="img" aria-label="NVIDIA">
      <path
        fill="currentColor"
        d="M7.255 20.234s6.419-9.476 19.236-10.455V6.34C12.294 7.481 0 19.51 0 19.51s6.963 20.138 26.491 21.982v-3.655C12.161 36.032 7.255 20.234 7.255 20.234zM26.49 30.57v3.346c-10.83-1.931-13.837-13.194-13.837-13.194s5.2-5.764 13.837-6.698v3.672c-4.532-.544-8.09 3.69-8.09 3.69s1.984 7.131 8.09 9.184zM26.49 0v6.341c.417-.033.834-.06 1.253-.074 16.14-.544 26.658 13.242 26.658 13.242s-12.08 14.694-24.663 14.694c-1.153 0-2.234-.107-3.248-.287v3.92a21.24 21.24 0 002.704.176c11.71 0 20.18-5.982 28.38-13.063 1.359 1.089 6.925 3.738 8.07 4.9-7.797 6.529-25.968 11.792-36.27 11.792-.993 0-1.947-.06-2.884-.15V47H71V0H26.491zm0 14.024V9.78c.412-.03.829-.052 1.253-.065 11.607-.365 19.222 9.977 19.222 9.977S38.742 31.12 29.923 31.12c-1.27 0-2.407-.205-3.432-.55V17.697c4.52.546 5.428 2.543 8.145 7.073l6.042-5.096s-4.41-5.787-11.845-5.787c-.81 0-1.583.057-2.342.138z"
      />
    </svg>
  );
}

function GitHubMark() {
  return (
    <svg className="github-mark" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 2C6.477 2 2 6.59 2 12.253c0 4.53 2.865 8.373 6.839 9.73.5.095.682-.222.682-.494 0-.244-.009-.89-.014-1.747-2.782.619-3.369-1.374-3.369-1.374-.455-1.184-1.11-1.499-1.11-1.499-.908-.636.069-.623.069-.623 1.003.073 1.531 1.057 1.531 1.057.892 1.566 2.341 1.114 2.91.852.091-.663.349-1.114.635-1.37-2.221-.259-4.556-1.139-4.556-5.067 0-1.119.39-2.034 1.029-2.752-.103-.259-.446-1.302.098-2.713 0 0 .84-.276 2.75 1.051A9.32 9.32 0 0 1 12 6.984a9.32 9.32 0 0 1 2.504.346c1.909-1.327 2.748-1.051 2.748-1.051.545 1.411.202 2.454.1 2.713.64.718 1.028 1.633 1.028 2.752 0 3.938-2.339 4.805-4.566 5.058.359.317.679.943.679 1.901 0 1.372-.013 2.479-.013 2.816 0 .274.18.594.688.493A10.26 10.26 0 0 0 22 12.253C22 6.59 17.523 2 12 2Z"
      />
    </svg>
  );
}

export type AppView = "dashboard" | "research" | "built-together";
const PATHS: Record<AppView, string> = { dashboard: "/", research: "/research", "built-together": "/built-together" };

export function viewFor(pathname: string, search = ""): AppView {
  if (pathname === "/research" || new URLSearchParams(search).has("investigation")) return "research";
  return pathname === "/built-together" ? "built-together" : "dashboard";
}

export default function App() {
  const { status, error: statusError } = useSystemStatus();
  const flow = useInvestigation();
  const [view, setView] = useState<AppView>(() => viewFor(window.location.pathname, window.location.search));
  const [seed, setSeed] = useState<InvestigationSeed | null>(null);

  useEffect(() => {
    const onHistory = () => setView(viewFor(window.location.pathname, window.location.search));
    window.addEventListener("popstate", onHistory);
    return () => window.removeEventListener("popstate", onHistory);
  }, []);

  const navigate = (next: AppView, fresh = false) => {
    const id = fresh ? undefined : flow.investigation?.investigation_id;
    const target = next === "research" && id ? `/research?investigation=${encodeURIComponent(id)}` : PATHS[next];
    window.history.pushState(null, "", target);
    setView(next);
  };
  const link = (next: AppView, label: string) => (
    <a href={PATHS[next]} aria-current={view === next ? "page" : undefined} onClick={(event) => { event.preventDefault(); navigate(next); }}>{label}</a>
  );

  const readiness = !status ? (statusError ? "Status unavailable" : "Checking system") : status.ready ? "Ready" : "Not ready";
  const readinessDetail = statusError ?? status?.reason ?? "All dependencies are available.";

  return (
    <div className={`app-shell${view === "research" && !flow.investigation ? " research-start-shell" : ""}`}>
      <header className="app-bar">
        <a className="brand" href="/" aria-label="Market Shock home" onClick={(event) => { event.preventDefault(); navigate("dashboard"); }}>
          <NvidiaMark />
          <strong>Market Shock</strong>
        </a>
        <nav className="app-navigation" aria-label="Primary navigation">
          {link("dashboard", "Dashboard")}
          {link("research", "Research")}
          {link("built-together", "Built Together")}
        </nav>
        <div className="app-actions">
          <div className="connection" role="status" title={readinessDetail} aria-label={`System ${readiness}: ${readinessDetail}`}>
            <span className={status?.ready ? "connection-dot" : "connection-dot warning"} aria-hidden="true" />
            {readiness}
          </div>
          {status?.langsmith_project_url && (
            <a className="langsmith-link" href={status.langsmith_project_url} target="_blank" rel="noopener noreferrer" aria-label="Open LangSmith traces in a new tab">
              LangSmith <span aria-hidden="true">↗</span>
            </a>
          )}
          <a className="repository-link" href="https://github.com/pastorsj/market-analysis-demo" target="_blank" rel="noopener noreferrer" aria-label="Open the Market Shock demo source code on GitHub in a new tab">
            <GitHubMark />
            <span>GitHub</span>
            <span className="external-link-arrow" aria-hidden="true">↗</span>
          </a>
        </div>
      </header>

      {view === "research" && (
        <aside className="research-disclosure" aria-label="Research use and privacy notice">
          <p><strong>Historical research demo — not investment advice.</strong> Do not enter personal or confidential information. Prompts, evidence, and outputs are sent to the configured inference endpoints and, when configured, LangSmith tracing.</p>
        </aside>
      )}

      {view === "dashboard" && (
        <Dashboard
          companies={status?.companies ?? []}
          coverage={status?.coverage ?? null}
          onOpenResearch={(next) => { flow.reset(); setSeed(next); navigate("research", true); }}
          onOpenTechnology={() => navigate("built-together")}
        />
      )}
      {view === "research" && <Research status={status} statusError={statusError} flow={flow} seed={seed} />}
      {view === "built-together" && <BuiltTogether status={status} />}
    </div>
  );
}

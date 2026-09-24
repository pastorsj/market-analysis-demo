import { useState, type ReactNode } from "react";
import type { Citation, Report, ToolSummary } from "../api/types";
import { formatDuration, words } from "../format";
import { ArtifactView } from "./ArtifactView";
import { EvidenceDrawer } from "./EvidenceDrawer";
import "./report.css";

function emphasizedText(value: string): ReactNode[] {
  const rendered: ReactNode[] = [];
  const pattern = /\*\*([^*\n]+)\*\*/g;
  let offset = 0, match: RegExpExecArray | null;
  while ((match = pattern.exec(value)) !== null) {
    if (match.index > offset) rendered.push(value.slice(offset, match.index));
    rendered.push(<strong key={`emphasis-${match.index}`}>{match[1]}</strong>);
    offset = match.index + match[0].length;
  }
  if (offset < value.length) rendered.push(value.slice(offset));
  return rendered;
}

/** Render only the server's bounded report Markdown contract.
 *
 * Unsupported Markdown is ordinary React text. There is deliberately no HTML,
 * link, image, code, quote, table, nesting, or general Markdown parser here.
 */
export function LimitedMarkdown({ value, className }: { value: string; className?: string }) {
  const lines = value.replaceAll("\r\n", "\n").split("\n");
  const blocks: ReactNode[] = [];
  const heading = /^(##|###) ([^#].*)$/;
  const unordered = /^- (.+)$/;
  const ordered = /^\d+\. (.+)$/;
  let index = 0;
  while (index < lines.length) {
    if (!lines[index].trim()) { index += 1; continue; }
    const headingMatch = lines[index].match(heading);
    if (headingMatch) {
      const content = emphasizedText(headingMatch[2]);
      blocks.push(headingMatch[1] === "##"
        ? <h3 key={`heading-${index}`}>{content}</h3>
        : <h4 key={`heading-${index}`}>{content}</h4>);
      index += 1;
      continue;
    }
    const listPattern = unordered.test(lines[index]) ? unordered : ordered.test(lines[index]) ? ordered : null;
    if (listPattern) {
      const items: ReactNode[] = [];
      const start = index;
      while (index < lines.length) {
        const item = lines[index].match(listPattern);
        if (!item) break;
        items.push(<li key={`item-${index}`}>{emphasizedText(item[1])}</li>);
        index += 1;
      }
      blocks.push(listPattern === ordered
        ? <ol key={`list-${start}`}>{items}</ol>
        : <ul key={`list-${start}`}>{items}</ul>);
      continue;
    }
    const paragraph: string[] = [];
    const start = index;
    while (index < lines.length && lines[index].trim()) {
      if (index !== start && (heading.test(lines[index]) || unordered.test(lines[index]) || ordered.test(lines[index]))) break;
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(<p key={`paragraph-${start}`}>{emphasizedText(paragraph.join(" "))}</p>);
  }
  return <div className={["limited-markdown", className].filter(Boolean).join(" ")}>{blocks}</div>;
}

function ToolResult({ tool }: { tool: ToolSummary }) {
  return (
    <li className={`tool-result ${tool.outcome}`}>
      <div className="tool-result-heading">
        <strong>{words(tool.tool)}</strong>
        <span>{tool.ticker} · {words(tool.outcome)}</span>
      </div>
      <p>{tool.summary}</p>
      {tool.receipt && (
        <p className="gpu-receipt" aria-label="GPU receipt">
          {tool.receipt.engine} on {tool.receipt.device} · {formatDuration(tool.receipt.duration_ms)}
        </p>
      )}
      {tool.limitations.length > 0 && <ul className="tool-limitations">{tool.limitations.map((item) => <li key={item}>{item}</li>)}</ul>}
    </li>
  );
}

export function ReportView({ report, onAsk }: { report: Report; onAsk?: (question: string) => void }) {
  const [selected, setSelected] = useState<Citation | null>(null);
  return (
    <article className="report" aria-label="Agent answer">
      <header className="report-header">
        <div className="assistant-label"><span className="assistant-mark">N</span><strong>Market Shock</strong></div>
        {report.kind === "guide" && <span className="route">Research guide</span>}
      </header>
      <LimitedMarkdown value={report.answer} className="summary" />

      {report.artifacts.length > 0 && (
        <section aria-label="Measured tool output">
          <h3>Measured tool output</h3>
          <div className="artifacts">{report.artifacts.map((artifact) => <ArtifactView artifact={artifact} key={artifact.artifact_id} />)}</div>
        </section>
      )}

      {report.uncertainty.length > 0 && (
        <section className="uncertainty">
          <h3>What remains uncertain</h3>
          <ul>{report.uncertainty.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      )}

      {report.citations.length > 0 && (
        <section className="sources-inventory">
          <h3>Sources</h3>
          <nav aria-label="Cited sources">
            {report.citations.map((citation, index) => (
              <button type="button" key={citation.citation_id} onClick={() => setSelected(citation)}>
                <span>[{index + 1}]</span> {citation.title}
              </button>
            ))}
          </nav>
        </section>
      )}

      {report.tools.length > 0 && (
        <section className="tool-results">
          <h3>Tool results</h3>
          <ul>{report.tools.map((tool, index) => <ToolResult tool={tool} key={`${tool.tool}-${tool.ticker}-${index}`} />)}</ul>
        </section>
      )}

      {report.limitations.length > 0 && (
        <section className="report-limitations">
          <h3>Limitations</h3>
          <ul>{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      )}

      {onAsk && report.suggested_questions.length > 0 && (
        <section className="suggested-questions">
          <h3>Suggested follow-ups</h3>
          <div>{report.suggested_questions.map((question) => <button type="button" key={question} onClick={() => onAsk(question)}>{question}</button>)}</div>
        </section>
      )}

      <EvidenceDrawer citation={selected} onClose={() => setSelected(null)} />
    </article>
  );
}

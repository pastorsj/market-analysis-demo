import { useState, type ReactNode } from "react";
import type { Citation, Report } from "../api/types";
import { ArtifactView } from "./ArtifactView";
import { EvidenceDrawer } from "./EvidenceDrawer";

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
export function LimitedMarkdown({ value, className, testId }: { value: string; className?: string; testId?: string }) {
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
  return <div className={["limited-markdown", className].filter(Boolean).join(" ")} data-report-summary={testId === "summary" ? "" : undefined}>{blocks}</div>;
}

const routeName = (value: string) => value === "local_only"
  ? "Local Nemotron"
  : value === "frontier_only"
    ? "Frontier model"
    : "Switchyard escalation";

const sentence = (value: string) => value.replaceAll("_", " ");

const confidenceLabel = (value: number) => value >= 0.8 ? "High confidence" : value >= 0.6 ? "Moderate confidence" : "Low confidence";

export function ReportView({ report }: { report: Report }) {
  const [selected, setSelected] = useState<Citation | null>(null);
  const citationsById = new Map(report.citations.map((citation) => [citation.citation_id, citation]));
  const claimedCitationIds = new Set(report.claims.flatMap((claim) => claim.citation_ids));
  const additionalCitations = report.citations.filter((citation) => !claimedCitationIds.has(citation.citation_id));

  return (
    <article className="report" data-report="final" data-answer-mode={report.answer_mode} data-route-mode={report.routing.effective_mode}>
      <header className="report-header">
        <div className="assistant-label"><span className="assistant-mark">N</span><strong>Market Shock</strong></div>
        <span className="route">{routeName(report.routing.effective_mode)}</span>
      </header>

      <h2 data-report-title="">{report.title}</h2>
      <LimitedMarkdown value={report.summary} className="summary" testId="summary" />
      {report.no_data_reasons.length > 0 && <ul className="no-data-reasons" aria-label="No-data reasons">{report.no_data_reasons.map((reason) => <li data-no-data-reason={reason} key={reason}>No-data reason: {sentence(reason)}</li>)}</ul>}

      <section>
        <h3>Evidence-backed findings</h3>
        <div className="claims">
          {report.claims.map((claim) => (
            <article
              className={`claim ${claim.kind}`}
              data-claim-id={claim.claim_id}
              data-claim-kind={claim.kind}
              data-claim-confidence={claim.confidence}
              key={claim.claim_id}
            >
              <div className="claim-meta">
                <span>{claim.kind}</span>
                <span>{confidenceLabel(claim.confidence)}</span>
              </div>
              {report.claims.length === 1 && claim.text === report.summary
                ? <p className="claim-evidence-label">Evidence supporting the answer above</p>
                : <LimitedMarkdown value={claim.text} className="claim-body" />}
              {claim.citation_ids.length > 0 && (
                <nav aria-label="Claim citations">
                  {claim.citation_ids.map((id) => {
                    const citation = citationsById.get(id);
                    return citation ? (
                      <button data-citation-id={id} key={id} onClick={() => setSelected(citation)}>
                        <span>[{report.citations.indexOf(citation) + 1}]</span> {citation.title}
                      </button>
                    ) : null;
                  })}
                </nav>
              )}
            </article>
          ))}
        </div>
      </section>

      {report.artifacts.length > 0 && (
        <section>
          <h3>Visual artifacts</h3>
          <div className="artifacts">
            {report.artifacts.map((artifact) => (
              <figure key={artifact.artifact_id}>
                <figcaption>{artifact.title}</figcaption>
                <ArtifactView artifact={artifact} />
              </figure>
            ))}
          </div>
        </section>
      )}

      {report.uncertainty.length > 0 && (
        <section className="uncertainty">
          <h3>What remains uncertain</h3>
          <ul>{report.uncertainty.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      )}

      {additionalCitations.length > 0 && (
        <section className="sources-inventory">
          <h3>Sources</h3>
          <p className="muted">Additional evidence retained in the report inventory.</p>
          <nav aria-label="Additional report sources">
            {additionalCitations.map((citation) => (
              <button data-citation-id={citation.citation_id} key={citation.citation_id} onClick={() => setSelected(citation)}>
                <span>[{report.citations.indexOf(citation) + 1}]</span> {citation.title}
              </button>
            ))}
          </nav>
        </section>
      )}

      <EvidenceDrawer citation={selected} onClose={() => setSelected(null)} />
    </article>
  );
}

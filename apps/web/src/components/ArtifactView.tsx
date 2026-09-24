import type { Artifact } from "../api/types";
import { formatNumber, formatPercent } from "../format";
import "./artifact.css";

type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((item): item is Row => item !== null && typeof item === "object") : [];
const num = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
const text = (value: unknown) => value === null || value === undefined ? "n/a" : String(value);

function AnalogueTable({ artifact }: { artifact: Artifact }) {
  const analogues = rows(artifact.data.rows);
  return (
    <>
      <table className="artifact-table">
        <caption>Measured tool output · sessions most similar to {text(artifact.data.target_session)}</caption>
        <thead><tr><th scope="col">Ticker</th><th scope="col">Session</th><th scope="col">Return</th><th scope="col">vs benchmark</th><th scope="col">Volume ratio</th><th scope="col">Distance</th></tr></thead>
        <tbody>
          {analogues.map((row, index) => (
            <tr key={`${text(row.ticker)}-${text(row.session_date)}-${index}`}>
              <th scope="row">{text(row.ticker)}</th>
              <td>{text(row.session_date)}</td>
              <td>{formatPercent(num(row.return_1d_pct))}</td>
              <td>{formatNumber(num(row.benchmark_relative_return_pp))} pp</td>
              <td>{formatNumber(num(row.volume_ratio))}×</td>
              <td>{formatNumber(num(row.distance), 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {typeof artifact.data.method === "string" && <p className="artifact-note">{artifact.data.method}</p>}
    </>
  );
}

function ComovementTable({ artifact }: { artifact: Artifact }) {
  const nodes = rows(artifact.data.nodes);
  return (
    <table className="artifact-table">
      <caption>Measured tool output · instruments linked to {text(artifact.data.target)} before {text(artifact.data.session_date)}</caption>
      <thead><tr><th scope="col">Instrument</th><th scope="col">Hops</th><th scope="col">Correlation</th><th scope="col">Session return</th></tr></thead>
      <tbody>
        {nodes.map((node) => (
          <tr key={text(node.ticker)}>
            <th scope="row">{text(node.ticker)}</th>
            <td>{text(node.hops)}</td>
            <td>{formatNumber(num(node.correlation_with_target))}</td>
            <td>{formatPercent(num(node.session_return_pct))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TopicMap({ artifact }: { artifact: Artifact }) {
  const points = rows(artifact.data.points).flatMap((point, index) => {
    const coordinates = Array.isArray(point.coordinates) ? point.coordinates.map(num) : [];
    const [x, y] = coordinates;
    return x === null || y === null || x === undefined || y === undefined ? [] : [{ key: `${text(point.evidence_id)}-${index}`, title: text(point.title), x, y }];
  });
  if (!points.length) return null;
  const xs = points.map((point) => point.x), ys = points.map((point) => point.y);
  const scale = (value: number, values: number[]) => {
    const minimum = Math.min(...values), maximum = Math.max(...values);
    return maximum === minimum ? 50 : 8 + 84 * (value - minimum) / (maximum - minimum);
  };
  return (
    <svg className="topic-map" viewBox="0 0 100 100" role="img" aria-label={`${artifact.title}: ${points.length} documents`}>
      {points.map((point) => (
        <circle key={point.key} cx={scale(point.x, xs)} cy={100 - scale(point.y, ys)} r="2.8"><title>{point.title}</title></circle>
      ))}
    </svg>
  );
}

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  return (
    <figure className="artifact-view" data-artifact-kind={artifact.kind}>
      <figcaption>{artifact.title}</figcaption>
      {artifact.kind === "analogue_table" && <AnalogueTable artifact={artifact} />}
      {artifact.kind === "comovement_graph" && <ComovementTable artifact={artifact} />}
      {artifact.kind === "topic_projection" && <TopicMap artifact={artifact} />}
      <details className="artifact-data">
        <summary>Inspect exact artifact data</summary>
        <pre>{JSON.stringify(artifact.data, null, 2)}</pre>
      </details>
    </figure>
  );
}

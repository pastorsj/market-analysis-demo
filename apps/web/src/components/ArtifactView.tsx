import type { Artifact } from "../api/types";
import "./artifact.css";

type Point = { evidence_id?: string; coordinates?: number[] };
type PlottedPoint = { evidenceId: string; x: number; y: number };

function plottedPoints(data: Record<string, unknown>): PlottedPoint[] {
  const points = Array.isArray(data.points) ? data.points as Point[] : [];
  return points.flatMap((point, index) => {
    const coordinates = point.coordinates;
    if (!Array.isArray(coordinates) || coordinates.length < 2 || !coordinates.slice(0, 2).every(Number.isFinite)) return [];
    return [{ evidenceId: point.evidence_id ?? `Point ${index + 1}`, x: coordinates[0], y: coordinates[1] }];
  });
}

function TopicMap({ artifact, points }: { artifact: Artifact; points: PlottedPoint[] }) {
  const xs = points.map((point) => point.x), ys = points.map((point) => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = (value: number, minimum: number, maximum: number) => maximum === minimum ? 50 : 8 + 84 * (value - minimum) / (maximum - minimum);
  return (
    <svg className="topic-map" viewBox="0 0 100 100" role="img" aria-label={`${artifact.title}, ${points.length} evidence points`}>
      {points.map((point) => (
        <g key={point.evidenceId}>
          <circle cx={scale(point.x, minX, maxX)} cy={100 - scale(point.y, minY, maxY)} r="2.8" />
          <title>{point.evidenceId}</title>
        </g>
      ))}
    </svg>
  );
}

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  const points = artifact.kind === "topic_projection" ? plottedPoints(artifact.data) : [];
  return (
    <div className="artifact-view" data-artifact-id={artifact.artifact_id} data-artifact-kind={artifact.kind}>
      {points.length > 0 && <TopicMap artifact={artifact} points={points} />}
      <details className="artifact-data">
        <summary>Inspect exact artifact data</summary>
        <pre data-artifact-json aria-label={`${artifact.title} exact artifact data`}>{JSON.stringify(artifact.data, null, 2)}</pre>
      </details>
    </div>
  );
}

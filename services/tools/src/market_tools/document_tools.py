"""Document tools: semantic news search and the topic map."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from . import evidence
from .models import Artifact, ToolResult, stable_id
from .runtime import Runtime, Timer, coverage, no_data, result
from .scenario import parse_time

METADATA_ONLY_WARNING = (
    "Documents marked content_scope=metadata_only record that a filing exists and when; "
    "they do not contain the filing's contents."
)


def _window(runtime: Runtime, ticker: str, as_of: datetime, lookback_days: int) -> list[dict]:
    start = as_of - timedelta(days=lookback_days)
    return [
        row
        for row in runtime.scenario.eligible_documents(ticker, as_of)
        if parse_time(row["available_at"]) >= start
    ]


def search_news(
    runtime: Runtime, ticker: str, as_of: datetime, query: str, top_k: int = 5, lookback_days: int = 90
) -> ToolResult:
    """Rank the ticker's documents available in the lookback window by similarity to ``query``."""
    timer = Timer()
    documents = _window(runtime, ticker, as_of, lookback_days)
    if not documents:
        return no_data(
            "search_news",
            as_of,
            ticker,
            "documents",
            "no_eligible_documents",
            f"No {ticker} documents were available in the {lookback_days} days before the cutoff.",
        )
    by_chunk = {row["chunk_id"]: row for row in documents}
    hits = runtime.semantic.search(query, set(by_chunk), top_k)
    pairs = [evidence.document(runtime.scenario, by_chunk[chunk_id], score) for chunk_id, score in hits]
    matches = [
        {
            "evidence_id": item.evidence_id,
            "title": item.values["title"],
            "source_type": item.values["source_type"],
            "content_scope": item.values["content_scope"],
            "available_at": citation.available_at.isoformat(),
            "similarity": item.values["similarity"],
        }
        for item, citation in pairs
    ]
    summary = (
        f"Ranked {len(documents)} {ticker} documents available from {lookback_days} days before the cutoff "
        f"by similarity to the query and returned the top {len(matches)}."
    )
    return result(
        "search_news",
        as_of,
        "ok",
        {
            "summary": summary,
            "query": query,
            "window_start": (as_of - timedelta(days=lookback_days)).isoformat(),
            "eligible_documents": len(documents),
            "matches": matches,
        },
        [coverage("documents", ticker, len(matches), min(top_k, len(documents)))],
        receipt=timer.receipt(runtime, "cuvs"),
        pairs=pairs,
        warnings=[METADATA_ONLY_WARNING],
    )


def project_news_topics(
    runtime: Runtime, ticker: str, as_of: datetime, max_documents: int = 24, dimensions: int = 2
) -> ToolResult:
    """Project the newest documents' stored embeddings into 2-D or 3-D with cuML UMAP."""
    timer = Timer()
    tool = "project_news_topics"
    eligible = runtime.scenario.eligible_documents(ticker, as_of)
    documents = eligible[:max_documents]
    if len(documents) < 3:
        return no_data(
            tool,
            as_of,
            ticker,
            "projection_documents",
            "insufficient_projection_documents",
            f"At least three {ticker} documents are needed; {len(documents)} were available.",
        )
    vectors = runtime.semantic.document_vectors([row["chunk_id"] for row in documents])
    projection = runtime.UMAP(
        n_components=dimensions,
        n_neighbors=min(15, len(documents) - 1),
        metric="cosine",
        init="random",
        random_state=7,
    ).fit_transform(vectors)
    points = runtime.cp.asnumpy(projection).tolist()
    if any(not math.isfinite(value) for point in points for value in point):
        return no_data(
            tool, as_of, ticker, "projection_documents", "projection_invalid", "UMAP produced NaN values."
        )
    pairs = [evidence.document(runtime.scenario, row) for row in documents]
    newest, oldest = documents[0]["available_at"][:10], documents[-1]["available_at"][:10]
    summary = (
        f"Projected the {len(documents)} newest of {len(eligible)} {ticker} documents available at the cutoff "
        f"({oldest} to {newest}) into {dimensions} dimensions from their stored Nemotron embeddings."
    )
    artifact = Artifact(
        artifact_id=stable_id("artifact", runtime.scenario.scenario_id, tool, ticker, as_of, max_documents),
        kind="topic_projection",
        title=f"{ticker} document map",
        data={
            "dimensions": dimensions,
            "points": [
                {"evidence_id": item.evidence_id, "title": item.values["title"], "coordinates": point}
                for (item, _), point in zip(pairs, points, strict=True)
            ],
        },
    )
    projection_evidence = evidence.computed(
        runtime.scenario,
        tool,
        as_of,
        {"summary": summary, "points": artifact.data["points"]},
        [item.evidence_id for item, _ in pairs],
    )
    return result(
        tool,
        as_of,
        "ok",
        {
            "summary": summary,
            "document_count": len(documents),
            "eligible_documents": len(eligible),
            "newest": newest,
            "oldest": oldest,
        },
        [coverage("projection_documents", ticker, len(documents), len(documents))],
        receipt=timer.receipt(runtime, "cuml"),
        pairs=[*pairs, projection_evidence],
        artifacts=[artifact],
        warnings=[
            METADATA_ONLY_WARNING,
            "Nearby points share wording, which does not establish a shared cause.",
        ],
    )

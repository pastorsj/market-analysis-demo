from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceItem(StrictModel):
    evidence_id: str
    observed_at: datetime
    available_at: datetime
    source_id: str
    values: dict[str, object]


class Citation(StrictModel):
    citation_id: str = Field(pattern=r"^cit-[a-f0-9]{12,64}$")
    evidence_id: str
    title: str = Field(min_length=1, max_length=500)
    url: AnyHttpUrl | None = None  # computed results have no external source
    source_type: Literal["market", "news", "filing", "release", "relationship", "model"]
    published_at: datetime
    available_at: datetime
    excerpt: str = Field(min_length=1, max_length=1200)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hindsight: bool = False


class ExecutionReceipt(StrictModel):
    engine: Literal["cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml"]
    device: str
    gpu_executed: Literal[True] = True
    duration_ms: Annotated[float, Field(ge=0)]
    scenario_id: str


class Artifact(StrictModel):
    artifact_id: str
    kind: Literal["analogue_table", "comovement_graph", "topic_projection"]
    title: str = Field(min_length=1, max_length=500)
    data: dict[str, object] = Field(default_factory=dict)


class CoverageItem(StrictModel):
    dimension: Literal[
        "instrument",
        "market_window",
        "documents",
        "analogue_candidates",
        "comovement",
        "risk_model",
        "projection_documents",
    ]
    key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:/@+\-]+$")
    status: Literal["available", "partial", "missing"]
    required: bool
    observed_count: int = Field(ge=0, le=1_000_000)
    expected_count: int | None = Field(default=None, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def count_matches_status(self) -> CoverageItem:
        if self.status == "missing" and self.observed_count != 0:
            raise ValueError("missing coverage cannot have observations")
        if self.status == "available" and self.observed_count == 0:
            raise ValueError("available coverage requires observations")
        return self


class ToolLimitation(StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    affected: Annotated[list[Annotated[str, Field(min_length=1, max_length=160)]], Field(max_length=20)] = (
        Field(default_factory=list)
    )


class ToolResult(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    tool: Literal[
        "detect_market_shock",
        "get_price_context",
        "search_news",
        "find_historical_analogues",
        "map_comovement",
        "predict_volatility_risk",
        "project_news_topics",
    ]
    as_of: datetime
    outcome: Literal["ok", "partial", "no_data"]
    coverage: Annotated[list[CoverageItem], Field(min_length=1, max_length=32)]
    limitations: Annotated[list[ToolLimitation], Field(max_length=20)] = Field(default_factory=list)
    evidence: Annotated[list[EvidenceItem], Field(max_length=100)]
    citations: Annotated[list[Citation], Field(max_length=100)]
    receipt: ExecutionReceipt | None = None  # None when no computation ran
    data: dict[str, object]
    artifacts: Annotated[list[Artifact], Field(max_length=10)] = Field(default_factory=list)
    warnings: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> ToolResult:
        unavailable = [item for item in self.coverage if item.status != "available"]
        if self.outcome == "ok" and (unavailable or self.limitations):
            raise ValueError("ok outcome requires complete coverage and no limitations")
        if self.outcome == "partial" and (
            not unavailable
            or not self.limitations
            or not any(item.status != "missing" for item in self.coverage)
        ):
            raise ValueError("partial outcome requires usable incomplete coverage")
        if self.outcome == "no_data" and (
            not self.limitations
            or not any(item.required and item.status == "missing" for item in self.coverage)
            or self.artifacts
        ):
            raise ValueError("no_data requires a missing prerequisite and no artifacts")
        evidence_ids = [item.evidence_id for item in self.evidence]
        citation_evidence = [item.evidence_id for item in self.citations]
        if len(evidence_ids) != len(set(evidence_ids)) or len(citation_evidence) != len(
            set(citation_evidence)
        ):
            raise ValueError("evidence and citation identities must be unique")
        if set(citation_evidence) != set(evidence_ids):
            raise ValueError("every evidence item requires exactly one citation")
        if not isinstance(self.data.get("summary"), str) or not self.data["summary"]:
            raise ValueError("tool data requires a non-empty summary")
        return self


def stable_id(prefix: str, *values: object, length: int = 24) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:length]}"

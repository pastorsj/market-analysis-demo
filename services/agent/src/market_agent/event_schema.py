"""Immutable event artifact models and their validation contract."""

from __future__ import annotations

from datetime import date, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class EventCatalogError(ValueError):
    """A stable failure code for a catalog that must not enter the runtime."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise EventCatalogError(code)


class ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Binding(ExactModel):
    scenario_id: str = Field(min_length=3, max_length=160)
    scenario_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_snapshot_id: str = Field(min_length=3, max_length=160)
    market_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_snapshot_id: str = Field(min_length=3, max_length=160)
    document_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Category(ExactModel):
    category_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    label: str = Field(min_length=3, max_length=64)
    description: str = Field(min_length=10, max_length=240)
    sort_order: int = Field(ge=1, le=32)


class Question(ExactModel):
    question_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    label: str = Field(min_length=3, max_length=72)
    capability: Literal[
        "move-measurement",
        "evidence-review",
        "peer-comparison",
        "historical-analogues",
        "shock-propagation",
        "risk-analysis",
    ]
    text: str = Field(min_length=12, max_length=500)


class MarketRequirement(ExactModel):
    required: Literal[True]
    required_fields: tuple[Literal["adjusted_close", "volume"], ...]
    price_basis: Literal["provider_adjusted"]

    @model_validator(mode="after")
    def bounded(self):
        _need(
            1 <= len(self.required_fields) <= 2
            and tuple(sorted(set(self.required_fields))) == self.required_fields,
            "event_market_requirement",
        )
        return self


class DocumentRequirement(ExactModel):
    required_for_ready: Literal[True]
    requirement_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    source_kinds: tuple[Literal["company_release", "filing", "primary_source"], ...]

    @model_validator(mode="after")
    def bounded(self):
        _need(
            1 <= len(self.source_kinds) <= 3 and tuple(sorted(set(self.source_kinds))) == self.source_kinds,
            "event_document_requirement",
        )
        return self


class NewsRequirement(ExactModel):
    required_for_publication: Literal[False]
    required_for_ready: Literal[True]
    source_kind: Literal["licensed_news_metadata"]


class DerivedRequirement(ExactModel):
    required_for_ready: Literal[True]
    features: tuple[
        Literal[
            "event-returns",
            "relative-returns",
            "volume-context",
            "historical-analogues",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def bounded(self):
        order = ("event-returns", "relative-returns", "volume-context", "historical-analogues")
        _need(
            1 <= len(self.features) <= 4
            and tuple(item for item in order if item in self.features) == self.features,
            "event_derived_requirement",
        )
        return self


class SourceRequirements(ExactModel):
    market: MarketRequirement
    documents: DocumentRequirement
    licensed_news: NewsRequirement
    derived_features: DerivedRequirement


class Gap(ExactModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=96)
    layer: Literal["market", "documents", "licensed_news", "derived_features"]
    detail: str = Field(min_length=10, max_length=280)


class LayerQualification(ExactModel):
    status: Literal["ready", "partial", "blocked"]
    gaps: tuple[Gap, ...]


class Qualification(ExactModel):
    status: Literal["ready", "partial"]
    market: LayerQualification
    documents: LayerQualification
    licensed_news: LayerQualification
    derived_features: LayerQualification
    gaps: tuple[Gap, ...]

    @model_validator(mode="after")
    def consistent(self):
        layers = (
            ("market", self.market),
            ("documents", self.documents),
            ("licensed_news", self.licensed_news),
            ("derived_features", self.derived_features),
        )
        _need(self.market.status == "ready", "event_market_not_ready")
        for name, layer in layers:
            allowed = {"ready", "blocked"} if name == "market" else {"ready", "partial"}
            _need(
                layer.status in allowed and (layer.status == "ready") == (not layer.gaps),
                "event_layer_status",
            )
            _need(all(gap.layer == name for gap in layer.gaps), "event_gap_layer")
        flattened = tuple(gap for _, layer in layers for gap in layer.gaps)
        key = lambda gap: (gap.layer, gap.code, gap.detail)
        _need(len(set(key(gap) for gap in flattened)) == len(flattened), "event_gap_projection")
        _need(tuple(sorted(self.gaps, key=key)) == tuple(sorted(flattened, key=key)), "event_gap_projection")
        _need((self.status == "ready") == all(layer.status == "ready" for _, layer in layers), "event_status")
        return self


class Limitation(ExactModel):
    limitation_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    detail: str = Field(min_length=10, max_length=280)


class Event(ExactModel):
    event_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    category_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    title: str = Field(min_length=5, max_length=100)
    summary: str = Field(min_length=20, max_length=420)
    sort_order: int = Field(ge=1, le=10_000)
    event_session: date
    source_dates: tuple[date, ...]
    primary_ticker: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")
    analysis_tickers: tuple[str, ...]
    context_instruments: tuple[str, ...]
    start_session: date
    end_session: date
    default_cutoff: datetime
    questions: tuple[Question, ...]
    source_requirements: SourceRequirements
    limitations: tuple[Limitation, ...]
    qualification: Qualification

    @field_validator("default_cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime) -> datetime:
        _need(value.tzinfo is not None, "event_cutoff")
        return value

    @model_validator(mode="after")
    def bounded(self):
        _need(
            1 <= len(self.analysis_tickers) <= 5
            and len(set(self.analysis_tickers)) == len(self.analysis_tickers),
            "event_analysis_scope",
        )
        _need(self.analysis_tickers[0] == self.primary_ticker, "event_primary_scope")
        _need(
            1 <= len(self.context_instruments) <= 4
            and len(set(self.context_instruments)) == len(self.context_instruments),
            "event_context_scope",
        )
        _need(not set(self.analysis_tickers) & set(self.context_instruments), "event_scope_overlap")
        _need(
            all(_TICKER.fullmatch(item) for item in (*self.analysis_tickers, *self.context_instruments)),
            "event_ticker",
        )
        _need(
            self.start_session <= self.event_session <= self.end_session
            and self.event_session <= self.default_cutoff.date() <= self.end_session,
            "event_chronology",
        )
        _need(
            1 <= len(self.source_dates) <= 8
            and len(set(self.source_dates)) == len(self.source_dates)
            and all(self.start_session <= item <= self.default_cutoff.date() for item in self.source_dates),
            "event_source_dates",
        )
        _need(
            3 <= len(self.questions) <= 6
            and len({item.question_id for item in self.questions}) == len(self.questions),
            "event_questions",
        )
        _need(
            1 <= len(self.limitations) <= 20
            and len({item.limitation_id for item in self.limitations}) == len(self.limitations),
            "event_limitations",
        )
        return self


class ExcludedEvent(ExactModel):
    event_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    gaps: tuple[Gap, ...]

    @model_validator(mode="after")
    def market_only(self):
        _need(bool(self.gaps) and all(item.layer == "market" for item in self.gaps), "event_exclusion")
        return self


class Summary(ExactModel):
    declared_events: int = Field(ge=0)
    published_events: int = Field(ge=0)
    ready_events: int = Field(ge=0)
    partial_events: int = Field(ge=0)
    excluded_events: int = Field(ge=0)


class PreparedCatalog(ExactModel):
    schema_version: Literal[1]
    artifact_id: str = Field(pattern=r"^shock-events-[a-f0-9]{16}$")
    catalog_id: Literal["curated-shock-events-v1"]
    calendar: Literal["XNYS"]
    timezone: Literal["America/New_York"]
    binding: Binding
    categories: tuple[Category, ...]
    events: tuple[Event, ...]
    excluded_events: tuple[ExcludedEvent, ...]
    summary: Summary

    @model_validator(mode="after")
    def consistent(self):
        _need(bool(self.categories) and len(self.categories) <= 32, "event_categories")
        _need(len({item.category_id for item in self.categories}) == len(self.categories), "event_categories")
        _need(
            tuple(item.sort_order for item in self.categories) == tuple(range(1, len(self.categories) + 1)),
            "event_category_order",
        )
        category_ids = {item.category_id for item in self.categories}
        _need(bool(self.events) and len(self.events) <= 1_000, "event_count")
        _need(len({item.event_id for item in self.events}) == len(self.events), "event_identity")
        _need(all(item.category_id in category_ids for item in self.events), "event_category")
        _need(
            tuple(item.sort_order for item in self.events)
            == tuple(sorted(item.sort_order for item in self.events)),
            "event_order",
        )
        question_ids = [question.question_id for event in self.events for question in event.questions]
        _need(len(question_ids) == len(set(question_ids)), "event_question_identity")
        ready = sum(item.qualification.status == "ready" for item in self.events)
        _need(
            self.summary.published_events == len(self.events)
            and self.summary.ready_events == ready
            and self.summary.partial_events == len(self.events) - ready,
            "event_summary",
        )
        _need(
            self.summary.excluded_events == len(self.excluded_events)
            and self.summary.declared_events == len(self.events) + len(self.excluded_events),
            "event_summary",
        )
        return self


class ArtifactRecord(ExactModel):
    path: Literal["catalog.json"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0, le=8 * 1024 * 1024)
    records: int = Field(ge=0, le=1_000)
    media_type: Literal["application/json"]


class ArtifactManifest(ExactModel):
    schema_version: Literal[1]
    artifact_kind: Literal["shock-event-catalog-v1"]
    artifact_id: str = Field(pattern=r"^shock-events-[a-f0-9]{16}$")
    catalog_id: Literal["curated-shock-events-v1"]
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding: Binding
    summary: Summary
    artifacts: tuple[ArtifactRecord, ...]

    @model_validator(mode="after")
    def one_catalog(self):
        _need(len(self.artifacts) == 1, "event_artifacts")
        return self

"""GPU runtime shared by every tool, plus small helpers for building results."""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .market import EASTERN, MarketData
from .models import (
    Artifact,
    Citation,
    CoverageItem,
    EvidenceItem,
    ExecutionReceipt,
    ToolLimitation,
    ToolResult,
)
from .scenario import Scenario, Session, parse_time
from .semantic import SemanticIndex


class Runtime:
    """Loaded scenario, market frame, semantic index, and risk model on one GPU."""

    def __init__(self, settings: Settings):
        import cudf
        import cugraph
        import cupy as cp
        from cuml.manifold import UMAP
        from xgboost import Booster

        self.cp, self.cudf, self.cugraph, self.UMAP = cp, cudf, cugraph, UMAP
        self.device = cp.cuda.runtime.getDeviceProperties(0)["name"].decode()
        self.scenario = Scenario(settings.root / "scenario")
        self.market = MarketData(self.scenario, cudf, cp)
        self.semantic = SemanticIndex(self.scenario, settings.embed_path, cp)
        self.risk_model = Booster()
        self.risk_model.load_model(str(self.scenario.risk_model_path))
        self.risk_model.set_param({"device": "cuda"})
        self.actions = cudf.read_parquet(str(self.scenario.actions_path)).to_pandas().to_dict("records")

    def session(self, as_of: datetime) -> Session | None:
        """The last completed session at the cutoff, if the cutoff is inside coverage."""
        if not self.market.in_coverage(as_of.astimezone(EASTERN).date().isoformat()):
            return None
        return self.scenario.completed_session(as_of)

    def known_actions(self, ticker: str, as_of: datetime) -> list[dict[str, Any]]:
        return [
            row
            for row in self.actions
            if row["instrument_id"] == ticker and parse_time(row["published_at"]) <= as_of.astimezone(UTC)
        ]


def coverage(dimension: str, key: str, observed: int, expected: int, required: bool = True) -> CoverageItem:
    status = "available" if observed >= expected else "partial" if observed else "missing"
    return CoverageItem(
        dimension=dimension,
        key=key,
        status=status,
        required=required,
        observed_count=observed,
        expected_count=expected,
    )


def limitation(code: str, message: str, *affected: str) -> ToolLimitation:
    return ToolLimitation(code=code, message=message, affected=list(affected))


class Timer:
    def __init__(self) -> None:
        self.started = time.perf_counter()

    def receipt(self, runtime: Runtime, engine: str) -> ExecutionReceipt:
        return ExecutionReceipt(
            engine=engine,
            device=runtime.device,
            duration_ms=(time.perf_counter() - self.started) * 1000,
            scenario_id=runtime.scenario.scenario_id,
        )


def result(
    tool: str,
    as_of: datetime,
    outcome: str,
    data: dict[str, Any],
    coverage_items: list[CoverageItem],
    limitations: list[ToolLimitation] = (),
    *,
    receipt: ExecutionReceipt | None = None,
    pairs: Iterable[tuple[EvidenceItem, Citation]] = (),
    artifacts: list[Artifact] = (),
    warnings: list[str] = (),
) -> ToolResult:
    unique: dict[str, tuple[EvidenceItem, Citation]] = {}
    for item, citation in pairs:
        unique.setdefault(item.evidence_id, (item, citation))
    return ToolResult(
        tool=tool,
        as_of=as_of,
        outcome=outcome,
        coverage=coverage_items,
        limitations=list(limitations),
        evidence=[item for item, _ in unique.values()],
        citations=[citation for _, citation in unique.values()],
        receipt=receipt,
        data=data,
        artifacts=list(artifacts),
        warnings=list(warnings),
    )


def no_data(tool: str, as_of: datetime, key: str, dimension: str, code: str, message: str) -> ToolResult:
    return result(
        tool,
        as_of,
        "no_data",
        {"summary": message},
        [coverage(dimension, key, 0, 1)],
        [limitation(code, message, key)],
    )


RECONSTRUCTION_WARNING = (
    "Prices are a later reconstruction adjusted for splits with later information, "
    "not an archived-at-cutoff feed."
)

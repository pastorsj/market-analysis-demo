"""Load one prepared scenario bundle and verify every file the tools read."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ScenarioError(RuntimeError):
    """Startup failure with a stable, log-safe code."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ScenarioError("naive_timestamp", value)
    return parsed.astimezone(UTC)


def sha256_file(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise ScenarioError("artifact_unreadable", path.name) from exc


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError("json_unreadable", path.name) from exc


def contained(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root.resolve() and root.resolve() not in candidate.parents:
        raise ScenarioError("artifact_escape", relative)
    return candidate


def verify_artifacts(root: Path, artifacts: list[dict[str, Any]]) -> None:
    """Fail closed unless every declared artifact has its recorded size and digest."""
    for item in artifacts:
        path = contained(root, item["path"])
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise ScenarioError("artifact_size_mismatch", item["path"])
        if sha256_file(path) != item["sha256"]:
            raise ScenarioError("artifact_digest_mismatch", item["path"])


@dataclass(frozen=True)
class Session:
    session_date: str
    open_at: datetime
    close_at: datetime


class Scenario:
    """Immutable metadata for one published scenario (market, documents, models)."""

    def __init__(self, root: Path):
        self.root = root
        self.manifest = read_json(root / "manifest.json")
        self.manifest_sha256 = sha256_file(root / "manifest.json")
        verify_artifacts(root, self.manifest["artifacts"])

        self.market_root = contained(root, self.manifest["market"]["path"])
        if sha256_file(self.market_root / "manifest.json") != self.manifest["market"]["manifest_sha256"]:
            raise ScenarioError("market_manifest_digest_mismatch")
        self.market = read_json(self.market_root / "manifest.json")
        verify_artifacts(self.market_root, self.market["artifacts"])

        self.scenario_id: str = self.manifest["scenario_id"]
        self.vintage_status: str = self.manifest["vintage_status"]
        universe = self.market["universe"]
        self.targets: tuple[str, ...] = tuple(universe["targets"])
        self.peers: tuple[str, ...] = tuple(universe["peers"])
        self.benchmarks: tuple[str, ...] = tuple(universe["required_benchmarks"])
        self.optional_instruments: tuple[str, ...] = tuple(universe["optional_instruments"])
        self.benchmark_policy: dict[str, dict[str, list[str]]] = self.market["benchmark_policy"]
        self.field_coverage: dict[str, dict[str, Any]] = self.market["field_coverage"]
        self.sources = {item["source_id"]: item for item in self.market["sources"]}
        self.bar_paths = sorted(
            contained(self.market_root, item["path"])
            for item in self.market["artifacts"]
            if item["path"].startswith("bars/") and item["path"].endswith(".parquet")
        )
        self.instruments_path = self.market_root / "instruments.parquet"
        self.actions_path = self.market_root / "actions.parquet"

        index = read_json(root / "processed/session-index.json")
        self.sessions = tuple(
            Session(row["session_date"], parse_time(row["open_at"]), parse_time(row["close_at"]))
            for row in index["sessions"]
        )
        documents = [
            json.loads(line)
            for line in (root / "processed/documents.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.documents = sorted(documents, key=lambda row: (row["available_at"], row["chunk_id"]))
        self.risk_model = read_json(root / "models/risk-model.json")
        self.risk_model_path = contained(root / "models", self.risk_model["model_path"])
        self.semantic_index = read_json(root / "indexes/cuvs-index.json")
        self.embedding_ids: list[str] = read_json(root / "indexes/embedding_ids.json")

    def completed_session(self, as_of: datetime) -> Session | None:
        """Return the last session whose close is at or before the cutoff."""
        cutoff = as_of.astimezone(UTC)
        eligible = [item for item in self.sessions if item.close_at <= cutoff]
        return eligible[-1] if eligible else None

    def benchmarks_for(self, sector: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        policy = self.benchmark_policy.get(sector or "", {"required": ["SPY"], "optional": []})
        return tuple(policy["required"]), tuple(policy["optional"])

    def eligible_documents(self, ticker: str, as_of: datetime) -> list[dict[str, Any]]:
        """Documents about the ticker available at the cutoff, newest first, one per source."""
        cutoff = as_of.astimezone(UTC)
        newest: dict[str, dict[str, Any]] = {}
        for row in self.documents:
            if ticker in row["issuer_ids"] and parse_time(row["available_at"]) <= cutoff:
                newest[row["source_id"]] = row  # later revisions replace earlier ones
        return sorted(newest.values(), key=lambda row: row["available_at"], reverse=True)

    def source_url(self, source_id: str) -> str | None:
        source = self.sources.get(source_id)
        return source.get("attribution_url") if source else None

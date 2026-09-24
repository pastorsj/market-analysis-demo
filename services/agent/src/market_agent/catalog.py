"""What the agent knows about the prepared data: tickers, sessions, and curated events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import COMPANIES
from .event_schema import ArtifactManifest, Event, EventCatalogError, PreparedCatalog


@dataclass(frozen=True)
class Session:
    session_date: date
    close_at: datetime


@dataclass(frozen=True)
class Coverage:
    """Tickers and trading sessions of the published scenario (validated by the tools service)."""

    scenario_id: str
    scenario_manifest_sha256: str
    targets: tuple[str, ...]
    peers: tuple[str, ...]
    sessions: tuple[Session, ...]

    @classmethod
    def load(cls, root: Path) -> Coverage:
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        market = json.loads((root / manifest["market"]["path"] / "manifest.json").read_text())
        index = json.loads((root / "processed/session-index.json").read_text())
        return cls(
            scenario_id=manifest["scenario_id"],
            scenario_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            targets=tuple(market["universe"]["targets"]),
            peers=tuple(market["universe"]["peers"]),
            sessions=tuple(
                Session(date.fromisoformat(row["session_date"]), _utc(row["close_at"]))
                for row in index["sessions"]
            ),
        )

    @property
    def tickers(self) -> tuple[str, ...]:
        """Companies a user can research (targets first, then peers)."""
        return (*self.targets, *self.peers)

    @property
    def first_session(self) -> date:
        return self.sessions[0].session_date

    @property
    def last_session(self) -> date:
        return self.sessions[-1].session_date

    def session_on_or_before(self, day: date) -> Session | None:
        """The last trading session on or before ``day``, if ``day`` is inside coverage."""
        if not self.first_session <= day <= self.last_session:
            return None
        return next(item for item in reversed(self.sessions) if item.session_date <= day)

    def companies(self) -> list[dict[str, str]]:
        return [{"symbol": symbol, "name": COMPANIES.get(symbol, (symbol, ()))[0]} for symbol in self.tickers]


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class EventCatalog:
    """Curated shock events bound to the published scenario."""

    def __init__(self, document: PreparedCatalog):
        self.document = document
        self.events = {item.event_id: item for item in document.events}

    @classmethod
    def load(cls, root: Path, coverage: Coverage) -> EventCatalog:
        root = root.resolve(strict=True)
        manifest = ArtifactManifest.model_validate_json((root / "manifest.json").read_bytes())
        body = (root / "catalog.json").read_bytes()
        if hashlib.sha256(body).hexdigest() != manifest.artifacts[0].sha256:
            raise EventCatalogError("event_catalog_digest")
        document = PreparedCatalog.model_validate_json(body)
        if document.binding.scenario_id != coverage.scenario_id:
            raise EventCatalogError("event_catalog_scenario_mismatch")
        return cls(document)

    def get(self, event_id: str) -> Event | None:
        return self.events.get(event_id)

    def public(self) -> dict[str, Any]:
        return {
            "categories": [item.model_dump(mode="json") for item in self.document.categories],
            "events": [
                item.model_dump(mode="json", exclude={"source_requirements", "qualification"})
                | {"status": item.qualification.status}
                for item in self.document.events
            ],
        }

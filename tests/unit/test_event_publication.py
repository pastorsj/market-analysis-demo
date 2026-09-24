from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.spark.event_publication import (
    EventPublicationError,
    publish,
    restore,
    verify_binding,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _write(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _fixture(tmp_path: Path) -> dict[str, Path | str]:
    repository = tmp_path / "repository"
    catalog = repository / "data/events/catalog.yaml"
    schema = repository / "data/schemas/shock-event-catalog.schema.json"
    catalog_sha = _write(
        catalog,
        b"schema_version: 1\ncatalog_id: curated-shock-events-v1\n",
    )
    schema_sha = _write(schema, b'{"type":"object"}\n')

    scenario = tmp_path / "runtime/candidates/market-shock-v2-0123456789abcdef"
    scenario_manifest = {
        "schema_version": 2,
        "scenario_id": scenario.name,
        "market": {"snapshot_id": "market-test", "manifest_sha256": "1" * 64},
        "documents": {"snapshot_id": "documents-test", "manifest_sha256": "2" * 64},
    }
    scenario_body = _canonical(scenario_manifest) + b"\n"
    scenario_sha = _write(scenario / "manifest.json", scenario_body)
    binding = {
        "scenario_id": scenario.name,
        "scenario_manifest_sha256": scenario_sha,
        "market_snapshot_id": "market-test",
        "market_manifest_sha256": "1" * 64,
        "document_snapshot_id": "documents-test",
        "document_manifest_sha256": "2" * 64,
    }
    identity = {
        "catalog_sha256": catalog_sha,
        "schema_sha256": schema_sha,
        "binding": binding,
    }
    artifact_id = "shock-events-" + hashlib.sha256(_canonical(identity)).hexdigest()[:16]
    events_root = tmp_path / "runtime/events"
    artifact = events_root / "artifacts" / artifact_id
    summary = {
        "declared_events": 1,
        "published_events": 1,
        "ready_events": 1,
        "partial_events": 0,
        "excluded_events": 0,
    }
    payload = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "catalog_id": "curated-shock-events-v1",
        "calendar": "XNYS",
        "timezone": "America/New_York",
        "binding": binding,
        "categories": [
            {
                "category_id": "test-category",
                "label": "Test category",
                "description": "A complete category used by publication contract tests.",
                "sort_order": 1,
            }
        ],
        "events": [
            {
                "event_id": "test-event",
                "category_id": "test-category",
                "title": "Complete test event",
                "summary": "A complete prepared event used to exercise publication semantics.",
                "sort_order": 1,
                "event_session": "2026-01-02",
                "source_dates": ["2026-01-02"],
                "primary_ticker": "NVDA",
                "analysis_tickers": ["NVDA"],
                "context_instruments": ["SPY"],
                "start_session": "2026-01-02",
                "end_session": "2026-01-02",
                "default_cutoff": "2026-01-02T20:00:00Z",
                "questions": [
                    {
                        "question_id": f"test-question-{number}",
                        "label": f"Test question {number}",
                        "capability": capability,
                        "text": f"What does complete test question {number} establish about this event?",
                    }
                    for number, capability in enumerate(
                        (
                            "move-measurement",
                            "evidence-review",
                            "peer-comparison",
                        ),
                        start=1,
                    )
                ],
                "source_requirements": {
                    "market": {
                        "required": True,
                        "required_fields": ["adjusted_close", "volume"],
                        "price_basis": "provider_adjusted",
                    },
                    "documents": {
                        "required_for_ready": True,
                        "requirement_id": "test-documents",
                        "source_kinds": ["company_release"],
                    },
                    "licensed_news": {
                        "required_for_publication": False,
                        "required_for_ready": True,
                        "source_kind": "licensed_news_metadata",
                    },
                    "derived_features": {
                        "required_for_ready": True,
                        "features": ["event-returns"],
                    },
                },
                "limitations": [
                    {
                        "limitation_id": "test-limitation",
                        "detail": "This is synthetic publication-contract test data only.",
                    }
                ],
                "qualification": {
                    "status": "ready",
                    "market": {"status": "ready", "gaps": []},
                    "documents": {"status": "ready", "gaps": []},
                    "licensed_news": {"status": "ready", "gaps": []},
                    "derived_features": {"status": "ready", "gaps": []},
                    "gaps": [],
                },
            }
        ],
        "excluded_events": [],
        "summary": summary,
    }
    payload_body = _canonical(payload) + b"\n"
    payload_sha = _write(artifact / "catalog.json", payload_body)
    manifest = {
        "schema_version": 1,
        "artifact_kind": "shock-event-catalog-v1",
        "artifact_id": artifact_id,
        "catalog_id": "curated-shock-events-v1",
        "catalog_sha256": catalog_sha,
        "schema_sha256": schema_sha,
        "binding": binding,
        "summary": summary,
        "artifacts": [
            {
                "path": "catalog.json",
                "sha256": payload_sha,
                "bytes": len(payload_body),
                "records": 1,
                "media_type": "application/json",
            }
        ],
    }
    _write(artifact / "manifest.json", _canonical(manifest) + b"\n")
    return {
        "repository": repository,
        "catalog": catalog,
        "schema": schema,
        "scenario": scenario,
        "events_root": events_root,
        "artifact": artifact,
        "artifact_id": artifact_id,
    }


def _rewrite_artifact(
    fixture: dict[str, Path | str],
    mutate,
) -> None:
    artifact = Path(fixture["artifact"])
    payload_path = artifact / "catalog.json"
    manifest_path = artifact / "manifest.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    mutate(payload)
    payload_body = _canonical(payload) + b"\n"
    payload_sha = _write(payload_path, payload_body)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary"] = payload["summary"]
    manifest["artifacts"][0].update(
        {
            "sha256": payload_sha,
            "bytes": len(payload_body),
            "records": len(payload["events"]),
        }
    )
    _write(manifest_path, _canonical(manifest) + b"\n")


def test_verifies_source_scenario_and_artifact_then_atomically_binds_current(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt = publish(
        fixture["scenario"],
        fixture["artifact"],
        fixture["events_root"],
        fixture["catalog"],
        fixture["schema"],
    )

    current = fixture["events_root"] / "current"
    assert current.is_symlink()
    assert os.readlink(current) == f"artifacts/{fixture['artifact_id']}"
    assert receipt["status"] == "ready"
    assert receipt["scenario_id"] == Path(fixture["scenario"]).name
    assert receipt["current_target"] == os.readlink(current)
    assert receipt["prior_target"] is None
    assert not list(Path(fixture["events_root"]).glob(".current.next.*"))

    verified = verify_binding(
        fixture["scenario"],
        current,
        fixture["catalog"],
        fixture["schema"],
    )
    assert verified == {key: receipt[key] for key in verified}


@pytest.mark.parametrize("mutation", ["source", "scenario", "artifact"])
def test_binding_fails_closed_on_every_digest_boundary(tmp_path: Path, mutation: str) -> None:
    fixture = _fixture(tmp_path)
    if mutation == "source":
        Path(fixture["catalog"]).write_text("schema_version: 2\n", encoding="utf-8")
    elif mutation == "scenario":
        path = Path(fixture["scenario"]) / "manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        path = Path(fixture["artifact"]) / "catalog.json"
        path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(EventPublicationError, match="event_"):
        verify_binding(
            fixture["scenario"],
            fixture["artifact"],
            fixture["catalog"],
            fixture["schema"],
        )


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda payload: payload.update(categories=[]), "event_categories"),
        (lambda payload: payload["events"][0].pop("title"), "event_contract"),
        (
            lambda payload: payload["summary"].update(
                ready_events=0,
                partial_events=1,
            ),
            "event_summary",
        ),
    ],
)
def test_publication_rejects_runtime_invalid_prepared_catalogs(
    tmp_path: Path,
    mutate,
    code: str,
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_artifact(fixture, mutate)

    with pytest.raises(EventPublicationError, match=code):
        publish(
            fixture["scenario"],
            fixture["artifact"],
            fixture["events_root"],
            fixture["catalog"],
            fixture["schema"],
        )
    current = Path(fixture["events_root"]) / "current"
    assert not current.exists() and not current.is_symlink()


def test_restore_is_compare_and_swap_and_never_overwrites_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt = publish(
        fixture["scenario"],
        fixture["artifact"],
        fixture["events_root"],
        fixture["catalog"],
        fixture["schema"],
    )
    current = fixture["events_root"] / "current"
    with pytest.raises(EventPublicationError, match="event_current_alias_drift"):
        restore(fixture["events_root"], "absent", "artifacts/shock-events-ffffffffffffffff")
    assert os.readlink(current) == receipt["current_target"]

    restore(fixture["events_root"], "absent", receipt["current_target"])
    assert not current.exists() and not current.is_symlink()


def test_publication_rejects_non_symlink_current_without_replacing_it(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    current = Path(fixture["events_root"]) / "current"
    current.mkdir()

    with pytest.raises(EventPublicationError, match="event_current_alias"):
        publish(
            fixture["scenario"],
            fixture["artifact"],
            fixture["events_root"],
            fixture["catalog"],
            fixture["schema"],
        )
    assert current.is_dir() and not current.is_symlink()

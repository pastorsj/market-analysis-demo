from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.data import event_contract
from scripts.spark.event_publication import verify_binding
from tests.unit.test_event_publication import _fixture

READY = {"status": "ready", "gaps": []}
MARKET_GAP = {"code": "market_session_unavailable", "layer": "market", "detail": "No bound session."}
DOCUMENT_GAP = {"code": "document_evidence_unavailable", "layer": "documents", "detail": "No evidence."}


def qualification(*, documents=READY, market=READY):
    gaps = sorted(market["gaps"] + documents["gaps"], key=lambda row: (row["layer"], row["code"]))
    return {
        "status": "partial" if gaps else "ready",
        "market": market,
        "documents": documents,
        "licensed_news": READY,
        "derived_features": READY,
        "gaps": gaps,
    }


def source(tmp_path: Path):
    """The fixture's published catalog, turned back into catalog.yaml form."""
    fixture = _fixture(tmp_path)
    payload = json.loads((Path(fixture["artifact"]) / "catalog.json").read_text())
    event = {key: value for key, value in payload["events"][0].items() if key != "qualification"}
    catalog = {
        "version": 1,
        "catalog_id": payload["catalog_id"],
        "calendar": payload["calendar"],
        "timezone": payload["timezone"],
        "supported_tickers": ["NVDA", "SPY"],
        "categories": payload["categories"],
        "events": [{**event, "known_gaps": []}],
    }
    shas = {
        "catalog_sha256": event_contract.sha256_file(Path(fixture["catalog"])),
        "schema_sha256": event_contract.sha256_file(Path(fixture["schema"])),
    }
    return fixture, catalog, shas, {"binding": payload["binding"]}


def second_event(catalog):
    event = copy.deepcopy(catalog["events"][0])
    event.update(event_id="second-event", sort_order=2)
    for number, question in enumerate(event["questions"], start=1):
        question["question_id"] = f"second-question-{number}"
        question["text"] = f"What does second test question {number} establish about this event?"
    return event


def test_built_artifact_matches_the_published_fixture_byte_for_byte(tmp_path, monkeypatch):
    fixture, catalog, shas, scenario = source(tmp_path)
    monkeypatch.setattr(event_contract, "qualify_event", lambda event, scenario: qualification())
    payload = event_contract.prepare_event_catalog(catalog, scenario=scenario, **shas)
    built = event_contract.write_event_artifact(payload, artifacts_root=tmp_path / "built", **shas)
    assert built.name == fixture["artifact_id"]
    for name in ("catalog.json", "manifest.json"):
        assert (built / name).read_bytes() == (Path(fixture["artifact"]) / name).read_bytes()
    event_contract.validate_event_artifact(built)
    verify_binding(fixture["scenario"], built, fixture["catalog"], fixture["schema"])


def test_market_gaps_exclude_and_other_gaps_publish_as_partial(tmp_path, monkeypatch):
    _, catalog, shas, scenario = source(tmp_path)
    catalog["events"].append(second_event(catalog))
    third = second_event(catalog)
    third.update(event_id="third-event", sort_order=3)
    for question in third["questions"]:
        question["question_id"] = question["question_id"].replace("second", "third")
        question["text"] = question["text"].replace("second", "third")
    catalog["events"].append(third)
    outcomes = {
        "test-event": qualification(),
        "second-event": qualification(documents={"status": "partial", "gaps": [DOCUMENT_GAP]}),
        "third-event": qualification(market={"status": "excluded", "gaps": [MARKET_GAP]}),
    }
    monkeypatch.setattr(event_contract, "qualify_event", lambda event, scenario: outcomes[event["event_id"]])
    payload = event_contract.prepare_event_catalog(catalog, scenario=scenario, **shas)
    assert [event["event_id"] for event in payload["events"]] == ["test-event", "second-event"]
    assert payload["excluded_events"] == [{"event_id": "third-event", "gaps": [MARKET_GAP]}]
    assert payload["summary"] == {
        "declared_events": 3,
        "published_events": 2,
        "ready_events": 1,
        "partial_events": 1,
        "excluded_events": 1,
    }
    assert all("known_gaps" not in event for event in payload["events"])


def test_writing_is_idempotent_and_never_replaces_a_different_artifact(tmp_path, monkeypatch):
    _, catalog, shas, scenario = source(tmp_path)
    monkeypatch.setattr(event_contract, "qualify_event", lambda event, scenario: qualification())
    payload = event_contract.prepare_event_catalog(catalog, scenario=scenario, **shas)
    root = tmp_path / "artifacts"
    first = event_contract.write_event_artifact(payload, artifacts_root=root, **shas)
    assert event_contract.write_event_artifact(payload, artifacts_root=root, **shas) == first
    (first / "catalog.json").write_bytes(b"{}\n")
    with pytest.raises(event_contract.EventContractError):
        event_contract.write_event_artifact(payload, artifacts_root=root, **shas)
    assert not list(root.glob(".*"))

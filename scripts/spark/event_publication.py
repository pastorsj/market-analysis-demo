#!/usr/bin/env python3
"""Verify and atomically publish the scenario-bound shock-event catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.data.event_contract import EventContractError, validate_prepared_catalog


DIGEST = re.compile(r"[0-9a-f]{64}")
ARTIFACT_ID = re.compile(r"shock-events-[0-9a-f]{16}")
MANIFEST_FIELDS = {
    "schema_version",
    "artifact_kind",
    "artifact_id",
    "catalog_id",
    "catalog_sha256",
    "schema_sha256",
    "binding",
    "summary",
    "artifacts",
}
BINDING_FIELDS = {
    "scenario_id",
    "scenario_manifest_sha256",
    "market_snapshot_id",
    "market_manifest_sha256",
    "document_snapshot_id",
    "document_manifest_sha256",
}
SUMMARY_FIELDS = {
    "declared_events",
    "published_events",
    "ready_events",
    "partial_events",
    "excluded_events",
}
CATALOG_FIELDS = {
    "schema_version",
    "artifact_id",
    "catalog_id",
    "calendar",
    "timezone",
    "binding",
    "categories",
    "events",
    "excluded_events",
    "summary",
}


class EventPublicationError(ValueError):
    """A stable, non-secret event publication contract failure."""


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise EventPublicationError(code)


def _stable_bytes(path: Path, maximum: int, code: str) -> bytes:
    try:
        before = path.lstat()
        _need(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= maximum,
            code,
        )
        body = path.read_bytes()
        after = path.lstat()
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        _need(len(body) == before.st_size and identity(before) == identity(after), code)
        return body
    except OSError as exc:
        raise EventPublicationError(code) from exc


def _json(body: bytes, code: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            _need(key not in value, code)
            value[key] = item
        return value

    try:
        value = json.loads(body, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EventPublicationError(code) from exc
    _need(isinstance(value, dict), code)
    return value


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _scenario_binding(scenario_root: Path) -> dict[str, str]:
    try:
        root = scenario_root.resolve(strict=True)
    except OSError as exc:
        raise EventPublicationError("event_scenario_root") from exc
    _need(root.is_dir(), "event_scenario_root")
    body = _stable_bytes(root / "manifest.json", 16 * 1024 * 1024, "event_scenario_manifest")
    manifest = _json(body, "event_scenario_manifest")
    market = manifest.get("market")
    documents = manifest.get("documents")
    _need(isinstance(market, dict) and isinstance(documents, dict), "event_scenario_manifest")
    binding = {
        "scenario_id": manifest.get("scenario_id"),
        "scenario_manifest_sha256": _sha256(body),
        "market_snapshot_id": market.get("snapshot_id"),
        "market_manifest_sha256": market.get("manifest_sha256"),
        "document_snapshot_id": documents.get("snapshot_id"),
        "document_manifest_sha256": documents.get("manifest_sha256"),
    }
    _need(
        all(isinstance(value, str) and value for value in binding.values()),
        "event_scenario_binding",
    )
    _need(
        all(
            DIGEST.fullmatch(binding[key])
            for key in (
                "scenario_manifest_sha256",
                "market_manifest_sha256",
                "document_manifest_sha256",
            )
        ),
        "event_scenario_binding",
    )
    return binding


def verify_binding(
    scenario_root: Path,
    event_root: Path,
    catalog_path: Path,
    schema_path: Path,
) -> dict[str, Any]:
    """Return a stable receipt only when sources, scenario, and artifact agree."""
    binding = _scenario_binding(scenario_root)
    catalog_sha = _sha256(_stable_bytes(catalog_path, 8 * 1024 * 1024, "event_source_catalog"))
    schema_sha = _sha256(_stable_bytes(schema_path, 2 * 1024 * 1024, "event_source_schema"))
    try:
        root = event_root.resolve(strict=True)
    except OSError as exc:
        raise EventPublicationError("event_artifact_root") from exc
    _need(root.is_dir() and ARTIFACT_ID.fullmatch(root.name) is not None, "event_artifact_root")

    manifest_body = _stable_bytes(root / "manifest.json", 512 * 1024, "event_artifact_manifest")
    manifest = _json(manifest_body, "event_artifact_manifest")
    _need(set(manifest) == MANIFEST_FIELDS, "event_artifact_manifest")
    _need(
        manifest.get("schema_version") == 1
        and manifest.get("artifact_kind") == "shock-event-catalog-v1"
        and manifest.get("artifact_id") == root.name
        and manifest.get("catalog_sha256") == catalog_sha
        and manifest.get("schema_sha256") == schema_sha,
        "event_artifact_identity",
    )
    _need(manifest.get("binding") == binding, "event_scenario_binding")
    _need(set(manifest["binding"]) == BINDING_FIELDS, "event_scenario_binding")
    summary = manifest.get("summary")
    _need(isinstance(summary, dict) and set(summary) == SUMMARY_FIELDS, "event_summary")
    _need(
        all(type(value) is int and value >= 0 for value in summary.values())
        and summary["published_events"] <= summary["declared_events"]
        and summary["published_events"] + summary["excluded_events"] == summary["declared_events"]
        and summary["ready_events"] + summary["partial_events"] == summary["published_events"],
        "event_summary",
    )
    artifacts = manifest.get("artifacts")
    _need(isinstance(artifacts, list) and len(artifacts) == 1, "event_artifact_record")
    record = artifacts[0]
    _need(
        isinstance(record, dict)
        and set(record)
        == {
            "path",
            "sha256",
            "bytes",
            "records",
            "media_type",
        },
        "event_artifact_record",
    )
    _need(
        record.get("path") == "catalog.json"
        and DIGEST.fullmatch(str(record.get("sha256", ""))) is not None
        and type(record.get("bytes")) is int
        and record["bytes"] > 0
        and type(record.get("records")) is int
        and record["records"] >= 0
        and record.get("media_type") == "application/json",
        "event_artifact_record",
    )
    payload_body = _stable_bytes(root / "catalog.json", 8 * 1024 * 1024, "event_artifact_catalog")
    payload = _json(payload_body, "event_artifact_catalog")
    _need(
        len(payload_body) == record["bytes"] and _sha256(payload_body) == record["sha256"],
        "event_artifact_digest",
    )
    _need(set(payload) == CATALOG_FIELDS, "event_artifact_catalog")
    try:
        validate_prepared_catalog(payload)
    except EventContractError as exc:
        code = exc.issues[0].code if exc.issues else "event_artifact_catalog"
        raise EventPublicationError(code) from exc
    expected_id = (
        "shock-events-"
        + _sha256(
            _canonical(
                {
                    "catalog_sha256": catalog_sha,
                    "schema_sha256": schema_sha,
                    "binding": binding,
                }
            )
        )[:16]
    )
    _need(
        expected_id == root.name == payload.get("artifact_id")
        and payload.get("binding") == binding
        and payload.get("summary") == summary
        and payload.get("catalog_id") == manifest.get("catalog_id")
        and isinstance(payload.get("events"), list)
        and len(payload["events"]) == record["records"] == summary["published_events"]
        and isinstance(payload.get("excluded_events"), list)
        and len(payload["excluded_events"]) == summary["excluded_events"],
        "event_artifact_projection",
    )
    return {
        "schema_version": "spark-event-binding-v1",
        "artifact_id": expected_id,
        "catalog_sha256": catalog_sha,
        "schema_sha256": schema_sha,
        "scenario_id": binding["scenario_id"],
        "scenario_manifest_sha256": binding["scenario_manifest_sha256"],
        "manifest_sha256": _sha256(manifest_body),
        "catalog_artifact_sha256": record["sha256"],
        "status": "ready",
    }


def _target(events_root: Path, artifact: Path) -> str:
    try:
        root = events_root.resolve(strict=True)
        resolved = artifact.resolve(strict=True)
        expected_parent = (root / "artifacts").resolve(strict=True)
    except OSError as exc:
        raise EventPublicationError("event_publication_path") from exc
    _need(
        not events_root.is_symlink()
        and root.is_dir()
        and resolved.is_dir()
        and not artifact.is_symlink()
        and resolved.parent == expected_parent
        and ARTIFACT_ID.fullmatch(resolved.name) is not None,
        "event_publication_path",
    )
    return f"artifacts/{resolved.name}"


def _read_current(events_root: Path) -> str | None:
    current = events_root / "current"
    if not current.exists() and not current.is_symlink():
        return None
    _need(current.is_symlink(), "event_current_alias")
    target = os.readlink(current)
    parts = Path(target).parts
    _need(
        len(parts) == 2 and parts[0] == "artifacts" and ARTIFACT_ID.fullmatch(parts[1]) is not None,
        "event_current_alias",
    )
    return target


def _replace_alias(events_root: Path, target: str | None) -> None:
    current = events_root / "current"
    descriptor, temporary_text = tempfile.mkstemp(prefix=".current.next.", dir=events_root)
    os.close(descriptor)
    temporary = Path(temporary_text)
    try:
        temporary.unlink()
        if target is None:
            current.unlink(missing_ok=True)
        else:
            os.symlink(target, temporary)
            os.replace(temporary, current)
        directory = os.open(events_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def publish(
    scenario_root: Path,
    artifact: Path,
    events_root: Path,
    catalog_path: Path,
    schema_path: Path,
) -> dict[str, Any]:
    receipt = verify_binding(scenario_root, artifact, catalog_path, schema_path)
    target = _target(events_root, artifact)
    prior = _read_current(events_root)
    _replace_alias(events_root, target)
    _need(_read_current(events_root) == target, "event_current_alias")
    return {**receipt, "current_target": target, "prior_target": prior}


def restore(events_root: Path, prior: str, expected_current: str) -> None:
    _need(_read_current(events_root) == expected_current, "event_current_alias_drift")
    target = None if prior == "absent" else prior
    if target is not None:
        parts = Path(target).parts
        _need(
            len(parts) == 2
            and parts[0] == "artifacts"
            and ARTIFACT_ID.fullmatch(parts[1]) is not None
            and (events_root / target).resolve(strict=True).parent
            == (events_root / "artifacts").resolve(strict=True),
            "event_restore_target",
        )
    _replace_alias(events_root, target)


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    repository = args.repository_root.resolve()
    return (
        repository / "data/events/catalog.yaml",
        repository / "data/schemas/shock-event-catalog.schema.json",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "publish"):
        command = subparsers.add_parser(name)
        command.add_argument("--repository-root", type=Path, required=True)
        command.add_argument("--scenario-root", type=Path, required=True)
        command.add_argument("--event-root" if name == "verify" else "--artifact", type=Path, required=True)
        if name == "publish":
            command.add_argument("--events-root", type=Path, required=True)
    command = subparsers.add_parser("restore")
    command.add_argument("--events-root", type=Path, required=True)
    command.add_argument("--prior-target", required=True)
    command.add_argument("--expected-current", required=True)
    args = parser.parse_args()
    try:
        if args.command == "restore":
            restore(args.events_root, args.prior_target, args.expected_current)
            return 0
        catalog, schema = _paths(args)
        if args.command == "verify":
            receipt = verify_binding(args.scenario_root, args.event_root, catalog, schema)
        else:
            receipt = publish(
                args.scenario_root,
                args.artifact,
                args.events_root,
                catalog,
                schema,
            )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except (EventPublicationError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-closed contract for point-in-time document metadata and summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from scripts.data.market_contract import (
    ContractIssue, MarketContractError, content_id, parse_date, parse_utc, sha256_bytes,
)


SOURCE_KINDS = frozenset({"filing", "company_release", "primary_source", "licensed_news_metadata"})
VINTAGES = frozenset({"archived_at_cutoff", "reconstructed_later", "unknown"})
SEC_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})
MIGRATION_KEYS = frozenset({
    "mode", "source_capture_id", "source_manifest_sha256", "publication_receipt_sha256",
    "scenario_id", "scenario_manifest_sha256", "document_snapshot_id", "document_manifest_sha256",
})
DOC_CAPTURE_ID = re.compile(r"^doccapture-[0-9a-f]{16}$")
SCENARIO_ID = re.compile(r"^market-shock-v2-[0-9a-f]{16}$")
DOCUMENT_SNAPSHOT_ID = re.compile(r"^documents-[0-9a-f]{16}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEC_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
PROOF_KINDS = frozenset({"sec_submission_acceptance", "q4_press_release_feed", "official_body_date"})


class DocumentFileError(OSError):
    """A path was not a stable, singly-linked regular file tree."""


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_plain_directories(root: Path, parent: Path) -> None:
    root = _absolute_without_resolving(root)
    parent = _absolute_without_resolving(parent)
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise DocumentFileError("path escapes declared root") from exc
    current = root
    for part in ((), *relative.parts):
        if part:
            current /= part
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise DocumentFileError("directory is unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise DocumentFileError("directory is not a plain directory")


def read_regular_bytes(path: Path, *, root: Path | None = None) -> bytes:
    """Read one stable view of a regular file without following links.

    The returned bytes are the only view callers should hash and parse.  A
    second hard link is rejected because it provides an undeclared mutation
    path outside the validated tree.
    """
    path = _absolute_without_resolving(path)
    declared_root = _absolute_without_resolving(root or path.parent)
    _require_plain_directories(declared_root, path.parent)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise DocumentFileError("O_NOFOLLOW is unavailable")
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError as exc:
        raise DocumentFileError("file cannot be opened without following links") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise DocumentFileError("file is not a singly-linked regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        body = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields) or len(body) != before.st_size:
        raise DocumentFileError("file changed while it was read")
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise DocumentFileError("file path changed after it was read") from exc
    if (
        stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1 or current.st_dev != before.st_dev or current.st_ino != before.st_ino
    ):
        raise DocumentFileError("file path changed after it was read")
    return body


def read_regular_tree(root: Path) -> dict[str, bytes]:
    """Capture an exact, stable byte inventory for a plain directory tree."""
    root = _absolute_without_resolving(root)
    _require_plain_directories(root, root)
    def walk_error(error: OSError) -> None:
        raise DocumentFileError("tree cannot be enumerated") from error

    paths: list[tuple[str, Path]] = []
    for directory, names, files in os.walk(root, topdown=True, onerror=walk_error, followlinks=False):
        base = Path(directory)
        for name in sorted(names):
            metadata = os.lstat(base / name)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise DocumentFileError("tree contains a non-directory or linked directory")
        for name in sorted(files):
            path = base / name
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise DocumentFileError("tree contains a linked or non-regular file")
            paths.append((path.relative_to(root).as_posix(), path))
    result: dict[str, bytes] = {}
    signatures: dict[str, tuple[int, ...]] = {}
    signature_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    for relative, path in paths:
        result[relative] = read_regular_bytes(path, root=root)
        metadata = os.lstat(path)
        signatures[relative] = tuple(getattr(metadata, field) for field in signature_fields)
    observed: set[str] = set()
    for directory, names, files in os.walk(root, topdown=True, onerror=walk_error, followlinks=False):
        base = Path(directory)
        if any((base / name).is_symlink() for name in names):
            raise DocumentFileError("tree changed while it was read")
        for name in files:
            path = base / name
            relative = path.relative_to(root).as_posix()
            metadata = os.lstat(path)
            current = tuple(getattr(metadata, field) for field in signature_fields)
            if stat.S_ISLNK(metadata.st_mode) or signatures.get(relative) != current:
                raise DocumentFileError("tree changed while it was read")
            observed.add(relative)
    if observed != set(result):
        raise DocumentFileError("tree inventory changed while it was read")
    return result


@dataclass(frozen=True)
class DocumentIssue:
    code: str
    detail: str


class DocumentContractError(ValueError):
    def __init__(self, issues: Iterable[DocumentIssue | ContractIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{item.code}: {item.detail}" for item in self.issues))


def _strings(value: Any, *, minimum: int = 0) -> bool:
    return (
        isinstance(value, list) and len(value) >= minimum
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def parse_document_corpus(body: bytes) -> dict[str, Any]:
    """Parse the exact captured corpus bytes and enforce its closed schema."""
    try:
        corpus = yaml.safe_load(body)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise DocumentContractError([DocumentIssue("corpus_contract", "YAML")]) from exc
    allowed_top = {
        "version", "issuers", "sec", "q4_feed", "company_releases",
        "requirements", "licensed_news", "public_authorities",
    }
    if (
        not isinstance(corpus, dict) or not {"version", "issuers", "requirements"} <= set(corpus)
        or not set(corpus) <= allowed_top or type(corpus.get("version")) is not int
        or corpus["version"] != 1
    ):
        raise DocumentContractError([DocumentIssue("corpus_contract", "root")])
    issuers = corpus.get("issuers")
    if not isinstance(issuers, dict) or set(issuers) != {"NVDA", "AMD", "JPM", "GS", "SCHW"}:
        raise DocumentContractError([DocumentIssue("corpus_contract", "issuers")])
    for issuer_id, issuer in issuers.items():
        if (
            not isinstance(issuer, dict) or set(issuer) != {"cik", "official_hosts"}
            or not isinstance(issuer.get("cik"), str) or re.fullmatch(r"[0-9]{10}", issuer["cik"]) is None
            or not _strings(issuer.get("official_hosts"), minimum=1)
            or any("/" in host or ":" in host for host in issuer["official_hosts"])
        ):
            raise DocumentContractError([DocumentIssue("corpus_contract", f"issuer:{issuer_id}")])
    authorities = corpus.get("public_authorities", {})
    if not isinstance(authorities, dict):
        raise DocumentContractError([DocumentIssue("corpus_contract", "public_authorities")])
    for authority_id, authority in authorities.items():
        if (
            not isinstance(authority_id, str) or re.fullmatch(r"[A-Z][A-Z0-9_-]{1,31}", authority_id) is None
            or not isinstance(authority, dict) or set(authority) != {"official_hosts"}
            or not _strings(authority.get("official_hosts"), minimum=1)
            or any("/" in host or ":" in host for host in authority["official_hosts"])
        ):
            raise DocumentContractError([DocumentIssue("corpus_contract", f"authority:{authority_id}")])
    sec = corpus.get("sec")
    if sec is not None:
        expected = {
            "submissions_url_template", "submissions_archive_url_template", "archive_url_template",
            "forms", "start", "end_exclusive",
        }
        if (
            not isinstance(sec, dict) or set(sec) != expected
            or not all(isinstance(sec.get(key), str) and sec[key] for key in expected - {"forms"})
            or not _strings(sec.get("forms"), minimum=1)
        ):
            raise DocumentContractError([DocumentIssue("corpus_contract", "sec")])
        try:
            if parse_date(sec["start"]) >= parse_date(sec["end_exclusive"]):
                raise DocumentContractError([DocumentIssue("corpus_contract", "sec window")])
        except MarketContractError as exc:
            raise DocumentContractError([DocumentIssue("corpus_contract", "sec window")]) from exc
    q4 = corpus.get("q4_feed")
    if q4 is not None and (
        not isinstance(q4, dict)
        or set(q4) != {"url", "public_api_key", "language_id", "category_id"}
        or not all(isinstance(q4.get(key), str) and q4[key] for key in ("url", "public_api_key", "category_id"))
        or type(q4.get("language_id")) is not int
    ):
        raise DocumentContractError([DocumentIssue("corpus_contract", "q4_feed")])
    releases = corpus.get("company_releases", [])
    if not isinstance(releases, list):
        raise DocumentContractError([DocumentIssue("corpus_contract", "company_releases")])
    release_required = {
        "source_id", "issuer_id", "event_id", "canonical_url", "title", "publication_proof",
        "summary", "license_id", "redistribution",
    }
    for index, release in enumerate(releases):
        if (
            not isinstance(release, dict) or not release_required <= set(release)
            or not set(release) <= release_required | {"source_kind", "source_authority_id"}
            or not all(isinstance(release.get(key), str) and release[key] for key in release_required - {"publication_proof"})
            or release.get("issuer_id") not in issuers
            or ("source_kind" in release and not isinstance(release["source_kind"], str))
        ):
            raise DocumentContractError([DocumentIssue("corpus_contract", f"release:{index}")])
        authority_id = release.get("source_authority_id")
        if authority_id is not None:
            parsed = urlparse(release["canonical_url"])
            if (
                not isinstance(authority_id, str) or authority_id not in authorities
                or release.get("source_kind") != "primary_source"
                or parsed.scheme != "https" or parsed.hostname not in authorities[authority_id]["official_hosts"]
                or parsed.username or parsed.password or parsed.fragment
            ):
                raise DocumentContractError([DocumentIssue("corpus_contract", f"authority_release:{index}")])
        proof = release.get("publication_proof")
        if not isinstance(proof, dict) or not isinstance(proof.get("kind"), str):
            raise DocumentContractError([DocumentIssue("corpus_contract", f"proof:{index}")])
        kind = proof["kind"]
        shapes = {
            "official_body_date": {"kind", "date", "body_markers"},
            "sec_submission_acceptance": {"kind", "accession", "document", "document_role", "body_markers"},
            "q4_press_release_feed": {
                "kind", "year", "press_release_id", "revision_number", "workflow_id",
                "timezone", "headline", "link_path", "body_markers",
            },
        }
        if kind not in shapes or set(proof) != shapes[kind] or not _strings(proof.get("body_markers"), minimum=2):
            raise DocumentContractError([DocumentIssue("corpus_contract", f"proof:{index}")])
        if kind == "official_body_date":
            if not isinstance(proof.get("date"), str):
                raise DocumentContractError([DocumentIssue("corpus_contract", f"proof:{index}")])
            try:
                parse_date(proof["date"])
            except MarketContractError as exc:
                raise DocumentContractError([DocumentIssue("corpus_contract", f"proof:{index}")]) from exc
        elif kind == "sec_submission_acceptance":
            if (
                not isinstance(proof.get("accession"), str) or SEC_ACCESSION.fullmatch(proof["accession"]) is None
                or not isinstance(proof.get("document"), str) or not proof["document"]
                or proof.get("document_role") not in {"primary", "accession_document"}
            ):
                raise DocumentContractError([DocumentIssue("corpus_contract", f"proof:{index}")])
        elif (
            type(proof.get("year")) is not int or type(proof.get("press_release_id")) is not int
            or type(proof.get("revision_number")) is not int
            or not all(isinstance(proof.get(key), str) and proof[key] for key in (
                "workflow_id", "timezone", "headline", "link_path",
            ))
        ):
            raise DocumentContractError([DocumentIssue("corpus_contract", f"proof:{index}")])
    requirements = corpus.get("requirements")
    if not isinstance(requirements, list):
        raise DocumentContractError([DocumentIssue("corpus_contract", "requirements")])
    required_requirement = {"requirement_id", "issuer_id", "event_id", "source_kinds", "cutoff"}
    for index, requirement in enumerate(requirements):
        if (
            not isinstance(requirement, dict) or not required_requirement <= set(requirement)
            or not set(requirement) <= required_requirement | {"filing_summary"}
            or not all(isinstance(requirement.get(key), str) and requirement[key] for key in required_requirement - {"source_kinds"})
            or requirement.get("issuer_id") not in issuers
            or not _strings(requirement.get("source_kinds"), minimum=1)
            or ("filing_summary" in requirement and not isinstance(requirement["filing_summary"], str))
        ):
            raise DocumentContractError([DocumentIssue("corpus_contract", f"requirement:{index}")])
        try:
            parse_utc(requirement["cutoff"], "cutoff")
        except MarketContractError as exc:
            raise DocumentContractError([DocumentIssue("corpus_contract", f"requirement:{index}")]) from exc
    licensed = corpus.get("licensed_news")
    if licensed is not None and (
        not isinstance(licensed, dict) or set(licensed) != {"required_for_release", "absent_gap", "mapping"}
        or type(licensed.get("required_for_release")) is not bool
        or not isinstance(licensed.get("absent_gap"), str) or not isinstance(licensed.get("mapping"), str)
    ):
        raise DocumentContractError([DocumentIssue("corpus_contract", "licensed_news")])
    return corpus


def valid_document_migration(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == MIGRATION_KEYS
        and value.get("mode") == "verified_published_v5_migration"
        and isinstance(value.get("source_capture_id"), str)
        and DOC_CAPTURE_ID.fullmatch(value["source_capture_id"]) is not None
        and isinstance(value.get("scenario_id"), str)
        and SCENARIO_ID.fullmatch(value["scenario_id"]) is not None
        and isinstance(value.get("document_snapshot_id"), str)
        and DOCUMENT_SNAPSHOT_ID.fullmatch(value["document_snapshot_id"]) is not None
        and all(
            isinstance(value.get(key), str) and SHA256.fullmatch(value[key]) is not None
            for key in (
                "source_manifest_sha256", "publication_receipt_sha256",
                "scenario_manifest_sha256", "document_manifest_sha256",
            )
        )
    )


def _allowed_host(url: str, issuer: str, issuers: dict[str, Any]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        return False
    allowed = SEC_HOSTS | frozenset(issuers.get(issuer, {}).get("official_hosts", []))
    return parsed.hostname in allowed


def _authority_host(url: str, identity: dict[str, Any]) -> bool:
    authority_id = identity.get("source_authority_id")
    hosts = identity.get("source_authority_hosts")
    if authority_id is None and hosts is None:
        return False
    parsed = urlparse(url)
    return (
        isinstance(authority_id, str)
        and re.fullmatch(r"[A-Z][A-Z0-9_-]{1,31}", authority_id) is not None
        and _strings(hosts, minimum=1)
        and parsed.scheme == "https" and parsed.hostname in hosts
        and not parsed.username and not parsed.password and not parsed.fragment
    )


def _proof_identity(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("publication_proof_identity")
    if not isinstance(value, str):
        raise DocumentContractError([DocumentIssue("publication_proof_shape", str(row.get("evidence_id")))])
    try:
        identity = json.loads(value)
    except json.JSONDecodeError as exc:
        raise DocumentContractError([DocumentIssue("publication_proof_shape", str(row.get("evidence_id")))]) from exc
    if not isinstance(identity, dict):
        raise DocumentContractError([DocumentIssue("publication_proof_shape", str(row.get("evidence_id")))])
    return identity


def _body_has_markers(body: bytes | None, markers: Any) -> bool:
    if (
        body is None or not isinstance(markers, list) or len(markers) < 2
        or any(not isinstance(marker, str) or not marker.strip() for marker in markers)
    ):
        return False
    text = body.decode("utf-8", errors="ignore").casefold()
    return all(marker.casefold() in text for marker in markers)


def _sec_recent(payload: dict[str, Any]) -> dict[str, Any]:
    recent = payload.get("filings", {}).get("recent", payload)
    fields = ("accessionNumber", "filingDate", "acceptanceDateTime", "reportDate", "form", "primaryDocument")
    if not isinstance(recent, dict) or any(not isinstance(recent.get(name), list) for name in fields):
        raise DocumentContractError([DocumentIssue("publication_proof_identity", "SEC schema")])
    if len({len(recent[name]) for name in fields}) != 1:
        raise DocumentContractError([DocumentIssue("publication_proof_identity", "SEC columns")])
    if any(any(not isinstance(value, str) for value in recent[name]) for name in fields):
        raise DocumentContractError([DocumentIssue("publication_proof_identity", "SEC scalar types")])
    return recent


def _sec_accepted_at(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise DocumentContractError([DocumentIssue("publication_proof_identity", "SEC acceptance")])
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            parsed = parsed.astimezone(timezone.utc)
        else:
            # Legacy SEC submissions archives use compact UTC acceptance timestamps.
            parsed = datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise DocumentContractError([DocumentIssue("publication_proof_identity", "SEC acceptance")]) from exc
    return parsed.isoformat().replace("+00:00", "Z")


def validate_publication_proof(
    row: dict[str, Any], proof_bytes: bytes, source_bytes: bytes | None = None,
    index_bytes: bytes | None = None, issuer_bytes: bytes | None = None,
    *, enforce_declared: bool = True,
) -> str:
    """Derive publication time and identity from immutable third-party bytes.

    The declared ``published_at`` is compared only after the timestamp has been
    reconstructed from the captured SEC/issuer payload.  This function is used
    both while acquiring a source and again against copied snapshot artifacts.
    """
    kind = row.get("publication_proof_kind")
    identity = _proof_identity(row)
    evidence_id = str(row.get("evidence_id", "unknown"))
    parsed_url = urlparse(str(row.get("canonical_url", "")))
    derived: str
    expected_precision: str
    if kind == "sec_submission_acceptance":
        expected_keys = {
            "accession", "document", "document_role", "issuer_name", "submission_file", "body_markers",
        }
        accession, document = identity.get("accession"), identity.get("document")
        if (
            set(identity) != expected_keys or not isinstance(accession, str)
            or SEC_ACCESSION.fullmatch(accession) is None or not isinstance(document, str)
            or not document or "/" in document or ".." in document
            or identity.get("document_role") not in {"primary", "accession_document"}
        ):
            raise DocumentContractError([DocumentIssue("publication_proof_shape", evidence_id)])
        try:
            payload = json.loads(proof_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)]) from exc
        recent = _sec_recent(payload)
        matches = [index for index, value in enumerate(recent["accessionNumber"]) if value == accession]
        if len(matches) != 1:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        index = matches[0]
        expected_cik = str(row.get("cik", "")).zfill(10)
        try:
            issuer_payload = json.loads(issuer_bytes) if issuer_bytes is not None else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DocumentContractError([DocumentIssue("publication_proof_issuer_identity", evidence_id)]) from exc
        payload_cik = issuer_payload.get("cik")
        payload_name = issuer_payload.get("name")
        if (
            isinstance(payload_cik, bool) or not isinstance(payload_cik, (str, int))
            or str(payload_cik).zfill(10) != expected_cik or not isinstance(payload_name, str)
            or payload_name != identity.get("issuer_name")
        ):
            raise DocumentContractError([DocumentIssue("publication_proof_issuer_identity", evidence_id)])
        submission_file = identity.get("submission_file")
        if submission_file is None:
            if issuer_bytes != proof_bytes:
                raise DocumentContractError([DocumentIssue("publication_proof_issuer_identity", evidence_id)])
        elif not isinstance(submission_file, str) or "/" in submission_file or ".." in submission_file:
            raise DocumentContractError([DocumentIssue("publication_proof_shape", evidence_id)])
        else:
            files = issuer_payload.get("filings", {}).get("files", [])
            if not isinstance(files, list) or sum(
                isinstance(item, dict) and item.get("name") == submission_file for item in files
            ) != 1:
                raise DocumentContractError([DocumentIssue("publication_proof_issuer_identity", evidence_id)])
        proof_cik = payload.get("cik")
        if proof_cik is not None and (
            isinstance(proof_cik, bool) or not isinstance(proof_cik, (str, int))
            or str(proof_cik).zfill(10) != expected_cik
        ):
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        compact = accession.replace("-", "")
        try:
            cik_int = int(str(row.get("cik")))
        except (TypeError, ValueError) as exc:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)]) from exc
        expected_path = f"/Archives/edgar/data/{cik_int}/{compact}/{document}"
        if parsed_url.scheme != "https" or parsed_url.hostname != "www.sec.gov" or parsed_url.path != expected_path \
                or parsed_url.query or parsed_url.fragment:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        markers = identity["body_markers"]
        primary = recent["primaryDocument"][index]
        if markers:
            if not _body_has_markers(source_bytes, markers):
                raise DocumentContractError([DocumentIssue("publication_proof_source_identity", evidence_id)])
        elif document != primary:
            raise DocumentContractError([DocumentIssue("publication_proof_source_identity", evidence_id)])
        if identity["document_role"] == "primary":
            if document != primary or index_bytes is not None:
                raise DocumentContractError([DocumentIssue("publication_proof_source_identity", evidence_id)])
        else:
            try:
                filing_index = json.loads(index_bytes) if index_bytes is not None else {}
                directory = filing_index["directory"]
                items = directory["item"]
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise DocumentContractError([DocumentIssue("publication_proof_index_identity", evidence_id)]) from exc
            expected_parent = f"/Archives/edgar/data/{cik_int}"
            expected_directory = f"{expected_parent}/{compact}"
            typed_items = (
                isinstance(items, list) and all(
                    isinstance(item, dict) and set(item) == {"last-modified", "name", "type", "size"}
                    and all(isinstance(item.get(key), str) for key in item)
                    for item in items
                )
            )
            if (
                not isinstance(filing_index, dict) or set(filing_index) != {"directory"}
                or not isinstance(directory, dict) or set(directory) != {"item", "name", "parent-dir"}
                or directory.get("name") != expected_directory or directory.get("parent-dir") != expected_parent
                or not typed_items or sum(item["name"] == document for item in items) != 1
            ):
                raise DocumentContractError([DocumentIssue("publication_proof_index_identity", evidence_id)])
        derived = _sec_accepted_at(recent["acceptanceDateTime"][index])
        expected_precision = "second"
    elif kind == "q4_press_release_feed":
        expected_keys = {
            "feed_url", "year", "press_release_id", "revision_number", "workflow_id", "timezone",
            "headline", "link_path", "body_markers",
        }
        if set(identity) != expected_keys or type(identity.get("year")) is not int \
                or type(identity.get("press_release_id")) is not int \
                or type(identity.get("revision_number")) is not int:
            raise DocumentContractError([DocumentIssue("publication_proof_shape", evidence_id)])
        if (
            identity.get("feed_url") != "https://pressroom.aboutschwab.com/feed/PressRelease.svc/GetPressReleaseList"
            or identity.get("timezone") != "America/New_York"
            or not all(isinstance(identity.get(key), str) and identity[key] for key in ("workflow_id", "headline", "link_path"))
            or f"/press-release/{identity.get('year')}/" not in str(identity.get("link_path"))
        ):
            raise DocumentContractError([DocumentIssue("publication_proof_shape", evidence_id)])
        try:
            payload = json.loads(proof_bytes)
            values = payload["GetPressReleaseListResult"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)]) from exc
        if not isinstance(values, list):
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        required_item = {
            "PressReleaseId": int, "RevisionNumber": int, "WorkflowId": str,
            "Headline": str, "LinkToDetailPage": str, "PressReleaseDate": str,
        }
        if any(
            not isinstance(item, dict) or any(type(item.get(key)) is not expected for key, expected in required_item.items())
            for item in values
        ):
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        matches = [item for item in values if item["PressReleaseId"] == identity["press_release_id"]]
        if len(matches) != 1:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        item = matches[0]
        if any(item.get(source) != identity[target] for source, target in (
            ("RevisionNumber", "revision_number"), ("WorkflowId", "workflow_id"),
            ("Headline", "headline"), ("LinkToDetailPage", "link_path"),
        )):
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        if parsed_url.scheme != "https" or parsed_url.hostname != "pressroom.aboutschwab.com" \
                or parsed_url.path.rstrip("/") != identity["link_path"].rstrip("/") or parsed_url.query or parsed_url.fragment:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)])
        if not _body_has_markers(source_bytes, identity["body_markers"]):
            raise DocumentContractError([DocumentIssue("publication_proof_source_identity", evidence_id)])
        try:
            local = datetime.strptime(item["PressReleaseDate"], "%m/%d/%Y %H:%M:%S")
            if local.year != identity["year"]:
                raise ValueError("feed year does not match proof identity")
            derived = local.replace(tzinfo=ZoneInfo(identity["timezone"])).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise DocumentContractError([DocumentIssue("publication_proof_identity", evidence_id)]) from exc
        expected_precision = "second"
    elif kind == "official_body_date":
        expected_keys = {"date", "body_markers"}
        if "source_authority_id" in identity or "source_authority_hosts" in identity:
            expected_keys |= {"source_authority_id", "source_authority_hosts"}
        if (
            set(identity) != expected_keys
            or (
                "source_authority_id" in identity
                and not _authority_host(str(row.get("canonical_url", "")), identity)
            )
            or row.get("publication_proof_artifact") != row.get("capture_artifact")
            or row.get("publication_proof_sha256") != row.get("source_sha256")
            or source_bytes is None or proof_bytes != source_bytes
            or not _body_has_markers(proof_bytes, identity.get("body_markers"))
        ):
            raise DocumentContractError([DocumentIssue("publication_proof_source_identity", evidence_id)])
        try:
            day = parse_date(identity["date"], "publication_proof.date")
        except (KeyError, TypeError, MarketContractError) as exc:
            raise DocumentContractError([DocumentIssue("publication_proof_shape", evidence_id)]) from exc
        derived = datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        expected_precision = "date"
    else:
        raise DocumentContractError([DocumentIssue("publication_proof_kind", evidence_id)])
    if enforce_declared and (
        row.get("published_at") != derived or row.get("published_at_precision") != expected_precision
    ):
        raise DocumentContractError([DocumentIssue("publication_proof_time", evidence_id)])
    return derived


def validate_document_row(row: dict[str, Any], issuers: dict[str, Any]) -> None:
    required = {
        "evidence_id", "issuer_id", "cik", "event_id", "source_kind", "form", "title",
        "canonical_url", "published_at", "published_at_precision", "source_revised_at", "captured_at", "vintage_status",
        "license_id", "redistribution", "content_scope", "summary", "summary_sha256", "source_sha256", "capture_id", "capture_artifact",
        "publication_proof_kind", "publication_proof_artifact", "publication_proof_sha256", "publication_proof_identity",
        "publication_index_artifact", "publication_index_sha256",
        "publication_issuer_artifact", "publication_issuer_sha256",
    }
    issues: list[DocumentIssue | ContractIssue] = []
    if not required <= set(row) <= required | {"available_at"}:
        issues.append(DocumentIssue("document_shape", str(row.get("evidence_id", "unknown"))))
    required_strings = {
        "evidence_id", "issuer_id", "cik", "event_id", "source_kind", "title", "canonical_url",
        "published_at", "published_at_precision", "captured_at", "vintage_status", "license_id",
        "redistribution", "content_scope", "summary", "summary_sha256", "source_sha256", "capture_id",
    }
    nullable_strings = {
        "form", "source_revised_at", "capture_artifact", "publication_proof_kind",
        "publication_proof_artifact", "publication_proof_sha256", "publication_proof_identity",
        "publication_index_artifact", "publication_index_sha256",
        "publication_issuer_artifact", "publication_issuer_sha256",
    }
    if "available_at" in row:
        required_strings.add("available_at")
    if any(not isinstance(row.get(name), str) or not row[name] for name in required_strings) or any(
        row.get(name) is not None and not isinstance(row.get(name), str) for name in nullable_strings
    ):
        issues.append(DocumentIssue("document_scalar_type", str(row.get("evidence_id", "unknown"))))
    issuer = str(row.get("issuer_id", ""))
    if issuer not in issuers or str(row.get("cik")) != str(issuers.get(issuer, {}).get("cik")):
        issues.append(DocumentIssue("issuer_identity", issuer))
    if row.get("source_kind") not in SOURCE_KINDS:
        issues.append(DocumentIssue("source_kind", str(row.get("source_kind"))))
    proof_identity: dict[str, Any] = {}
    if row.get("publication_proof_identity") is not None:
        try:
            proof_identity = _proof_identity(row)
        except DocumentContractError:
            pass
    if row.get("source_kind") == "licensed_news_metadata":
        parsed = urlparse(str(row.get("canonical_url", "")))
        url_allowed = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    elif proof_identity.get("source_authority_id") is not None:
        url_allowed = row.get("source_kind") == "primary_source" and _authority_host(
            str(row.get("canonical_url", "")), proof_identity,
        )
    else:
        url_allowed = _allowed_host(str(row.get("canonical_url", "")), issuer, issuers)
    if not url_allowed:
        issues.append(DocumentIssue("canonical_url", str(row.get("canonical_url", ""))))
    if row.get("content_scope") not in {"metadata_only", "project_summary"}:
        issues.append(DocumentIssue("content_scope", str(row.get("content_scope"))))
    if row.get("published_at_precision") not in {"second", "minute", "date"}:
        issues.append(DocumentIssue("publication_precision", str(row.get("published_at_precision"))))
    summary = str(row.get("summary", ""))
    if not summary.startswith("Project-authored summary:") or len(summary) > 1200:
        issues.append(DocumentIssue("summary_contract", str(row.get("evidence_id", "unknown"))))
    if "<html" in summary.lower() or len(summary.split()) > 180:
        issues.append(DocumentIssue("body_copy", str(row.get("evidence_id", "unknown"))))
    if sha256_bytes(summary.encode()) != row.get("summary_sha256"):
        issues.append(DocumentIssue("summary_digest", str(row.get("evidence_id", "unknown"))))
    try:
        published = parse_utc(row.get("published_at"), "published_at")
        available = parse_utc(row.get("available_at", row.get("published_at")), "available_at")
        captured = parse_utc(row.get("captured_at"), "captured_at")
        revised_value = row.get("source_revised_at")
        revised = parse_utc(revised_value, "source_revised_at") if revised_value else published
        if published > available or available > captured or published > revised or revised > captured:
            issues.append(DocumentIssue("document_time_order", str(row.get("evidence_id"))))
    except MarketContractError as exc:
        issues.extend(exc.issues)
    if row.get("vintage_status") not in VINTAGES:
        issues.append(DocumentIssue("vintage_status", str(row.get("vintage_status"))))
    for name in ("summary_sha256", "source_sha256"):
        digest = str(row.get(name, ""))
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            issues.append(DocumentIssue("digest", name))
    artifact = row.get("capture_artifact")
    if artifact is not None:
        local_news_artifact = row.get("source_kind") == "licensed_news_metadata" and artifact == "news-metadata.jsonl"
        authoritative_artifact = isinstance(artifact, str) and artifact.startswith("raw/") and ".." not in Path(artifact).parts
        if not local_news_artifact and not authoritative_artifact:
            issues.append(DocumentIssue("capture_artifact", str(artifact)))
    proof_values = tuple(row.get(name) for name in (
        "publication_proof_kind", "publication_proof_artifact", "publication_proof_sha256", "publication_proof_identity",
    ))
    index_values = (row.get("publication_index_artifact"), row.get("publication_index_sha256"))
    issuer_values = (row.get("publication_issuer_artifact"), row.get("publication_issuer_sha256"))
    if row.get("source_kind") == "licensed_news_metadata":
        if any(value is not None for value in (*proof_values, *index_values, *issuer_values)):
            issues.append(DocumentIssue("publication_proof_shape", str(row.get("evidence_id"))))
    else:
        kind, proof_artifact, proof_sha, _identity = proof_values
        if kind not in PROOF_KINDS:
            issues.append(DocumentIssue("publication_proof_kind", str(row.get("evidence_id"))))
        if not isinstance(proof_artifact, str) or not proof_artifact.startswith("raw/") \
                or ".." in Path(proof_artifact).parts:
            issues.append(DocumentIssue("publication_proof_artifact", str(row.get("evidence_id"))))
        if not isinstance(proof_sha, str) or SHA256.fullmatch(proof_sha) is None:
            issues.append(DocumentIssue("publication_proof_digest", str(row.get("evidence_id"))))
        try:
            proof_identity = _proof_identity(row)
        except DocumentContractError as exc:
            issues.extend(exc.issues)
        index_artifact, index_sha = index_values
        if (index_artifact is None) != (index_sha is None):
            issues.append(DocumentIssue("publication_proof_index_shape", str(row.get("evidence_id"))))
        elif index_artifact is not None and (
            not isinstance(index_artifact, str) or not index_artifact.startswith("raw/")
            or ".." in Path(index_artifact).parts or not isinstance(index_sha, str)
            or SHA256.fullmatch(index_sha) is None
        ):
            issues.append(DocumentIssue("publication_proof_index_shape", str(row.get("evidence_id"))))
        needs_index = kind == "sec_submission_acceptance" and proof_identity.get("document_role") == "accession_document"
        if needs_index != (index_artifact is not None):
            issues.append(DocumentIssue("publication_proof_index_shape", str(row.get("evidence_id"))))
        issuer_artifact, issuer_sha = issuer_values
        needs_issuer = kind == "sec_submission_acceptance"
        if needs_issuer != (issuer_artifact is not None):
            issues.append(DocumentIssue("publication_proof_issuer_shape", str(row.get("evidence_id"))))
        elif issuer_artifact is not None and (
            not isinstance(issuer_artifact, str) or not issuer_artifact.startswith("raw/")
            or ".." in Path(issuer_artifact).parts or not isinstance(issuer_sha, str)
            or SHA256.fullmatch(issuer_sha) is None
        ):
            issues.append(DocumentIssue("publication_proof_issuer_shape", str(row.get("evidence_id"))))
    if issues:
        raise DocumentContractError(issues)


def eligible_at(row: dict[str, Any], cutoff: datetime) -> bool:
    """Declared availability controls eligibility; legacy rows use publication time."""
    if cutoff.tzinfo is None:
        raise DocumentContractError([DocumentIssue("naive_cutoff", "cutoff")])
    return parse_utc(row.get("available_at", row["published_at"]), "available_at") <= cutoff


def _snapshot_shape_issues(manifest: Any) -> list[DocumentIssue]:
    issues: list[DocumentIssue] = []
    expected = {
        "schema_version", "snapshot_kind", "snapshot_id", "normalizer_version", "gate", "data_tier",
        "vintage_status", "created_at", "captured_at", "cutoff_policy", "issuers", "corpus_sha256",
        "requirements", "observed_coverage", "sources", "artifacts", "gaps",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected:
        return [DocumentIssue("manifest_shape", "root")]
    if (
        type(manifest.get("schema_version")) is not int
        or type(manifest.get("normalizer_version")) is not int
        or any(not isinstance(manifest.get(key), str) or not manifest[key] for key in (
            "snapshot_kind", "snapshot_id", "gate", "data_tier", "vintage_status", "created_at",
            "captured_at", "cutoff_policy", "corpus_sha256",
        ))
        or SHA256.fullmatch(manifest["corpus_sha256"]) is None
    ):
        issues.append(DocumentIssue("manifest_scalar_type", "root"))
    issuers = manifest.get("issuers")
    if not isinstance(issuers, dict):
        issues.append(DocumentIssue("manifest_shape", "issuers"))
    else:
        for issuer_id, issuer in issuers.items():
            if (
                not isinstance(issuer_id, str) or not isinstance(issuer, dict)
                or set(issuer) != {"cik", "official_hosts"}
                or not isinstance(issuer.get("cik"), str) or re.fullmatch(r"[0-9]{10}", issuer["cik"]) is None
                or not _strings(issuer.get("official_hosts"), minimum=1)
            ):
                issues.append(DocumentIssue("manifest_shape", "issuers"))
                break
    requirements = manifest.get("requirements")
    requirement_keys = {"requirement_id", "issuer_id", "event_id", "source_kinds", "cutoff"}
    if not isinstance(requirements, list) or any(
        not isinstance(item, dict) or not requirement_keys <= set(item)
        or not set(item) <= requirement_keys | {"filing_summary"}
        or not all(isinstance(item.get(key), str) and item[key] for key in requirement_keys - {"source_kinds"})
        or not _strings(item.get("source_kinds"), minimum=1)
        or ("filing_summary" in item and not isinstance(item["filing_summary"], str))
        for item in requirements or []
    ):
        issues.append(DocumentIssue("manifest_shape", "requirements"))
    observed = manifest.get("observed_coverage")
    if (
        not isinstance(observed, dict)
        or set(observed) != {"documents", "coverage_rows", "issuers", "source_kinds", "published_start", "published_end"}
        or type(observed.get("documents")) is not int or observed["documents"] < 0
        or type(observed.get("coverage_rows")) is not int or observed["coverage_rows"] < 0
        or not _strings(observed.get("issuers")) or not _strings(observed.get("source_kinds"))
        or any(observed.get(key) is not None and not isinstance(observed.get(key), str) for key in ("published_start", "published_end"))
    ):
        issues.append(DocumentIssue("manifest_shape", "observed_coverage"))
    sources = manifest.get("sources")
    source_keys = {
        "capture_id", "adapter", "manifest_sha256", "captured_at", "vintage_status", "license_id",
        "redistribution", "archive_proof", "path",
    }
    if not isinstance(sources, list) or any(
        not isinstance(item, dict) or set(item) != source_keys
        or not all(isinstance(item.get(key), str) and item[key] for key in (
            "capture_id", "adapter", "manifest_sha256", "captured_at", "vintage_status", "path",
        ))
        or SHA256.fullmatch(item["manifest_sha256"]) is None
        or any(item.get(key) is not None and not isinstance(item.get(key), str) for key in ("license_id", "redistribution"))
        or (
            item.get("archive_proof") is not None and (
                not isinstance(item["archive_proof"], dict)
                or set(item["archive_proof"]) != {
                    "path", "sha256", "canonical_url", "proved_at", "coverage_end_exclusive", "bytes",
                }
                or type(item["archive_proof"].get("bytes")) is not int
                or any(not isinstance(item["archive_proof"].get(key), str) for key in (
                    "path", "sha256", "canonical_url", "proved_at", "coverage_end_exclusive",
                ))
            )
        )
        for item in sources or []
    ):
        issues.append(DocumentIssue("manifest_shape", "sources"))
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or any(
        not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes", "records", "media_type"}
        or not isinstance(item.get("path"), str) or not item["path"]
        or not isinstance(item.get("sha256"), str) or SHA256.fullmatch(item["sha256"]) is None
        or type(item.get("bytes")) is not int or item["bytes"] < 0
        or type(item.get("records")) is not int or item["records"] < 0
        or not isinstance(item.get("media_type"), str) or not item["media_type"]
        for item in artifacts or []
    ):
        issues.append(DocumentIssue("manifest_shape", "artifacts"))
    gaps = manifest.get("gaps")
    if not isinstance(gaps, list) or any(not isinstance(item, dict) for item in gaps or []):
        issues.append(DocumentIssue("manifest_shape", "gaps"))
    return issues


def validate_document_snapshot(root: Path, gate: str = "reconstruction") -> dict[str, Any]:
    try:
        root = _absolute_without_resolving(root)
        tree = read_regular_tree(root)
        manifest_bytes = tree["manifest.json"]
        manifest = json.loads(manifest_bytes)
    except (OSError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise DocumentContractError([DocumentIssue("manifest_unreadable", root.name)]) from exc
    issues: list[DocumentIssue | ContractIssue] = _snapshot_shape_issues(manifest)
    if issues:
        raise DocumentContractError(issues)
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 2 \
            or manifest.get("snapshot_kind") != "documents":
        issues.append(DocumentIssue("manifest_version", str(manifest.get("schema_version"))))
    if type(manifest.get("normalizer_version")) is not int or manifest.get("normalizer_version") != 2:
        issues.append(DocumentIssue("normalizer_version", str(manifest.get("normalizer_version"))))
    expected_snapshot_id = content_id("documents", {
        "normalizer_version": manifest.get("normalizer_version"),
        "captures": sorted(
            (item.get("capture_id"), item.get("manifest_sha256"))
            for item in manifest.get("sources", [])
        ),
        "corpus_sha256": manifest.get("corpus_sha256"),
        "gate": gate, "captured_at": manifest.get("captured_at"),
    })
    if manifest.get("snapshot_id") != expected_snapshot_id:
        issues.append(DocumentIssue("snapshot_identity", str(manifest.get("snapshot_id"))))
    if manifest.get("gate") != gate:
        issues.append(DocumentIssue("gate_mismatch", gate))
    if gate == "reconstruction" and (
        manifest.get("data_tier") != "authoritative_reconstruction"
        or manifest.get("vintage_status") != "reconstructed_later"
    ):
        issues.append(DocumentIssue("reconstruction_contract", str(manifest.get("data_tier"))))
    if gate == "release" and (
        manifest.get("data_tier") != "entitled_local"
        or manifest.get("vintage_status") != "archived_at_cutoff"
    ):
        issues.append(DocumentIssue("release_contract", str(manifest.get("data_tier"))))
    issuers = manifest.get("issuers", {})
    if gate == "release" and any(
        source.get("vintage_status") != "archived_at_cutoff"
        or source.get("adapter") != "local_news_metadata"
        or not source.get("license_id")
        or source.get("redistribution") not in {"allowed", "metadata_only", "local_only"}
        or not source.get("archive_proof")
        for source in manifest.get("sources", [])
    ):
        issues.append(DocumentIssue("release_source_unproved", "nested source declarations"))
    seen_paths: set[str] = set()
    for artifact in manifest.get("artifacts", []):
        relative = artifact.get("path", "")
        if relative in seen_paths:
            issues.append(DocumentIssue("duplicate_artifact", relative))
        seen_paths.add(relative)
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or relative == "manifest.json":
            issues.append(DocumentIssue("artifact_path", relative))
            continue
        body = tree.get(relative)
        if body is None:
            issues.append(DocumentIssue("artifact_missing", relative))
        elif len(body) != artifact.get("bytes") or sha256_bytes(body) != artifact.get("sha256"):
            issues.append(DocumentIssue("artifact_digest", relative))
        elif candidate.suffix == ".parquet":
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
                if pq.ParquetFile(pa.BufferReader(body)).metadata.num_rows != artifact.get("records"):
                    issues.append(DocumentIssue("artifact_records", relative))
            except Exception as exc:
                issues.append(DocumentIssue("parquet_unreadable", f"{relative}:{type(exc).__name__}"))
    required_paths = {"documents.parquet", "coverage.parquet"}
    if not required_paths <= seen_paths:
        issues.append(DocumentIssue("artifact_omission", ",".join(sorted(required_paths - seen_paths))))
    expected_tree = {"manifest.json", *seen_paths}
    if set(tree) != expected_tree:
        issues.append(DocumentIssue("artifact_inventory", ",".join(sorted(set(tree) ^ expected_tree))))
    capture_digests: dict[str, set[str]] = {}
    capture_descriptors: dict[str, dict[str, dict[str, Any]]] = {}
    capture_artifact_bytes: dict[tuple[str, str], bytes] = {}
    capture_coverage_ends: dict[str, Any] = {}
    captured_records: dict[tuple[str, str], dict[str, Any]] = {}
    authoritative_capture_ids: set[str] = set()
    for source in manifest.get("sources", []):
        capture_id = str(source.get("capture_id", ""))
        try:
            source_path = Path(str(source.get("path", "")))
            if source_path.is_absolute() or ".." in source_path.parts:
                raise KeyError("source path")
            capture_prefix = source_path.as_posix().rstrip("/") + "/"
            capture_bytes = tree.get(capture_prefix + "capture.json")
            if capture_bytes is None or sha256_bytes(capture_bytes) != source.get("manifest_sha256"):
                issues.append(DocumentIssue("capture_manifest_digest", capture_id))
                continue
            capture = json.loads(capture_bytes)
            if type(capture.get("schema_version")) is not int or capture.get("schema_version") != 1:
                issues.append(DocumentIssue("capture_shape", capture_id))
            if capture.get("capture_id") != capture_id or capture.get("adapter") != source.get("adapter"):
                issues.append(DocumentIssue("capture_identity", capture_id))
            if capture.get("captured_at") != source.get("captured_at") or capture.get("vintage_status") != source.get("vintage_status"):
                issues.append(DocumentIssue("capture_time_vintage", capture_id))
            proof = source.get("archive_proof")
            if source.get("vintage_status") == "archived_at_cutoff":
                try:
                    proof_bytes = tree.get(capture_prefix + proof["path"])
                    if (
                        parse_utc(proof["proved_at"], "archive_proof.proved_at") > parse_utc(source["captured_at"])
                        or parse_date(proof["coverage_end_exclusive"]) > parse_utc(source["captured_at"]).date()
                        or not str(proof["canonical_url"]).startswith("https://")
                        or proof_bytes is None or len(proof_bytes) != proof["bytes"]
                        or sha256_bytes(proof_bytes) != proof["sha256"]
                    ):
                        issues.append(DocumentIssue("archive_proof_invalid", capture_id))
                    else:
                        capture_coverage_ends[capture_id] = parse_date(proof["coverage_end_exclusive"])
                except (KeyError, TypeError, MarketContractError):
                    issues.append(DocumentIssue("archive_proof_missing", capture_id))
            elif proof is not None:
                issues.append(DocumentIssue("archive_proof_vintage", capture_id))
            digests: set[str] = set()
            artifact_rows = list(capture.get("raw_artifacts", []))
            if capture.get("file"):
                artifact_rows.append(capture["file"])
            if capture.get("adapter") == "authoritative":
                authoritative_capture_ids.add(capture_id)
                try:
                    expected_capture_keys = {
                        "schema_version", "capture_id", "adapter", "adapter_version", "captured_at",
                        "vintage_status", "corpus_sha256", "records", "raw_artifacts", "gaps",
                        "records_artifact", "gaps_artifact", "requests",
                    }
                    if frozenset(capture) not in {
                        frozenset(expected_capture_keys), frozenset(expected_capture_keys | {"migration"}),
                    }:
                        raise TypeError("authoritative capture schema")
                    records_bytes = tree[capture_prefix + "records.jsonl"]
                    gaps_bytes = tree[capture_prefix + "gaps.json"]
                    records = [json.loads(line) for line in records_bytes.decode().splitlines() if line]
                    gaps = json.loads(gaps_bytes)
                    descriptors = {
                        "records_artifact": ("records.jsonl", records_bytes),
                        "gaps_artifact": ("gaps.json", gaps_bytes),
                    }
                    if capture.get("adapter_version") != 7 or any(
                        capture.get(key) != {
                            "path": name, "bytes": len(body), "sha256": sha256_bytes(body),
                        } for key, (name, body) in descriptors.items()
                    ) or gaps != capture.get("gaps") or len(records) != capture.get("records"):
                        issues.append(DocumentIssue("capture_content_binding", capture_id))
                    if "migration" in capture and not valid_document_migration(capture["migration"]):
                        issues.append(DocumentIssue("capture_migration_provenance", capture_id))
                    material = [{key: value for key, value in row.items() if key != "capture_id"} for row in records]
                    identity = {
                        "adapter_version": capture.get("adapter_version"),
                        "corpus_sha256": capture.get("corpus_sha256"),
                        "captured_at": capture.get("captured_at"),
                        "raw_artifacts": capture.get("raw_artifacts"),
                        "records": material, "gaps": gaps,
                    }
                    if "migration" in capture:
                        identity["migration"] = capture["migration"]
                    expected_capture = content_id("doccapture", identity)
                    if expected_capture != capture_id or any(row.get("capture_id") != capture_id for row in records):
                        issues.append(DocumentIssue("capture_content_identity", capture_id))
                    for record in records:
                        key = (capture_id, str(record.get("evidence_id", "")))
                        if key in captured_records and captured_records[key] != record:
                            issues.append(DocumentIssue("capture_record_collision", f"{capture_id}:{key[1]}"))
                        captured_records[key] = record
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
                    issues.append(DocumentIssue("capture_content_binding", capture_id))
            for item in artifact_rows:
                if (
                    not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}
                    or not isinstance(item.get("path"), str) or Path(item["path"]).is_absolute()
                    or ".." in Path(item["path"]).parts or type(item.get("bytes")) is not int
                    or not isinstance(item.get("sha256"), str)
                ):
                    issues.append(DocumentIssue("capture_artifact_shape", capture_id))
                    continue
                body = tree.get(capture_prefix + item["path"])
                if body is None or len(body) != item["bytes"] or sha256_bytes(body) != item["sha256"]:
                    issues.append(DocumentIssue("capture_artifact_digest", f"{capture_id}:{item['path']}"))
                else:
                    digests.add(item["sha256"])
                    capture_artifact_bytes[(capture_id, item["path"])] = body
            descriptors = {
                item.get("path"): item for item in artifact_rows
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
            if len(descriptors) != len(artifact_rows):
                issues.append(DocumentIssue("capture_artifact_collision", capture_id))
            declared_capture_files = {
                "capture.json",
                *(item["path"] for item in artifact_rows if isinstance(item, dict) and isinstance(item.get("path"), str)),
            }
            if capture.get("adapter") == "authoritative":
                declared_capture_files |= {"records.jsonl", "gaps.json"}
            if isinstance(capture.get("archive_proof"), dict) and isinstance(capture["archive_proof"].get("path"), str):
                declared_capture_files.add(capture["archive_proof"]["path"])
            observed_capture_files = {
                relative[len(capture_prefix):] for relative in tree if relative.startswith(capture_prefix)
            }
            if declared_capture_files != observed_capture_files:
                issues.append(DocumentIssue("capture_artifact_inventory", capture_id))
            capture_digests[capture_id] = digests
            capture_descriptors[capture_id] = descriptors
        except (MarketContractError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            nested = getattr(exc, "issues", ())
            issues.extend(nested or [DocumentIssue("capture_manifest", capture_id)])
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        documents = pq.read_table(pa.BufferReader(tree["documents.parquet"])).to_pylist()
        coverage = pq.read_table(pa.BufferReader(tree["coverage.parquet"])).to_pylist()
    except Exception as exc:
        issues.append(DocumentIssue("parquet_unreadable", type(exc).__name__))
        documents, coverage = [], []
    seen: set[str] = set()
    for row in documents:
        try:
            validate_document_row(row, issuers)
        except DocumentContractError as exc:
            issues.extend(exc.issues)
        identity = str(row.get("evidence_id"))
        if identity in seen:
            issues.append(DocumentIssue("duplicate_evidence", identity))
        seen.add(identity)
        capture_id = str(row.get("capture_id", ""))
        if capture_id in authoritative_capture_ids:
            comparable = dict(row)
            if comparable.get("available_at") == comparable.get("published_at"):
                comparable.pop("available_at", None)
            if captured_records.get((capture_id, identity)) != comparable:
                issues.append(DocumentIssue("document_capture_record_drift", identity))
        if capture_id in capture_coverage_ends and parse_utc(row.get("published_at"), "published_at").date() >= capture_coverage_ends[capture_id]:
            issues.append(DocumentIssue("archive_proof_coverage", identity))
        if row.get("source_sha256") not in capture_digests.get(capture_id, set()):
            issues.append(DocumentIssue("source_capture_digest", identity))
        capture_artifact = row.get("capture_artifact")
        source_bytes: bytes | None = None
        if capture_artifact:
            try:
                if Path(capture_artifact).is_absolute() or ".." in Path(capture_artifact).parts:
                    raise KeyError("capture artifact")
                source_bytes = capture_artifact_bytes.get((capture_id, capture_artifact))
                descriptor = capture_descriptors.get(capture_id, {}).get(capture_artifact, {})
                if (
                    source_bytes is None or len(source_bytes) != descriptor.get("bytes")
                    or descriptor.get("sha256") != row.get("source_sha256")
                    or sha256_bytes(source_bytes) != row.get("source_sha256")
                ):
                    issues.append(DocumentIssue("document_artifact_digest", identity))
                    source_bytes = None
            except (KeyError, MarketContractError, OSError):
                issues.append(DocumentIssue("document_artifact_path", identity))
        proof_artifact = row.get("publication_proof_artifact")
        if proof_artifact is not None:
            try:
                if Path(proof_artifact).is_absolute() or ".." in Path(proof_artifact).parts:
                    raise KeyError("proof artifact")
                proof_bytes = capture_artifact_bytes.get((capture_id, proof_artifact))
                proof_descriptor = capture_descriptors.get(capture_id, {}).get(proof_artifact, {})
                if (
                    proof_bytes is None or len(proof_bytes) != proof_descriptor.get("bytes")
                    or proof_descriptor.get("sha256") != row.get("publication_proof_sha256")
                    or sha256_bytes(proof_bytes) != row.get("publication_proof_sha256")
                ):
                    issues.append(DocumentIssue("publication_proof_digest", identity))
                else:
                    index_bytes: bytes | None = None
                    index_artifact = row.get("publication_index_artifact")
                    if index_artifact is not None:
                        if Path(index_artifact).is_absolute() or ".." in Path(index_artifact).parts:
                            raise KeyError("index artifact")
                        candidate_index = capture_artifact_bytes.get((capture_id, index_artifact))
                        index_descriptor = capture_descriptors.get(capture_id, {}).get(index_artifact, {})
                        if (
                            candidate_index is None or len(candidate_index) != index_descriptor.get("bytes")
                            or index_descriptor.get("sha256") != row.get("publication_index_sha256")
                            or sha256_bytes(candidate_index) != row.get("publication_index_sha256")
                        ):
                            issues.append(DocumentIssue("publication_proof_index_digest", identity))
                        else:
                            index_bytes = candidate_index
                    issuer_bytes: bytes | None = None
                    issuer_artifact = row.get("publication_issuer_artifact")
                    if issuer_artifact is not None:
                        if Path(issuer_artifact).is_absolute() or ".." in Path(issuer_artifact).parts:
                            raise KeyError("issuer artifact")
                        candidate_issuer = capture_artifact_bytes.get((capture_id, issuer_artifact))
                        issuer_descriptor = capture_descriptors.get(capture_id, {}).get(issuer_artifact, {})
                        if (
                            candidate_issuer is None or len(candidate_issuer) != issuer_descriptor.get("bytes")
                            or issuer_descriptor.get("sha256") != row.get("publication_issuer_sha256")
                            or sha256_bytes(candidate_issuer) != row.get("publication_issuer_sha256")
                        ):
                            issues.append(DocumentIssue("publication_proof_issuer_digest", identity))
                        else:
                            issuer_bytes = candidate_issuer
                    validate_publication_proof(row, proof_bytes, source_bytes, index_bytes, issuer_bytes)
            except (KeyError, MarketContractError, OSError):
                issues.append(DocumentIssue("publication_proof_artifact", identity))
            except DocumentContractError as exc:
                issues.extend(exc.issues)
    observed = manifest.get("observed_coverage", {})
    direct = {
        "documents": len(documents), "coverage_rows": len(coverage),
        "issuers": sorted({row["issuer_id"] for row in documents}),
        "source_kinds": sorted({row["source_kind"] for row in documents}),
        "published_start": min((row["published_at"] for row in documents), default=None),
        "published_end": max((row["published_at"] for row in documents), default=None),
    }
    if observed != direct:
        issues.append(DocumentIssue("coverage_drift", repr(direct)))
    by_evidence = {row["evidence_id"]: row for row in documents}
    coverage_ids = {row.get("evidence_id") for row in coverage if row.get("evidence_id")}
    if not coverage_ids <= seen:
        issues.append(DocumentIssue("fabricated_coverage", "coverage evidence missing"))
    requirements = manifest.get("requirements", [])
    expected_pairs = {
        (requirement.get("requirement_id"), kind)
        for requirement in requirements for kind in requirement.get("source_kinds", [])
    }
    observed_pairs = {(row.get("requirement_id"), row.get("source_kind")) for row in coverage}
    if expected_pairs != observed_pairs:
        issues.append(DocumentIssue("requirement_coverage_drift", "requirement/source-kind pairs"))
    for requirement in requirements:
        try:
            cutoff = parse_utc(requirement.get("cutoff"), "requirement.cutoff")
        except MarketContractError as exc:
            issues.extend(exc.issues)
            continue
        for kind in requirement.get("source_kinds", []):
            actual_rows = [
                row for row in coverage
                if row.get("requirement_id") == requirement.get("requirement_id")
                and row.get("source_kind") == kind
            ]
            expected = [
                row for row in documents
                if row.get("issuer_id") == requirement.get("issuer_id")
                and row.get("source_kind") == kind
                and parse_utc(row.get("available_at", row.get("published_at")), "available_at") <= cutoff
                and row.get("event_id") == requirement.get("event_id")
            ]
            actual_ids = {row.get("evidence_id") for row in actual_rows if row.get("evidence_id")}
            if actual_ids != {row["evidence_id"] for row in expected}:
                issues.append(DocumentIssue("coverage_evidence_drift", str(requirement.get("requirement_id"))))
            for item in actual_rows:
                evidence_id = item.get("evidence_id")
                if evidence_id is None:
                    if item.get("status") != "unsupported" or not str(item.get("gap_code", "")).startswith("unsupported_"):
                        issues.append(DocumentIssue("coverage_gap_contract", str(requirement.get("requirement_id"))))
                    continue
                evidence = by_evidence.get(evidence_id, {})
                expected_status = "available_metadata" if evidence.get("content_scope") == "metadata_only" else "available"
                if (
                    item.get("status") != expected_status
                    or item.get("issuer_id") != evidence.get("issuer_id")
                    or item.get("published_at") != evidence.get("published_at")
                    or item.get("captured_at") != evidence.get("captured_at")
                    or item.get("canonical_url") != evidence.get("canonical_url")
                    or item.get("cutoff") != requirement.get("cutoff")
                ):
                    issues.append(DocumentIssue("coverage_row_drift", str(evidence_id)))
    if gate == "release" and any(row.get("vintage_status") != "archived_at_cutoff" for row in documents):
        issues.append(DocumentIssue("release_document_unproved", "document rows"))
    if issues:
        raise DocumentContractError(issues)
    return manifest

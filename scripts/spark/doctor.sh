#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

[[ $# -eq 0 ]] || spark_die "usage: $0"
spark_require_operator_tools
spark_require_command nvidia-smi
spark_require_four_services
spark_require_public_boundary

failures=0
check() {
  local code=$1 label=$2
  shift 2
  if "$@" >/dev/null 2>&1; then
    printf 'PASS  %-24s %s\n' "$code" "$label"
  else
    printf 'FAIL  %-24s %s\n' "$code" "$label"
    failures=$((failures + 1))
  fi
}

check HOST_ARCH "aarch64 host" bash -c '[[ "$(uname -m)" == aarch64 ]]'
check GPU_DEVICE "NVIDIA GB10 available" bash -c \
  'nvidia-smi --query-gpu=name --format=csv,noheader | grep -q GB10'
check GPU_RUNTIME "container GPU runtime" docker run --rm --pull never --network none --gpus all \
  market-shock/tools-foundation:phase0 nvidia-smi -L
check MODEL_MANIFEST "model manifest present" test -r "${SPARK_MANIFEST_DIR}/models.json"
check SCENARIO_MANIFEST "scenario manifest present" test -r "${SPARK_SCENARIO_DIR}/manifest.json"
check EVENT_CATALOG "event catalog source/scenario binding" python3 \
  "${REPO_ROOT}/scripts/spark/event_publication.py" verify \
  --repository-root "$REPO_ROOT" --scenario-root "$SPARK_SCENARIO_DIR" \
  --event-root "${SPARK_RUNTIME_ROOT}/events/current"
check FOUNDATION_GATE "foundation receipt passes" spark_json_status_pass "${SPARK_REPORT_DIR}/compatibility.json"
check PREP_IMAGE "qualification image local" docker image inspect market-shock/market-prep:phase9
check PHASE09_REPORT "Phase 09 qualification passes" docker run --rm --pull never --network none \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "${SPARK_RUNTIME_ROOT}:/srv/market-shock:ro" -v "${REPO_ROOT}:/workspace:ro" \
  -w /workspace --entrypoint /opt/market-prep-venv/bin/python \
  market-shock/market-prep:phase9 -c \
  'from pathlib import Path; from scripts.data.qualify_data import validate_report; validate_report(Path("/srv/market-shock/reports/data/phase-09-qualification.json"), Path("/srv/market-shock"))'
check MODEL_IMAGE "pinned vLLM image local" docker image inspect "$SPARK_MODEL_IMAGE"
check APP_IMAGES "application images local" bash -c \
  'docker image inspect market-shock-web:latest market-shock-agent:latest market-shock-tools:latest >/dev/null'
check OPENSHELL_PREPARED "OpenShell artifact receipt matches" spark_openshell verify
check RETENTION_BUDGET "visitor trace/state/disk budgets" python3 "${REPO_ROOT}/scripts/spark/retention.py" check

doctor_tmp="$(mktemp -d /tmp/market-shock-doctor.XXXXXX)"
chmod 0700 "$doctor_tmp"
trap 'rm -rf -- "$doctor_tmp"' EXIT
agent_health="${doctor_tmp}/agent-health.json"
booth_status="${doctor_tmp}/booth-status.json"
tools_health="${doctor_tmp}/tools-health.json"
: >"$agent_health"
: >"$booth_status"
: >"$tools_health"

services_running=false
booth_ready=false
if [[ -n "$("${COMPOSE[@]}" ps --status running -q 2>/dev/null)" ]]; then
  services_running=true
  check COMPOSE_SERVICES "web/tools/model running; no Compose agent" bash -c \
    '[[ "$(docker compose --env-file "$1" -f "$2" ps --status running --services | LC_ALL=C sort | tr "\n" " ")" == "model tools web " ]]' \
    _ "$SPARK_ENV_FILE" "$REPO_ROOT/compose.yaml"
  check OPENSHELL_AGENT "OpenShell sandbox and forwarding ready" spark_openshell status
  if curl -fsS --max-time 5 http://127.0.0.1:3000/health >/dev/null \
      && curl -fsS --max-time 20 http://127.0.0.1:3000/api/status >"$agent_health" \
      && spark_validate_public_status "$agent_health" >"$booth_status" 2>/dev/null; then
    booth_state=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' "$booth_status")
    if [[ "$booth_state" == ready ]]; then
      printf 'PASS  %-24s %s\n' BOOTH_STATUS "exact connected escalation contract"
      booth_ready=true
    else
      printf 'FAIL  %-24s %s\n' BOOTH_STATUS "prepared/degraded; remote routing is disabled"
      failures=$((failures + 1))
    fi
  else
    printf 'FAIL  %-24s %s\n' BOOTH_STATUS "public status failed the exact connected contract"
    failures=$((failures + 1))
  fi
  if [[ "$booth_ready" == true ]]; then
    check GENERATION_CANARY "bounded local generation proof" spark_openshell_generation_canary false
    check REMOTE_PROVIDER "approved inference endpoint reachable" spark_openshell_remote_provider_probe
  fi
  check TOOLS_HEALTH "tools evidence health ready" bash -c \
    '"${@:2}" exec -T tools python -c '\''import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read().decode())'\'' >"$1"' \
    _ "$tools_health" "${COMPOSE[@]}"
else
  printf 'INFO  %-24s %s\n' SERVICES_STOPPED "application is prepared but not running; booth readiness is not established"
fi

if python3 - \
    "${SPARK_SCENARIO_DIR}/manifest.json" \
    "${SPARK_RUNTIME_ROOT}/reports/data/phase-09-qualification.json" \
    "$agent_health" "$tools_health" "$services_running" <<'PY'
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


DIGEST = re.compile(r"[a-f0-9]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+,-]{0,127}")
TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9:.+-]+Z?)?")
FIELD = re.compile(r"(?:raw|adjusted)_(?:open|high|low|close|volume)|volume")
STATUS = re.compile(r"[a-z][a-z0-9_:-]{0,63}")
errors: list[str] = []


def error(code: str) -> None:
    if code not in errors:
        errors.append(code)


def read_json(path: Path, code: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        error(code)
        return None
    if not isinstance(value, dict):
        error(code)
        return None
    return value


def sha256(path: Path, code: str) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        error(code)
        return None


def relative(root: Path, value: Any, code: str, suffix: str = "") -> Path | None:
    if not isinstance(value, str):
        error(code)
        return None
    part = Path(value)
    if part.is_absolute() or ".." in part.parts:
        error(code)
        return None
    try:
        target = (root / part / suffix).resolve() if suffix else (root / part).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        error(code)
        return None
    return target


def safe(value: Any, pattern: re.Pattern[str], code: str) -> str:
    text = str(value)
    if not pattern.fullmatch(text):
        error(code)
        return "invalid"
    return text


def safe_list(values: Any, pattern: re.Pattern[str], code: str) -> list[str]:
    if not isinstance(values, list) or len(values) > 64:
        error(code)
        return []
    return [safe(value, pattern, code) for value in values]


def boolean(value: Any, code: str) -> str:
    if not isinstance(value, bool):
        error(code)
        return "invalid"
    return str(value).lower()


def number(value: Any, code: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        error(code)
        return "invalid"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def emit(label: str, value: str) -> None:
    print(f"DATA  {label:<24} {value}")


manifest_path = Path(sys.argv[1])
report_path = Path(sys.argv[2])
agent_path = Path(sys.argv[3])
tools_path = Path(sys.argv[4])
running = sys.argv[5] == "true"
manifest = read_json(manifest_path, "scenario_unreadable")
report = read_json(report_path, "qualification_unreadable")

if manifest is not None:
    scenario_root = manifest_path.resolve().parent
    scenario_id = safe(manifest.get("scenario_id"), IDENTIFIER, "scenario_id")
    tier = safe(manifest.get("data_tier"), STATUS, "data_tier")
    vintage = safe(manifest.get("vintage_status"), STATUS, "vintage_status")
    if manifest.get("schema_version") != 2 or manifest.get("snapshot_id") != manifest.get("scenario_id"):
        error("scenario_contract")
    emit("scenario_id", scenario_id)
    emit("tier", tier)
    emit("vintage", vintage)

    actual: dict[str, str | None] = {"scenario_manifest_sha256": sha256(manifest_path, "scenario_digest")}
    bindings: list[tuple[str, Any, str, str]] = [
        ("market_manifest_sha256", manifest.get("market", {}), "manifest_sha256", "market"),
        ("document_manifest_sha256", manifest.get("documents", {}), "manifest_sha256", "documents"),
    ]
    for key, binding, digest_key, label in bindings:
        if not isinstance(binding, dict):
            error(f"{label}_binding")
            actual[key] = None
            continue
        child = relative(scenario_root, binding.get("path"), f"{label}_path", "manifest.json")
        actual[key] = sha256(child, f"{label}_digest") if child else None
        expected = binding.get(digest_key)
        if actual[key] != expected or not isinstance(expected, str) or not DIGEST.fullmatch(expected):
            error(f"{label}_binding")
    readiness = manifest.get("readiness", {})
    for kind in ("market", "documents"):
        binding = readiness.get(kind, {}) if isinstance(readiness, dict) else {}
        key = f"{kind[:-1] if kind == 'documents' else kind}_readiness_sha256"
        child = relative(scenario_root, binding.get("path"), f"{kind}_readiness_path")
        actual[key] = sha256(child, f"{kind}_readiness_digest") if child else None
        expected = binding.get("sha256")
        if actual[key] != expected or not isinstance(expected, str) or not DIGEST.fullmatch(expected):
            error(f"{kind}_readiness_binding")
    coverage = manifest.get("coverage", {})
    session = coverage.get("session_index", {}) if isinstance(coverage, dict) else {}
    session_path = relative(scenario_root, session.get("path"), "session_index_path")
    actual["session_index_sha256"] = sha256(session_path, "session_index_digest") if session_path else None
    if actual["session_index_sha256"] != session.get("sha256"):
        error("session_index_binding")
    semantic = manifest.get("semantic", {})
    index_path = relative(scenario_root, semantic.get("index_path"), "semantic_index_path") if isinstance(semantic, dict) else None
    actual["semantic_index_sha256"] = sha256(index_path, "semantic_index_digest") if index_path else None
    if actual["semantic_index_sha256"] != semantic.get("index_sha256"):
        error("semantic_index_binding")
    risk = next((item for item in manifest.get("artifacts", []) if isinstance(item, dict) and item.get("path") == "models/risk-model.ubj"), None)
    risk_path = relative(scenario_root, "models/risk-model.ubj", "risk_model_path")
    actual["risk_model_sha256"] = sha256(risk_path, "risk_model_digest") if risk_path else None
    if not isinstance(risk, dict) or actual["risk_model_sha256"] != risk.get("sha256"):
        error("risk_model_binding")
    for label, key in (
        ("scenario_manifest", "scenario_manifest_sha256"),
        ("market_manifest", "market_manifest_sha256"),
        ("document_manifest", "document_manifest_sha256"),
        ("market_readiness", "market_readiness_sha256"),
        ("document_readiness", "document_readiness_sha256"),
        ("session_index", "session_index_sha256"),
        ("semantic_index", "semantic_index_sha256"),
        ("risk_model", "risk_model_sha256"),
    ):
        digest = actual.get(key)
        emit(f"digest.{label}", digest if isinstance(digest, str) and DIGEST.fullmatch(digest) else "invalid")

    if isinstance(coverage, dict):
        targets = safe_list(coverage.get("targets"), TICKER, "target_coverage")
        benchmarks = safe_list(coverage.get("required_benchmarks"), TICKER, "benchmark_coverage")
        emit("targets", ",".join(targets) or "none")
        emit("required_benchmarks", ",".join(benchmarks) or "none")
        dates = coverage.get("market_date_coverage", {})
        if isinstance(dates, dict):
            start = safe(dates.get("start"), DATE, "coverage_start")
            end = safe(dates.get("end_inclusive"), DATE, "coverage_end")
            emit("market_coverage", f"{start}..{end}; rows={number(dates.get('rows'), 'coverage_rows')}")
        else:
            error("market_coverage")
        fields = coverage.get("market_fields", {})
        if not isinstance(fields, dict) or len(fields) > 32:
            error("field_coverage")
        else:
            for symbol in sorted(fields):
                item = fields[symbol]
                symbol_safe = safe(symbol, TICKER, "field_symbol")
                present = safe_list(item.get("present_fields"), FIELD, "field_name") if isinstance(item, dict) else []
                basis = safe_list(item.get("price_basis"), STATUS, "price_basis") if isinstance(item, dict) else []
                emit(f"fields.{symbol_safe}", f"{','.join(present) or 'none'}; basis={','.join(basis) or 'none'}")
        documents = coverage.get("document_coverage", {})
        if isinstance(documents, dict):
            issuers = safe_list(documents.get("issuers"), TICKER, "document_issuers")
            kinds = safe_list(documents.get("source_kinds"), STATUS, "document_source_kinds")
            published_start = safe(documents.get("published_start"), DATE, "document_start")
            published_end = safe(documents.get("published_end"), DATE, "document_end")
            emit("document_coverage", f"issuers={','.join(issuers)}; kinds={','.join(kinds)}; rows={number(documents.get('documents'), 'document_rows')}")
            emit("document_dates", f"{published_start}..{published_end}")
        optional = coverage.get("optional_gaps", [])
        if not isinstance(optional, list):
            error("optional_gaps")
            optional = []
        optional_symbols = sorted({safe(item.get("instrument_id"), TICKER, "optional_symbol") for item in optional if isinstance(item, dict)})
        emit("optional_gaps", ",".join(optional_symbols) or "none")
        document_gaps = coverage.get("document_gaps", [])
        if not isinstance(document_gaps, list):
            error("document_gaps")
            document_gaps = []
        news = [item for item in document_gaps if isinstance(item, dict) and item.get("code") == "unsupported_missing_news"]
        news_issuers = sorted({safe(item.get("issuer_id"), TICKER, "news_issuer") for item in news})
        emit("news_gaps", f"unsupported_missing_news={len(news)}; issuers={','.join(news_issuers) or 'none'}")

    gpu = semantic.get("gpu_receipt", {}) if isinstance(semantic, dict) else {}
    emit("semantic_gpu", f"executed={boolean(gpu.get('gpu_executed'), 'semantic_gpu')}; fallback={boolean(gpu.get('fallback_used'), 'semantic_fallback')}")

    if report is not None:
        if report.get("schema_version") != 1 or report.get("phase") != "09-data-recovery":
            error("qualification_contract")
        report_status = safe(report.get("status"), STATUS, "qualification_status")
        report_gate = safe(report.get("gate"), STATUS, "qualification_gate")
        emit("qualification", f"status={report_status}; gate={report_gate}")
        expected_gate = "reconstruction" if manifest.get("data_tier") == "cc0_reconstruction" else "release"
        if report.get("status") != "pass" or report.get("gate") != expected_gate:
            error("qualification_outcome")
        if report.get("data_tier") != manifest.get("data_tier") or report.get("vintage_status") != manifest.get("vintage_status"):
            error("qualification_snapshot")
        expected_inputs = {
            "scenario_id": manifest.get("scenario_id"),
            "scenario_manifest_sha256": actual.get("scenario_manifest_sha256"),
            "market_snapshot_id": manifest.get("market", {}).get("snapshot_id"),
            "market_manifest_sha256": actual.get("market_manifest_sha256"),
            "document_snapshot_id": manifest.get("documents", {}).get("snapshot_id"),
            "document_manifest_sha256": actual.get("document_manifest_sha256"),
            "market_readiness_sha256": actual.get("market_readiness_sha256"),
            "document_readiness_sha256": actual.get("document_readiness_sha256"),
            "session_index_sha256": actual.get("session_index_sha256"),
            "semantic_index_sha256": actual.get("semantic_index_sha256"),
        }
        inputs = report.get("inputs", {})
        if not isinstance(inputs, dict) or any(inputs.get(key) != value for key, value in expected_inputs.items()):
            error("qualification_binding")
        environment = report.get("environment", {})
        if not isinstance(environment, dict) or environment.get("host_paths_recorded") is not False or environment.get("credentials_recorded") is not False:
            error("qualification_redaction")
        readiness_report = report.get("readiness", {})
        for kind in ("market", "documents"):
            item = readiness_report.get(kind, {}) if isinstance(readiness_report, dict) else {}
            statuses = item.get("statuses", {}) if isinstance(item, dict) else {}
            if not isinstance(statuses, dict):
                error("readiness_statuses")
                statuses = {}
            values = []
            for status, count in sorted(statuses.items()):
                values.append(f"{safe(status, STATUS, 'readiness_status')}={number(count, 'readiness_count')}")
            emit(f"readiness.{kind}", f"rows={number(item.get('rows'), 'readiness_rows')}; {','.join(values) or 'none'}")
            if item.get("rows") != 250 or sum(value for value in statuses.values() if isinstance(value, int) and not isinstance(value, bool)) != 250:
                error("readiness_denominator")
        market_report = report.get("market", {})
        raw = market_report.get("quality", {}).get("raw_fields_available") if isinstance(market_report, dict) else None
        emit("raw_fields", "available" if raw is True else "unavailable" if raw is False else "invalid")
        if not isinstance(raw, bool):
            error("raw_field_status")
        documents_report = report.get("documents", {})
        groups = documents_report.get("groups", []) if isinstance(documents_report, dict) else []
        if not isinstance(groups, list) or len(groups) > 64:
            error("document_groups")
            groups = []
        group_values = []
        for group in groups:
            if not isinstance(group, dict):
                error("document_group")
                continue
            issuer = safe(group.get("issuer_id"), TICKER, "document_group_issuer")
            kind = safe(group.get("source_kind"), STATUS, "document_group_kind")
            group_values.append(f"{issuer}:{kind}={number(group.get('records'), 'document_group_records')}")
        emit("document_groups", ",".join(group_values) or "none")
        parity = report.get("parity", {})
        samples = parity.get("samples", []) if isinstance(parity, dict) else []
        report_failures = report.get("failures", [])
        if not isinstance(samples, list) or not isinstance(report_failures, list):
            error("parity_shape")
            samples, report_failures = [], []
        emit("parity", f"completed={number(parity.get('completed'), 'parity_completed')}/{number(parity.get('requested'), 'parity_requested')}; failures={len(report_failures)}")
        if parity.get("requested", 0) < 100 or parity.get("completed") != parity.get("requested") or len(samples) != parity.get("completed") or report_failures:
            error("parity_denominator")
        receipts = [tool.get("receipt", {}) for sample in samples if isinstance(sample, dict) for tool in sample.get("tools", []) if isinstance(tool, dict)]
        gpu_receipts = sum(receipt.get("gpu_executed") is True and receipt.get("fallback_used") is False for receipt in receipts)
        emit("runtime_gpu", f"strict_receipts={gpu_receipts}/{len(receipts)}")
        if gpu_receipts != len(receipts) or len(receipts) != 2 * len(samples):
            error("runtime_gpu_receipts")
        latency = report.get("latency_ms", {})
        if not isinstance(latency, dict):
            error("latency_shape")
            latency = {}
        emit("latency_ms", f"p50={number(latency.get('p50'), 'latency_p50')}; p95={number(latency.get('p95'), 'latency_p95')}; max={number(latency.get('max'), 'latency_max')}")
        if not isinstance(latency.get("samples"), list) or len(latency["samples"]) != len(samples):
            error("latency_denominator")
        if report_failures:
            last = report_failures[-1] if isinstance(report_failures[-1], dict) else {}
            code = safe(last.get("code", "unknown"), STATUS, "failure_code")
            sample = number(last.get("sample", -1), "failure_sample")
            ticker = safe(last.get("ticker", "UNKNOWN"), TICKER, "failure_ticker")
            session_value = safe(last.get("session", "0000-00-00"), DATE, "failure_session")
            emit("last_failure", f"code={code}; sample={sample}; ticker={ticker}; session={session_value}")
        else:
            emit("last_failure", "none")
        eligibility = report.get("release_eligibility", {})
        eligibility_status = safe(eligibility.get("status"), STATUS, "release_eligibility") if isinstance(eligibility, dict) else "invalid"
        emit("release_eligibility", eligibility_status)
        if eligibility_status != ("not_eligible" if expected_gate == "reconstruction" else "eligible"):
            error("release_eligibility_status")
    else:
        for label in (
            "qualification", "readiness.market", "readiness.documents", "raw_fields",
            "document_groups", "parity", "runtime_gpu", "latency_ms",
            "last_failure", "release_eligibility",
        ):
            emit(label, "unavailable")

    for path, service in ((agent_path, "agent"), (tools_path, "tools")):
        if not running:
            emit(f"health.{service}", "not_running")
            continue
        health = read_json(path, f"{service}_health_unreadable")
        if health is None:
            emit(f"health.{service}", "unavailable")
            continue
        ready = health.get("ready") is True
        emit(f"health.{service}", "ready" if ready else "not_ready")
        if not ready:
            error(f"{service}_not_ready")
        if service == "tools":
            active_id = safe(health.get("scenario_id"), IDENTIFIER, "tools_health_scenario")
            active_digest = safe(health.get("scenario_manifest_sha256"), DIGEST, "tools_health_digest")
            emit("health.tools_scenario", active_id)
            emit("health.tools_digest", active_digest)
            health_binding = {
                "scenario_id": manifest.get("scenario_id"),
                "scenario_manifest_sha256": actual.get("scenario_manifest_sha256"),
                "market_manifest_sha256": actual.get("market_manifest_sha256"),
                "document_manifest_sha256": actual.get("document_manifest_sha256"),
                "market_readiness_sha256": actual.get("market_readiness_sha256"),
                "document_readiness_sha256": actual.get("document_readiness_sha256"),
                "data_tier": manifest.get("data_tier"),
                "vintage_status": manifest.get("vintage_status"),
            }
            if any(health.get(key) != value for key, value in health_binding.items()):
                error("tools_health_binding")

print(f"{'PASS' if not errors else 'FAIL'}  {'PHASE09_CONTRACT':<24} " + ("scenario, report, and health reconcile" if not errors else "safe validation codes=" + ",".join(errors)))
raise SystemExit(0 if not errors else 1)
PY
then
  :
else
  failures=$((failures + 1))
fi

if (( failures )); then
  spark_log "doctor found ${failures} blocking issue(s); see docs/OPERATIONS.md"
  exit 1
fi
if [[ "$booth_ready" == true ]]; then
  spark_log "doctor proved exact connected booth readiness without environment disclosure"
else
  spark_log "doctor passed preparation checks; booth readiness is not established"
fi

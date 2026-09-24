#!/usr/bin/env bash
set -Eeuo pipefail
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ -n "${COMPOSE_ENV_FILE:-}" ]]; then
  SPARK_ENV_FILE="${COMPOSE_ENV_FILE}"
elif [[ -f "${REPO_ROOT}/.env" ]]; then
  SPARK_ENV_FILE="${REPO_ROOT}/.env"
else
  SPARK_ENV_FILE="${REPO_ROOT}/.env.spark.example"
fi
COMPOSE=(docker compose --env-file "$SPARK_ENV_FILE" -f "$REPO_ROOT/compose.yaml")
SPARK_RUNTIME_ROOT="${SPARK_RUNTIME_ROOT:-/srv/market-shock}"
SPARK_MANIFEST_DIR="${SPARK_RUNTIME_ROOT}/manifests"
SPARK_REPORT_DIR="${SPARK_RUNTIME_ROOT}/reports/foundation"
SPARK_SCENARIO_DIR="${SPARK_RUNTIME_ROOT}/scenario"
SPARK_MODEL_IMAGE="vllm/vllm-openai:v0.27.1@sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967"
SPARK_LOCAL_MODEL="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
SPARK_LOCAL_REVISION="bee7596271d1495f6992ae224aefde4410e816b8"
SPARK_SPECULATIVE_MODEL="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark"
SPARK_SPECULATIVE_REVISION="8a0177116d138011e63103110f136ec0ca09ebbf"
SPARK_EMBED_MODEL="nvidia/Nemotron-3-Embed-1B-BF16"
SPARK_EMBED_REVISION="9e0b24858b1195815ecb1188ffa1b73bcea7b30a"
SPARK_JUDGE_MODEL="openai/openai/gpt-5.6-luna"
SPARK_CAPABLE_MODEL="nvidia/nvidia/nemotron-3-ultra"
spark_log(){ printf '[spark] %s\n' "$*" >&2; }
spark_die(){ spark_log "ERROR: $*"; exit 1; }
spark_require_command(){ command -v "$1" >/dev/null 2>&1 || spark_die "required command not found: $1"; }
spark_require_runtime_root(){ case "$SPARK_RUNTIME_ROOT" in /srv/market-shock|/srv/market-shock/*) mkdir -p "$SPARK_MANIFEST_DIR" "$SPARK_REPORT_DIR";; *) spark_die "runtime root must be /srv/market-shock or a child";; esac; }
spark_atomic_replace(){ local source=$1 target=$2; mkdir -p "$(dirname "$target")"; mv -f -- "$source" "$target"; }

spark_require_operator_tools() {
  spark_require_command docker
  spark_require_command curl
  spark_require_command python3
  docker compose version >/dev/null 2>&1 || spark_die "Docker Compose v2 is required"
  [[ -r "$SPARK_ENV_FILE" ]] || spark_die "Compose environment file is not readable; copy .env.spark.example to .env"
}

spark_require_four_services() {
  local actual expected
  actual="$("${COMPOSE[@]}" --profile image-only config --services | LC_ALL=C sort | tr '\n' ' ')"
  expected="agent model tools web "
  [[ "$actual" == "$expected" ]] || spark_die "Compose must resolve exactly: web, agent, tools, model (found: ${actual:-none})"
}

spark_openshell() {
  python3 "$REPO_ROOT/scripts/spark/openshell_runtime.py" "$@"
}

spark_prepare_web_bridge() {
  # Docker chooses IPAM on each host. Never assume another workstation's bridge IP.
  if ! docker network inspect market-shock_edge >/dev/null 2>&1; then
    docker network create --label com.docker.compose.network=edge \
      --label com.docker.compose.project=market-shock market-shock_edge >/dev/null
  fi
  AGENT_FORWARD_HOST="$(docker network inspect market-shock_edge \
    --format '{{(index .IPAM.Config 0).Gateway}}')"
  python3 -c 'import ipaddress,sys; a=ipaddress.ip_address(sys.argv[1]); assert a.version == 4 and a.is_private' \
    "$AGENT_FORWARD_HOST" || spark_die "web bridge requires a private IPv4 gateway"
  export AGENT_FORWARD_HOST
}

spark_require_public_boundary() {
  # Never render the interpolated Compose document here: it contains the agent's
  # injected remote endpoint and credential.  The static pass sees placeholders
  # only, while the runtime pass consumes Docker's publisher metadata.
  if ! docker compose -f "$REPO_ROOT/compose.yaml" --profile image-only config --no-interpolate --format json | python3 -c '
import json, sys
try:
    services = json.load(sys.stdin).get("services")
except (AttributeError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(services, dict):
    raise SystemExit(1)
published = []
for service, config in services.items():
    if not isinstance(config, dict) or not isinstance(config.get("ports", []), list):
        raise SystemExit(1)
    for port in config.get("ports", []):
        published.append((service, port))
expected = [("web", "127.0.0.1:${WEB_PORT:-3000}:3000")]
raise SystemExit(0 if published == expected else 1)
'; then
    spark_die "static Compose boundary must publish only loopback web on canonical host port 3000"
  fi

  if ! "${COMPOSE[@]}" --profile image-only ps --format json | python3 -c '
import json, sys
rows = []
try:
    for line in sys.stdin:
        if not line.strip():
            continue
        value = json.loads(line)
        rows.extend(value if isinstance(value, list) else [value])
except (AttributeError, json.JSONDecodeError):
    raise SystemExit(1)
published = set()
services = set()
for row in rows:
    if not isinstance(row, dict) or not isinstance(row.get("Publishers", []), list):
        raise SystemExit(1)
    service = row.get("Service")
    if service not in {"web", "tools", "model"}:
        raise SystemExit(1)
    services.add(service)
    for port in row.get("Publishers", []):
        if not isinstance(port, dict):
            raise SystemExit(1)
        external = port.get("PublishedPort")
        target, protocol, url = port.get("TargetPort"), port.get("Protocol"), port.get("URL")
        if type(external) is not int or external < 0 or type(target) is not int or target <= 0 or protocol not in {"tcp", "udp"} or not isinstance(url, str):
            raise SystemExit(1)
        if external > 0:
            published.add((service, url, external, target, protocol))
allowed = {("web", "127.0.0.1", 3000, 3000, "tcp")}
expected = allowed if "web" in services else set()
raise SystemExit(0 if published == expected else 1)
'; then
    spark_die "only web/tools/model may run in Compose; only loopback web port 3000 may be published"
  fi
}

spark_validate_public_status() {
  # Reduce the public document to an allowlisted readiness receipt. Never
  # persist the raw response: future status fields may carry operator-only data.
  local source=$1
  # The validator program itself arrives on stdin. Preserve a piped status
  # document on descriptor 3 when the caller selects `-`.
  python3 - "$source" 3<&0 <<'PY'
# PHASE22_STATUS_VALIDATOR_BEGIN
import json
import os
from pathlib import Path
import re
import sys

LOCAL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
SPECULATIVE = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark"
EMBED = "nvidia/Nemotron-3-Embed-1B-BF16"
LUNA = "openai/openai/gpt-5.6-luna"
ULTRA = "nvidia/nvidia/nemotron-3-ultra"
DIGEST = re.compile(r"[a-f0-9]{64}")
PROJECT = re.compile(r"https://smith\.langchain\.com/o/[A-Za-z0-9-]+/projects/p/[A-Za-z0-9-]+")

def need(condition):
    if not condition:
        raise ValueError("status contract")

try:
    raw = os.fdopen(3).read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text(encoding="utf-8")
    status = json.loads(raw, object_pairs_hook=lambda pairs: dict(pairs) if len(pairs) == len(dict(pairs)) else (_ for _ in ()).throw(ValueError("duplicate")))
    top = {"schema_version", "service", "version", "ready", "dependencies", "remote_routing_enabled", "observability", "investigations", "supported_tickers", "companies", "coverage", "limitations", "models", "routes", "contracts"}
    need(isinstance(status, dict) and set(status) == top and status.get("schema_version") == "system-status-v1")
    need(status.get("service") == "agent" and status.get("version") == "1.2.0" and status.get("investigations") == "available" and type(status.get("ready")) is bool)
    def safe(item, field=""):
        if isinstance(item, dict):
            for key, child in item.items(): safe(child, key)
        elif isinstance(item, list):
            for child in item: safe(child, field)
        elif isinstance(item, str):
            lowered = item.lower()
            need(not any(term in lowered for term in ("bearer ", "authorization", "api_key", "api-key", "credential", "password", "secret", "token=")))
            need("://" not in item or field == "project_link")
    safe(status)
    dependencies = status.get("dependencies")
    need(isinstance(dependencies, dict) and set(dependencies) == {"tools", "model", "coverage", "checkpoint", "mcp_contract", "event_catalog"})
    need(all(type(value) is bool for value in dependencies.values()))
    remote = status.get("remote_routing_enabled")
    need(type(remote) is bool)
    need(status.get("supported_tickers") == ["NVDA", "AMD", "JPM", "GS", "SCHW"])
    need(status.get("companies") == [
        {"symbol": "NVDA", "display_name": "NVIDIA"},
        {"symbol": "AMD", "display_name": "Advanced Micro Devices"},
        {"symbol": "JPM", "display_name": "JPMorgan Chase"},
        {"symbol": "GS", "display_name": "Goldman Sachs"},
        {"symbol": "SCHW", "display_name": "Charles Schwab"},
    ])
    need(isinstance(status.get("limitations"), list) and all(isinstance(item, str) for item in status["limitations"]))
    observability = status.get("observability")
    need(isinstance(observability, dict) and set(observability) in (
        {"remote_inference_enabled", "langsmith_export_enabled"},
        {"remote_inference_enabled", "langsmith_export_enabled", "project_link"},
    ))
    need(type(observability.get("remote_inference_enabled")) is bool and observability["remote_inference_enabled"] is remote)
    need(type(observability.get("langsmith_export_enabled")) is bool)
    if observability["langsmith_export_enabled"]:
        need(isinstance(observability.get("project_link"), str) and PROJECT.fullmatch(observability["project_link"]))
    else:
        need("project_link" not in observability)

    contracts = status.get("contracts")
    need(isinstance(contracts, dict) and set(contracts) == {"max_investigation_turns", "max_concurrent_investigations", "data", "skill", "prompt", "safety", "generation_model", "embedding_model", "embedding_revision"})
    need(contracts.get("max_investigation_turns") == 4 and type(contracts.get("max_investigation_turns")) is int)
    need(contracts.get("max_concurrent_investigations") == 1 and type(contracts.get("max_concurrent_investigations")) is int)
    need(contracts.get("generation_model") == LOCAL and contracts.get("embedding_model") == EMBED)
    need(contracts.get("embedding_revision") == "9e0b24858b1195815ecb1188ffa1b73bcea7b30a")
    need(contracts.get("skill") == "market-agent-skills/deep-agent-1.0.0")
    need(contracts.get("prompt") == "market-shock-grounded-synthesis/2.0.0" and contracts.get("safety") == "bounded-equity-research/1.0.0")
    need(isinstance(contracts.get("data"), str) and contracts["data"])

    coverage = status.get("coverage")
    need(isinstance(coverage, dict) and set(coverage) == {"scenario_id", "scenario_manifest_sha256", "data_tier", "vintage_status", "first_session", "last_session", "session_count", "document_source_kinds"} and coverage.get("scenario_id") == contracts["data"])
    need(DIGEST.fullmatch(str(coverage.get("scenario_manifest_sha256", ""))) is not None)

    expected = {
        LOCAL: ("bee7596271d1495f6992ae224aefde4410e816b8", "local_model_service", "immutable_revision", ("local_generation", "switchyard_efficient_target"), True, "model"),
        SPECULATIVE: ("8a0177116d138011e63103110f136ec0ca09ebbf", "local_model_service", "immutable_revision", ("speculative_assistant",), False, "model"),
        EMBED: ("9e0b24858b1195815ecb1188ffa1b73bcea7b30a", "local_tools_service", "immutable_revision", ("retrieval_embedding",), False, "tools"),
        LUNA: (None, "internal_inference_server", "exact_server_route_id", ("switchyard_classifier",), True, "remote_routing"),
        ULTRA: (None, "internal_inference_server", "exact_server_route_id", ("switchyard_capable_target", "report_formatter"), True, "remote_routing"),
    }
    models = status.get("models")
    need(isinstance(models, list) and len(models) == len(expected))
    observed = {}
    for model in models:
        need(isinstance(model, dict) and set(model) == {"model_id", "revision", "location", "identity_basis", "roles", "route_eligible", "dependency"})
        identifier = model.get("model_id")
        need(isinstance(identifier, str) and identifier not in observed and "llama" not in identifier.lower() and identifier != "openai/openai/gpt-5.6-sol")
        need(isinstance(model.get("roles"), list) and all(isinstance(role, str) for role in model["roles"]))
        need(type(model.get("route_eligible")) is bool)
        observed[identifier] = (model.get("revision"), model.get("location"), model.get("identity_basis"), tuple(model["roles"]), model.get("route_eligible"), model.get("dependency"))
    need(observed == expected)

    routes = status.get("routes")
    need(isinstance(routes, list) and len(routes) == 1 and isinstance(routes[0], dict))
    route = routes[0]
    need(set(route) == {"mode", "enabled", "disabled_reason", "model_roles", "evidence_tools_unchanged"})
    need(route.get("mode") == "switchyard_escalation" and type(route.get("enabled")) is bool)
    need(route.get("model_roles") == ["switchyard_classifier", "local_generation", "switchyard_capable_target", "report_formatter"])
    need(route.get("evidence_tools_unchanged") is True)
    common = all(dependencies.values())
    expected_ready = common and remote
    need(status["ready"] is expected_ready and route["enabled"] is expected_ready)
    need(route.get("disabled_reason") == (None if expected_ready else "remote_routing_disabled" if not remote else "required_dependencies_unavailable"))
    state = "ready" if expected_ready else "prepared_remote_off" if common and not remote else "degraded"
    normalized = {
        "schema_version": "phase22-booth-status-v1",
        "state": state,
        "ready": expected_ready,
        "remote_routing_enabled": remote,
        "route": "switchyard_escalation",
        "max_investigation_turns": 4,
        "max_concurrent_investigations": 1,
        "scenario_id": coverage["scenario_id"],
        "scenario_manifest_sha256": coverage["scenario_manifest_sha256"],
        "models": list(expected),
        "langsmith_export_enabled": observability["langsmith_export_enabled"],
    }
    print(json.dumps(normalized, sort_keys=True, separators=(",", ":")))
except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
    raise SystemExit("public status failed the exact booth contract") from None
# PHASE22_STATUS_VALIDATOR_END
PY
}

spark_openshell_generation_canary() {
  # The request runs inside the prepared sandbox. Its response is reduced to a
  # boolean receipt, and OpenShell suppresses raw command/provider failures.
  local fresh=${1:-false} lease_path=${2:-}
  [[ "$fresh" == true || "$fresh" == false ]] || return 2
  python3 - "$REPO_ROOT" "$fresh" "$lease_path" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, sys.argv[1])
from scripts.spark.openshell_runtime import NAME, shell

fresh = sys.argv[2] == "true"
lease_path = Path(sys.argv[3]) if sys.argv[3] else None
query = "/health/ready?verify_generation=true" if fresh else "/health/ready"
code = (
    "import json,sys,urllib.request; "
    f"u='http://127.0.0.1:2024{query}'; "
    "t=sys.stdin.read().strip(); "
    "q=urllib.request.Request(u,headers={'X-Market-Maintenance-Token':t} if t else {}); "
    "r=urllib.request.urlopen(q,timeout=20); "
    "print(json.dumps(json.load(r),sort_keys=True,separators=(',',':')))"
)
try:
    token = ""
    if lease_path is not None:
        info = lease_path.lstat()
        if lease_path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError
        lease = json.loads(lease_path.read_text())
        token = lease.get("token", "")
        if not isinstance(token, str) or not token:
            raise ValueError
    raw = shell("sandbox", "exec", "-n", NAME, "--", "/usr/local/bin/python3.12", "-c", code, timeout=25, input_text=token)
    value = json.loads(raw)
    if not (isinstance(value, dict) and value.get("service") == "agent" and value.get("ready") is True
            and value.get("tools") is True and value.get("model") is True):
        raise ValueError
    print(json.dumps({"schema_version": "phase22-generation-canary-v1", "status": "pass", "fresh": fresh}, sort_keys=True, separators=(",", ":")))
except (RuntimeError, OSError, ValueError, KeyError, json.JSONDecodeError):
    raise SystemExit("OpenShell generation canary unavailable; no provider output was displayed") from None
PY
}

spark_openshell_remote_provider_probe() {
  # Prove that the endpoint-bound inference provider still works from the
  # sandbox network namespace. A host reboot can leave a resumed sandbox with
  # stale DNS even though its process and provider attachment both look ready.
  # Reduce every failure to one operator-safe message; never print the endpoint,
  # credential, response body, or transport exception.
  python3 - "$REPO_ROOT" <<'PY'
import json
import sys

sys.path.insert(0, sys.argv[1])
from scripts.spark.openshell_runtime import NAME, shell

code = r'''
import json
import os
import urllib.request

enabled = os.environ.get("REMOTE_ROUTING_ENABLED", "false").lower() == "true"
if not enabled:
    print(json.dumps({"schema_version": "remote-provider-probe-v1", "status": "skipped", "enabled": False}, sort_keys=True, separators=(",", ":")))
    raise SystemExit(0)

base = os.environ.get("NVIDIA_BASE_URL", "").rstrip("/")
credential = os.environ.get("NVIDIA_INFERENCE_API_KEY", "")
if not base or not credential:
    raise SystemExit(1)

try:
    request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + credential})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise SystemExit(1)
except Exception:
    raise SystemExit(1) from None

print(json.dumps({"schema_version": "remote-provider-probe-v1", "status": "pass", "enabled": True, "http_status": 200}, sort_keys=True, separators=(",", ":")))
'''

try:
    raw = shell("sandbox", "exec", "-n", NAME, "--", "/usr/local/bin/python3.12", "-c", code, timeout=20)
    value = json.loads(raw)
    disabled = {"schema_version": "remote-provider-probe-v1", "status": "skipped", "enabled": False}
    enabled = {"schema_version": "remote-provider-probe-v1", "status": "pass", "enabled": True, "http_status": 200}
    if value not in (disabled, enabled):
        raise ValueError
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
except (RuntimeError, OSError, ValueError, json.JSONDecodeError):
    raise SystemExit("OpenShell remote inference probe failed; no endpoint, credential, response, or transport detail was displayed") from None
PY
}

spark_openshell_maintenance_acquire() {
  local target=$1
  python3 - "$REPO_ROOT" "$target" <<'PY'
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, sys.argv[1])
from scripts.spark.openshell_runtime import NAME, shell

target = Path(sys.argv[2])
code = (
    "import json,urllib.request; "
    "q=urllib.request.Request('http://127.0.0.1:2024/health/maintenance',data=b'',method='POST'); "
    "r=urllib.request.urlopen(q,timeout=5); "
    "print(json.dumps(json.load(r),sort_keys=True,separators=(',',':')))"
)
try:
    value = json.loads(shell("sandbox", "exec", "-n", NAME, "--", "/usr/local/bin/python3.12", "-c", code, timeout=10))
    if (not isinstance(value, dict) or set(value) != {"service", "lease_id", "token", "acquired_at"}
            or value["service"] != "agent" or re.fullmatch(r"[a-f0-9]{32}", str(value["lease_id"])) is None
            or not isinstance(value["token"], str) or len(value["token"]) < 32
            or not isinstance(value["acquired_at"], str) or not value["acquired_at"].endswith("Z")):
        raise ValueError
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":")); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
except (RuntimeError, OSError, ValueError, json.JSONDecodeError):
    raise SystemExit("OpenShell maintenance lease unavailable; no token or provider output was displayed") from None
PY
}

spark_openshell_maintenance_release() {
  local lease_path=$1 target=$2
  python3 - "$REPO_ROOT" "$lease_path" "$target" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, sys.argv[1])
from scripts.spark.openshell_runtime import NAME, shell

lease_path, target = Path(sys.argv[2]), Path(sys.argv[3])
try:
    info = lease_path.lstat()
    if lease_path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise ValueError
    lease = json.loads(lease_path.read_text())
    token, lease_id = lease["token"], lease["lease_id"]
    code = (
        "import json,sys,urllib.request; t=sys.stdin.read().strip(); "
        "q=urllib.request.Request('http://127.0.0.1:2024/health/maintenance',method='DELETE',headers={'X-Market-Maintenance-Token':t}); "
        "r=urllib.request.urlopen(q,timeout=5); print(json.dumps(json.load(r),sort_keys=True,separators=(',',':')))"
    )
    value = json.loads(shell("sandbox", "exec", "-n", NAME, "--", "/usr/local/bin/python3.12", "-c", code, timeout=10, input_text=token))
    if (not isinstance(value, dict) or set(value) != {"service", "lease_id", "acquired_at", "released_at"}
            or value["service"] != "agent" or value["lease_id"] != lease_id
            or value["acquired_at"] != lease["acquired_at"] or not str(value["released_at"]).endswith("Z")):
        raise ValueError
    safe = {"lease_id": lease_id, "acquired_at": value["acquired_at"], "released_at": value["released_at"], "acquired": True, "released": True}
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(safe, stream, sort_keys=True, separators=(",", ":")); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
except (RuntimeError, OSError, ValueError, KeyError, json.JSONDecodeError):
    raise SystemExit("OpenShell maintenance lease release failed; no token or provider output was displayed") from None
PY
}

spark_openshell_idle_probe() {
  # Side-effect-free admission proof. This endpoint is not proxied by web;
  # OpenShell executes the request inside the prepared agent sandbox.
  python3 - "$REPO_ROOT" <<'PY'
import json
import sys

sys.path.insert(0, sys.argv[1])
from scripts.spark.openshell_runtime import NAME, shell

code = (
    "import json,urllib.request; "
    "r=urllib.request.urlopen('http://127.0.0.1:2024/health/ready?require_idle=true',timeout=5); "
    "print(json.dumps(json.load(r),sort_keys=True,separators=(',',':')))"
)
try:
    raw = shell("sandbox", "exec", "-n", NAME, "--", "/usr/local/bin/python3.12", "-c", code, timeout=10)
    value = json.loads(raw)
    if value != {"service": "agent", "idle": True}:
        raise ValueError
    print(json.dumps({"schema_version": "phase22-idle-probe-v1", "status": "pass", "idle": True}, sort_keys=True, separators=(",", ":")))
except (RuntimeError, OSError, ValueError, json.JSONDecodeError):
    raise SystemExit("OpenShell idle probe unavailable; no provider output was displayed") from None
PY
}

spark_json_status_pass() {
  local path=$1
  [[ -r "$path" ]] || return 1
  python3 - "$path" <<'PY'
import json, pathlib, sys
try:
    document = json.loads(pathlib.Path(sys.argv[1]).read_text())
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if document.get("status") == "pass" else 1)
PY
}

spark_api_ready() {
  curl --fail --silent --show-error --max-time 20 \
    "http://127.0.0.1:3000/api/status" \
    | spark_validate_public_status - \
    | python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("state") == "ready" else 1)'
}

spark_wait_api_ready() {
  local attempt state_file status_file state
  status_file=$(mktemp /tmp/market-shock-status.XXXXXX)
  state_file=$(mktemp /tmp/market-shock-status-normalized.XXXXXX)
  chmod 0600 "$status_file" "$state_file"
  for attempt in {1..30}; do
    if curl --fail --silent --show-error --max-time 20 \
        "http://127.0.0.1:3000/api/status" >"$status_file" 2>/dev/null \
        && spark_validate_public_status "$status_file" >"$state_file" 2>/dev/null; then
      state=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' "$state_file")
      if [[ "$state" == ready ]]; then
        rm -f -- "$status_file" "$state_file"
        return 0
      fi
      if [[ "$state" == prepared_remote_off ]]; then
        rm -f -- "$status_file" "$state_file"
        spark_die "application is prepared/degraded because remote routing is disabled; booth readiness was not announced"
      fi
    fi
    sleep 1
  done
  rm -f -- "$status_file" "$state_file"
  spark_die "browser could not prove exact connected booth readiness after 30 readiness attempts"
}

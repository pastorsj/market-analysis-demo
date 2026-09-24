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
# shellcheck disable=SC2034 # read by the scripts that source this file
SPARK_MODEL_IMAGE="vllm/vllm-openai:v0.27.1@sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967"
spark_log(){ printf '[spark] %s\n' "$*" >&2; }
spark_die(){ spark_log "ERROR: $*"; exit 1; }
spark_require_command(){ command -v "$1" >/dev/null 2>&1 || spark_die "required command not found: $1"; }
spark_require_runtime_root(){ case "$SPARK_RUNTIME_ROOT" in /srv/market-shock|/srv/market-shock/*) mkdir -p "$SPARK_MANIFEST_DIR" "$SPARK_REPORT_DIR";; *) spark_die "runtime root must be /srv/market-shock or a child";; esac; }
spark_atomic_replace(){ local source=$1 target=$2; mkdir -p "$(dirname "$target")"; mv -f -- "$source" "$target"; }

spark_require_operator_tools() {
  spark_require_command docker
  spark_require_command curl
  spark_require_command python3
  spark_require_command jq
  docker compose version >/dev/null 2>&1 || spark_die "Docker Compose v2 is required"
  [[ -r "$SPARK_ENV_FILE" ]] || spark_die "Compose environment file is not readable; copy .env.spark.example to .env"
}

spark_require_compose_services() {
  # Compose runs web, tools, and model; OpenShell runs the agent.
  local actual
  actual="$("${COMPOSE[@]}" config --services | LC_ALL=C sort | tr '\n' ' ')"
  [[ "$actual" == "model tools web " ]] \
    || spark_die "Compose must define exactly web, tools, and model (found: ${actual:-none})"
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
  # Only web may publish a host port, and only on 127.0.0.1:3000 (declared and running).
  "${COMPOSE[@]}" config --format json \
    | python3 "$REPO_ROOT/scripts/spark/process_contract.py" static-ports \
    || spark_die "compose.yaml must publish only web on 127.0.0.1:3000"
  "${COMPOSE[@]}" ps --format json \
    | python3 "$REPO_ROOT/scripts/spark/process_contract.py" running-ports \
    || spark_die "only web/tools/model may run in Compose; only web may publish 127.0.0.1:3000"
}

spark_json_status_pass() {
  [[ -r "$1" ]] && jq -e '.status == "pass"' "$1" >/dev/null 2>&1
}

spark_agent_status() {
  # Usage: spark_agent_status ATTEMPTS
  # Exit 0: research ready; 3: prepared, but remote routing is off; 1: not ready.
  # Prints a one-line summary (never the raw status document).
  python3 "$REPO_ROOT/scripts/spark/agent_probe.py" status \
    --scenario-manifest "${SPARK_SCENARIO_DIR}/manifest.json" --attempts "$1"
}

spark_agent_remote_probe() {
  python3 "$REPO_ROOT/scripts/spark/agent_probe.py" remote
}

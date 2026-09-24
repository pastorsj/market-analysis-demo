#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

[[ $# -eq 0 ]] || spark_die "usage: $0"
spark_require_operator_tools
spark_require_command nvidia-smi
spark_require_compose_services
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

tools_health="$(mktemp /tmp/market-shock-tools-health.XXXXXX)"
trap 'rm -f -- "$tools_health"' EXIT

running=false
research_ready=false
if [[ -n "$("${COMPOSE[@]}" ps --status running -q 2>/dev/null)" ]]; then
  running=true
  check COMPOSE_SERVICES "web/tools/model running" bash -c \
    '[[ "$("${@}" ps --status running --services | LC_ALL=C sort | tr "\n" " ")" == "model tools web " ]]' \
    _ "${COMPOSE[@]}"
  check OPENSHELL_AGENT "OpenShell sandbox ready; agent reaches tools and model" spark_openshell status
  check TOOLS_HEALTH "tools health ready" bash -c \
    '"${@:2}" exec -T tools python -c '\''import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read().decode())'\'' >"$1"' \
    _ "$tools_health" "${COMPOSE[@]}"
  status_code=0
  status_message="$(spark_agent_status 1 2>&1)" || status_code=$?
  case "$status_code" in
    0)
      printf 'PASS  %-24s %s\n' AGENT_STATUS "ready for research on the prepared scenario"
      research_ready=true
      check REMOTE_PROVIDER "approved inference endpoint reachable" spark_agent_remote_probe
      ;;
    3) printf 'WARN  %-24s %s\n' AGENT_STATUS "$status_message" ;;
    *)
      printf 'FAIL  %-24s %s\n' AGENT_STATUS "$status_message"
      failures=$((failures + 1))
      ;;
  esac
else
  printf 'INFO  %-24s %s\n' SERVICES_STOPPED "application is prepared but not running"
fi

data_args=("${SPARK_SCENARIO_DIR}/manifest.json" "${SPARK_RUNTIME_ROOT}/reports/data/phase-09-qualification.json")
[[ "$running" == false ]] || data_args+=("$tools_health")
python3 "${REPO_ROOT}/scripts/spark/doctor_data.py" "${data_args[@]}" || failures=$((failures + 1))

if (( failures )); then
  spark_log "doctor found ${failures} blocking issue(s); see docs/OPERATIONS.md"
  exit 1
fi
if [[ "$research_ready" == true ]]; then
  spark_log "doctor passed: prepared, running, and ready for research"
elif [[ "$running" == true ]]; then
  spark_log "doctor passed preparation and runtime checks; research is disabled until remote routing is configured"
else
  spark_log "doctor passed preparation checks; the application is not running"
fi

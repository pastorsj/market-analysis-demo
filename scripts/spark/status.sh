#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

[[ $# -eq 0 ]] || spark_die "usage: $0"
spark_require_operator_tools
spark_require_compose_services
spark_require_public_boundary

printf 'DGX Spark Market Shock Investigator\n'
printf '  architecture: %s\n' "$(uname -m)"
printf '  application:  web, agent, tools, model\n'
printf '  agent host:   OpenShell (no Compose fallback)\n'
printf '  browser:      http://localhost:3000\n'

artifact_status() {
  local label=$1 path=$2 mode=${3:-file}
  local state=missing
  if [[ "$mode" == pass ]] && spark_json_status_pass "$path"; then
    state=pass
  elif [[ "$mode" == file && -r "$path" ]]; then
    state=present
  elif [[ -r "$path" ]]; then
    state=invalid
  fi
  printf '  %-13s %s\n' "${label}:" "$state"
}

if [[ -r "${SPARK_MANIFEST_DIR}/models.json" \
    && -d "${SPARK_RUNTIME_ROOT}/models/hf/nemotron-3-embed-1b-bf16-9e0b248" \
    && -d "${SPARK_RUNTIME_ROOT}/models/hf/nemotron-3.5-lightning-nvfp4-bee7596" \
    && -d "${SPARK_RUNTIME_ROOT}/models/hf/nemotron-3.5-lightning-dspark-8a01771" ]]; then
  printf '  %-13s %s\n' "models:" "present"
else
  printf '  %-13s %s\n' "models:" "incomplete"
fi
artifact_status "scenario" "${SPARK_SCENARIO_DIR}/manifest.json"
artifact_status "foundation" "${SPARK_REPORT_DIR}/compatibility.json" pass

printf '\nContainers:\n'
"${COMPOSE[@]}" ps
printf '\nOpenShell:\n'
spark_openshell status || printf '  agent sandbox is not ready\n'

if ! curl --fail --silent --max-time 3 "http://127.0.0.1:3000/health" >/dev/null; then
  printf '\nNOT READY: web is not answering on http://localhost:3000\n'
  exit 1
fi
status_code=0
status_message="$(spark_agent_status 1)" || status_code=$?
case "$status_code" in
  0) printf '\nREADY: http://localhost:3000\n' ;;
  3) printf '\nPREPARED, RESEARCH DISABLED: %s\n' "$status_message"; exit 1 ;;
  *) printf '\nNOT READY: %s\n' "$status_message"; exit 1 ;;
esac

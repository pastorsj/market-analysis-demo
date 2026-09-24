#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

recreate_agent=false
retention_lock_held=false
for argument in "$@"; do
  case "$argument" in
    --recreate-agent) recreate_agent=true ;;
    --retention-lock-held) retention_lock_held=true ;;
    *) spark_die "usage: $0 [--recreate-agent]" ;;
  esac
done
(( $# <= 2 )) || spark_die "usage: $0 [--recreate-agent]"

if [[ "$retention_lock_held" == false ]]; then
  command=("${REPO_ROOT}/scripts/spark/start.sh" --retention-lock-held)
  [[ "$recreate_agent" == false ]] || command+=(--recreate-agent)
  exec python3 "${REPO_ROOT}/scripts/spark/retention.py" guard-start -- "${command[@]}"
fi
retention_lock_fd=${SPARK_RETENTION_LOCK_FD:-}
[[ "$retention_lock_fd" =~ ^[0-9]+$ ]] \
  || spark_die "startup requires the guarded retention lock"
python3 "${REPO_ROOT}/scripts/spark/retention.py" verify-start-lock "$retention_lock_fd" \
  || spark_die "startup could not verify the guarded retention lock"

spark_require_operator_tools
spark_require_compose_services
spark_require_public_boundary
if [[ "$recreate_agent" == true ]]; then
  if ! python3 -c \
      'import sys; sys.path.insert(0, sys.argv[1]); from scripts.spark.retention import agent_stopped; raise SystemExit(0 if agent_stopped() else 1)' \
      "$REPO_ROOT"; then
    spark_die "explicit agent recreation requires a confirmed stopped runtime; run ./demo stop first"
  fi
fi
# Check only: startup must never rotate or delete an active generation.
python3 "$(dirname "$0")/retention.py" check \
  || spark_die "retention budget check failed; follow the inventory/reset/expiry guidance above"
[[ -r "${SPARK_MANIFEST_DIR}/models.json" ]] \
  || spark_die "model manifest missing; this runtime was not prepared on this machine (see docs/OPERATIONS.md)"
[[ -r "${SPARK_SCENARIO_DIR}/manifest.json" ]] \
  || spark_die "scenario manifest missing; this runtime was not prepared on this machine (see docs/OPERATIONS.md)"
[[ -r "${SPARK_MANIFEST_DIR}/scenario-current.json" ]] \
  || spark_die "scenario publication receipt missing; this runtime was not prepared on this machine (see docs/OPERATIONS.md)"
scenario_id="$(jq -r '.scenario_id' "${SPARK_SCENARIO_DIR}/manifest.json")"
[[ "$scenario_id" == "$(jq -r '.scenario_id' "${SPARK_MANIFEST_DIR}/scenario-current.json")" ]] \
  || spark_die "published scenario identity drift"
[[ "$(sha256sum "${SPARK_SCENARIO_DIR}/manifest.json" | cut -d' ' -f1)" == \
    "$(jq -r '.manifest_sha256' "${SPARK_MANIFEST_DIR}/scenario-current.json")" ]] \
  || spark_die "published scenario manifest digest drift"
[[ -r "${SPARK_MANIFEST_DIR}/market-prep.json" ]] \
  || spark_die "market-prep image receipt missing; this runtime was not prepared on this machine (see docs/OPERATIONS.md)"
[[ "$(docker image inspect market-shock/market-prep:phase9 --format '{{.Id}}')" == \
    "$(jq -r '.image_id' "${SPARK_MANIFEST_DIR}/market-prep.json")" ]] \
  || spark_die "market-prep image identity drift"
docker run --rm --pull never --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "${SPARK_RUNTIME_ROOT}:${SPARK_RUNTIME_ROOT}:ro" --entrypoint python \
  market-shock/market-prep:phase9 -c \
  'from pathlib import Path; from scripts.data.artifact_contract import load_manifest; load_manifest(Path("/srv/market-shock/scenario/manifest.json"))'
spark_json_status_pass "${SPARK_REPORT_DIR}/compatibility.json" \
  || spark_die "foundation compatibility gate is missing or failed; this runtime was not prepared on this machine (see docs/OPERATIONS.md)"
for model in \
  nemotron-3-embed-1b-bf16-9e0b248 \
  nemotron-3.5-lightning-nvfp4-bee7596 \
  nemotron-3.5-lightning-dspark-8a01771; do
  [[ -d "${SPARK_RUNTIME_ROOT}/models/hf/${model}" ]] \
    || spark_die "Compose-visible model path is missing: ${model}; this runtime was not prepared on this machine (see docs/OPERATIONS.md)"
done
docker image inspect "$SPARK_MODEL_IMAGE" >/dev/null 2>&1 \
  || spark_die "the exact vLLM image is absent; this runtime was not prepared on this machine (see docs/OPERATIONS.md)"
[[ -r "${SPARK_MANIFEST_DIR}/runtime-images.json" ]] \
  || spark_die "runtime image receipt missing; run scripts/spark/build-runtime.sh"
[[ -f "${SPARK_MANIFEST_DIR}/runtime-images.json" && ! -L "${SPARK_MANIFEST_DIR}/runtime-images.json" ]] \
  || spark_die "runtime image receipt must be a regular file"
runtime_rows="$(python3 "${REPO_ROOT}/scripts/spark/process_contract.py" receipt "${SPARK_MANIFEST_DIR}/runtime-images.json")" || spark_die "runtime image receipt is malformed"
[[ "$(wc -l <<<"$runtime_rows")" -eq 4 ]] || spark_die "runtime image receipt is incomplete"
declare -A runtime_image_ids
declare -A receipt_build_inputs
while IFS=$'\t' read -r service image image_id build_input; do
  runtime_image_ids[$service]="$image_id"
  [[ "$(docker image inspect "$image" --format '{{.Id}}')" == "$image_id" ]] \
    || spark_die "runtime image identity drift: $service"
  if [[ "$service" != model ]]; then
    receipt_build_inputs[$service]="$build_input"
    [[ "$(docker image inspect "$image_id" --format '{{index .Config.Labels "com.nvidia.market-shock.build-input-schema"}}')" == "market-shock-build-input-v1" ]] \
      || spark_die "runtime image build-input schema drift: $service"
    [[ "$(docker image inspect "$image_id" --format '{{index .Config.Labels "com.nvidia.market-shock.build-input-sha256"}}')" == "$build_input" ]] \
      || spark_die "runtime image build-input digest drift: $service"
  fi
done <<<"$runtime_rows"
verify_current_build_inputs() {
  python3 "${REPO_ROOT}/scripts/spark/build_inputs.py" --json | jq -e \
    --arg web "${receipt_build_inputs[web]}" --arg agent "${receipt_build_inputs[agent]}" \
    --arg tools "${receipt_build_inputs[tools]}" \
    '.schema_version == "market-shock-build-input-v1" and .services == {web: $web, agent: $agent, tools: $tools}' \
    >/dev/null
}
verify_current_build_inputs || spark_die "runtime images are stale for the current source tree"
compose_config="$("${COMPOSE[@]}" config --format json)"
python3 "$(dirname "$0")/process_contract.py" compose <<<"$compose_config" \
  || spark_die "Compose executable contract drift"
if [[ -z "$("${COMPOSE[@]}" ps --status running -q web)" ]] \
    && timeout 1 bash -c '</dev/tcp/127.0.0.1/3000' 2>/dev/null
then
  spark_die "host port 3000 is occupied by a process outside this Compose web service"
fi

on_failure() {
  local status=$?
  spark_log "startup failed; collecting safe diagnostics"
  "$(dirname "$0")/collect-diagnostics.sh" >&2 || true
  exit "$status"
}
trap on_failure ERR
spark_openshell verify
spark_prepare_web_bridge
spark_log "starting tools and model from local artifacts"
"${COMPOSE[@]}" up -d --no-build --pull never --no-deps --wait \
  --wait-timeout "${SPARK_START_TIMEOUT_SECONDS:-600}" tools model
spark_log "starting the agent only through the prepared OpenShell sandbox"
if [[ "$recreate_agent" == true ]]; then
  # This explicit maintenance mode runs only after the exact prepared tools and
  # model images are healthy.  OpenShell deletes/recreates only market-agent;
  # host state and traces remain mounted and no unsandboxed fallback exists.
  spark_openshell recreate
else
  spark_openshell start
fi
spark_agent_remote_probe >&2 \
  || spark_die "remote inference admission failed; run ./demo stop, confirm host network readiness, then run ./demo start --recreate-agent"
"${COMPOSE[@]}" up -d --no-build --pull never --no-deps --wait \
  --wait-timeout "${SPARK_START_TIMEOUT_SECONDS:-600}" web
for service in web tools model; do
  container_id="$("${COMPOSE[@]}" ps --status running -q "$service")"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] \
    || spark_die "running container identity is ambiguous: $service"
  [[ "$(docker inspect "$container_id" --format '{{.Image}}')" == "${runtime_image_ids[$service]}" ]] \
    || spark_die "running container image drift: $service"
  image_entrypoint="$(docker image inspect "${runtime_image_ids[$service]}" --format '{{json .Config.Entrypoint}}')"
  if [[ "$service" == model ]]; then
    expected_command="$(jq -c '.services.model.command' <<<"$compose_config")"
  else
    expected_command="$(docker image inspect "${runtime_image_ids[$service]}" --format '{{json .Config.Cmd}}')"
  fi
  container_entrypoint="$(docker inspect "$container_id" --format '{{json .Config.Entrypoint}}')"
  container_command="$(docker inspect "$container_id" --format '{{json .Config.Cmd}}')"
  python3 "$(dirname "$0")/process_contract.py" running "$service" \
    "$image_entrypoint" "$container_entrypoint" "$expected_command" "$container_command" \
    || spark_die "running container executable drift: $service"
done
verify_current_build_inputs || spark_die "source tree changed while the application was starting"
curl --fail --silent --show-error --max-time 5 \
  "http://127.0.0.1:3000/health" >/dev/null
spark_require_public_boundary
spark_openshell status >/dev/null
status_code=0
status_message="$(spark_agent_status 30)" || status_code=$?
trap - ERR
case "$status_code" in
  0) printf 'READY FOR DEMO: http://localhost:3000\n' ;;
  3)
    # Everything local is up; the browser shows the same reason and research fails closed.
    spark_log "$status_message"
    printf 'PREPARED, RESEARCH DISABLED: http://localhost:3000\n'
    ;;
  *)
    spark_log "agent is not ready: $status_message"
    "$(dirname "$0")/collect-diagnostics.sh" >&2 || true
    exit 1
    ;;
esac

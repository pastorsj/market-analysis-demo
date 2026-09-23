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
spark_require_four_services
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
  || spark_die "model manifest missing; run ./demo prepare"
[[ -r "${SPARK_SCENARIO_DIR}/manifest.json" ]] \
  || spark_die "scenario manifest missing; run ./demo prepare"
[[ -r "${SPARK_MANIFEST_DIR}/scenario-current.json" ]] \
  || spark_die "scenario publication receipt missing; run ./demo prepare"
scenario_id="$(jq -r '.scenario_id' "${SPARK_SCENARIO_DIR}/manifest.json")"
[[ "$scenario_id" == "$(jq -r '.scenario_id' "${SPARK_MANIFEST_DIR}/scenario-current.json")" ]] \
  || spark_die "published scenario identity drift"
[[ "$(sha256sum "${SPARK_SCENARIO_DIR}/manifest.json" | cut -d' ' -f1)" == \
    "$(jq -r '.manifest_sha256' "${SPARK_MANIFEST_DIR}/scenario-current.json")" ]] \
  || spark_die "published scenario manifest digest drift"
[[ -r "${SPARK_MANIFEST_DIR}/market-prep.json" ]] \
  || spark_die "market-prep image receipt missing; run ./demo prepare"
[[ "$(docker image inspect market-shock/market-prep:phase9 --format '{{.Id}}')" == \
    "$(jq -r '.image_id' "${SPARK_MANIFEST_DIR}/market-prep.json")" ]] \
  || spark_die "market-prep image identity drift"
docker run --rm --pull never --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "${SPARK_RUNTIME_ROOT}:${SPARK_RUNTIME_ROOT}:ro" --entrypoint python \
  market-shock/market-prep:phase9 -c \
  'from pathlib import Path; from scripts.data.artifact_contract import load_manifest; load_manifest(Path("/srv/market-shock/scenario/manifest.json"))'
spark_json_status_pass "${SPARK_REPORT_DIR}/compatibility.json" \
  || spark_die "foundation compatibility gate is missing or failed; run ./demo prepare"
for model in \
  nemotron-3-embed-1b-bf16-9e0b248 \
  nemotron-3.5-lightning-nvfp4-bee7596 \
  nemotron-3.5-lightning-dspark-8a01771; do
  [[ -d "${SPARK_RUNTIME_ROOT}/models/hf/${model}" ]] \
    || spark_die "Compose-visible model path is missing: ${model}; run ./demo prepare"
done
docker image inspect "$SPARK_MODEL_IMAGE" >/dev/null 2>&1 \
  || spark_die "the exact vLLM image is absent; run ./demo prepare"
[[ -r "${SPARK_MANIFEST_DIR}/runtime-images.json" ]] \
  || spark_die "runtime image receipt missing; run ./demo prepare"
[[ -f "${SPARK_MANIFEST_DIR}/runtime-images.json" && ! -L "${SPARK_MANIFEST_DIR}/runtime-images.json" ]] \
  || spark_die "runtime image receipt must be a regular file"
runtime_rows="$(python3 - "${SPARK_MANIFEST_DIR}/runtime-images.json" "$SPARK_MODEL_IMAGE" <<'PY'
import json, os, pathlib, re, stat, sys

def closed_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result

path, model_name = pathlib.Path(sys.argv[1]), sys.argv[2]
try:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600:
            raise ValueError("receipt file")
        chunks, size = [], 0
        while chunk := os.read(descriptor, 65536):
            size += len(chunk)
            if size > 65536:
                raise ValueError("receipt size")
            chunks.append(chunk)
        after, bound = os.fstat(descriptor), path.lstat()
    finally:
        os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(after) or identity(after) != identity(bound):
        raise ValueError("receipt changed")
    document = json.loads(b"".join(chunks), object_pairs_hook=closed_object)
    if set(document) != {"schema_version", "images"} or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("receipt shape")
    images = document["images"]
    names = {
        "web": "market-shock-web:latest",
        "agent": "market-shock-agent:latest",
        "tools": "market-shock-tools:latest",
        "model": model_name,
    }
    if not isinstance(images, dict) or set(images) != set(names):
        raise ValueError("image roles")
    for service in ("web", "agent", "tools", "model"):
        row = images[service]
        fields = {"name", "id", "build_input_sha256"} if service != "model" else {"name", "id"}
        if not isinstance(row, dict) or set(row) != fields or row["name"] != names[service]:
            raise ValueError("image row")
        if re.fullmatch(r"sha256:[a-f0-9]{64}", str(row["id"])) is None:
            raise ValueError("image identity")
        build = row.get("build_input_sha256", "-")
        if service != "model" and re.fullmatch(r"[a-f0-9]{64}", str(build)) is None:
            raise ValueError("build identity")
        print(service, row["name"], row["id"], build, sep="\t")
except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
    raise SystemExit(1)
PY
)" || spark_die "runtime image receipt is malformed"
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
  python3 - "$REPO_ROOT" "${receipt_build_inputs[web]}" "${receipt_build_inputs[agent]}" "${receipt_build_inputs[tools]}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from scripts.spark.build_inputs import SCHEMA_VERSION, build_input_digests

expected = dict(zip(("web", "agent", "tools"), sys.argv[2:], strict=True))
observed = build_input_digests(root)
raise SystemExit(0 if SCHEMA_VERSION == "market-shock-build-input-v1" and observed == expected else 1)
PY
}
verify_current_build_inputs || spark_die "runtime images are stale for the current source tree"
"${COMPOSE[@]}" --profile image-only config --format json \
  | python3 "$(dirname "$0")/process_contract.py" compose \
  || spark_die "Compose executable contract drift"
if [[ -z "$("${COMPOSE[@]}" ps --status running -q web)" ]] \
    && python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.settimeout(0.25)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", 3000)) == 0 else 1)
PY
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
"${COMPOSE[@]}" up -d --no-build --pull never --no-deps --wait \
  --wait-timeout "${SPARK_START_TIMEOUT_SECONDS:-600}" web
for service in web tools model; do
  container_id="$("${COMPOSE[@]}" ps --status running -q "$service")"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] \
    || spark_die "running container identity is ambiguous: $service"
  [[ "$(docker inspect "$container_id" --format '{{.Image}}')" == "${runtime_image_ids[$service]}" ]] \
    || spark_die "running container image drift: $service"
  image_entrypoint="$(docker image inspect "${runtime_image_ids[$service]}" --format '{{json .Config.Entrypoint}}')"
  image_command="$(docker image inspect "${runtime_image_ids[$service]}" --format '{{json .Config.Cmd}}')"
  container_entrypoint="$(docker inspect "$container_id" --format '{{json .Config.Entrypoint}}')"
  container_command="$(docker inspect "$container_id" --format '{{json .Config.Cmd}}')"
  python3 "$(dirname "$0")/process_contract.py" running "$service" \
    "$image_entrypoint" "$image_command" "$container_entrypoint" "$container_command" \
    || spark_die "running container executable drift: $service"
done
verify_current_build_inputs || spark_die "source tree changed while the application was starting"
curl --fail --silent --show-error --max-time 5 \
  "http://127.0.0.1:3000/health" >/dev/null
spark_wait_api_ready
spark_require_public_boundary
spark_openshell status
spark_log "prepared runtime and API are ready; use ./demo test full for live investigation verification"
trap - ERR
printf 'READY FOR DEMO: http://localhost:3000\n'

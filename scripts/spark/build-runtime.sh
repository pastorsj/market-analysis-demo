#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

[[ $# -eq 0 ]] || spark_die "usage: $0"
spark_require_operator_tools
spark_require_four_services
spark_require_public_boundary

build_snapshot="$(mktemp -d /tmp/market-shock-build.XXXXXX)"
cleanup_build_snapshot() {
  chmod -R u+w -- "$build_snapshot" 2>/dev/null || true
  rm -rf -- "$build_snapshot"
}
trap cleanup_build_snapshot EXIT
build_inputs="$(python3 "${REPO_ROOT}/scripts/spark/build_inputs.py" --snapshot-root "$build_snapshot")"
MARKET_SHOCK_WEB_BUILD_INPUT_SHA256="$(jq -er '.services.web | select(test("^[a-f0-9]{64}$"))' <<<"$build_inputs")"
MARKET_SHOCK_AGENT_BUILD_INPUT_SHA256="$(jq -er '.services.agent | select(test("^[a-f0-9]{64}$"))' <<<"$build_inputs")"
MARKET_SHOCK_TOOLS_BUILD_INPUT_SHA256="$(jq -er '.services.tools | select(test("^[a-f0-9]{64}$"))' <<<"$build_inputs")"
export MARKET_SHOCK_WEB_BUILD_INPUT_SHA256 MARKET_SHOCK_AGENT_BUILD_INPUT_SHA256 MARKET_SHOCK_TOOLS_BUILD_INPUT_SHA256
spark_log "building local web, agent, and tools images"
docker build --tag market-shock-web:latest \
  --build-arg "MARKET_SHOCK_BUILD_INPUT_SHA256=${MARKET_SHOCK_WEB_BUILD_INPUT_SHA256}" \
  "$build_snapshot/apps/web"
docker build --tag market-shock-agent:latest \
  --build-arg "MARKET_SHOCK_BUILD_INPUT_SHA256=${MARKET_SHOCK_AGENT_BUILD_INPUT_SHA256}" \
  "$build_snapshot/services/agent"
docker build --tag market-shock-tools:latest \
  --build-arg "MARKET_SHOCK_BUILD_INPUT_SHA256=${MARKET_SHOCK_TOOLS_BUILD_INPUT_SHA256}" \
  "$build_snapshot/services/tools"
post_build_inputs="$(python3 "${REPO_ROOT}/scripts/spark/build_inputs.py" --json)"
[[ "$post_build_inputs" == "$build_inputs" ]] \
  || spark_die "repository build inputs changed while frozen images were building"
label_schema="com.nvidia.market-shock.build-input-schema"
label_digest="com.nvidia.market-shock.build-input-sha256"
declare -A built_image_ids
for service in web agent tools; do
  image="market-shock-${service}:latest"
  variable="MARKET_SHOCK_${service^^}_BUILD_INPUT_SHA256"
  image_id="$(docker image inspect "$image" --format '{{.Id}}')"
  [[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] \
    || spark_die "runtime image identity is invalid: ${service}"
  built_image_ids[$service]="$image_id"
  [[ "$(docker image inspect "$image_id" --format "{{index .Config.Labels \"${label_schema}\"}}")" == "market-shock-build-input-v1" ]] \
    || spark_die "runtime image build-input schema label mismatch: ${service}"
  [[ "$(docker image inspect "$image_id" --format "{{index .Config.Labels \"${label_digest}\"}}")" == "${!variable}" ]] \
    || spark_die "runtime image build-input digest label mismatch: ${service}"
done
spark_log "pulling the production-pinned vLLM image"
"${COMPOSE[@]}" pull model
docker image inspect "$SPARK_MODEL_IMAGE" >/dev/null 2>&1 \
  || spark_die "the exact vLLM image is not present after pull"
spark_log "runtime images are ready"
for service in web agent tools; do
  [[ "$(docker image inspect "market-shock-${service}:latest" --format '{{.Id}}')" == "${built_image_ids[$service]}" ]] \
    || spark_die "runtime image tag changed before receipt publication: ${service}"
done
runtime_tmp="$(mktemp "${SPARK_MANIFEST_DIR}/runtime-images.XXXXXX")"
jq -n \
  --arg web "${built_image_ids[web]}" \
  --arg agent "${built_image_ids[agent]}" \
  --arg tools "${built_image_ids[tools]}" \
  --arg model "$(docker image inspect "$SPARK_MODEL_IMAGE" --format '{{.Id}}')" \
  --arg web_build "$MARKET_SHOCK_WEB_BUILD_INPUT_SHA256" \
  --arg agent_build "$MARKET_SHOCK_AGENT_BUILD_INPUT_SHA256" \
  --arg tools_build "$MARKET_SHOCK_TOOLS_BUILD_INPUT_SHA256" \
  '{schema_version:1,images:{web:{name:"market-shock-web:latest",id:$web,build_input_sha256:$web_build},agent:{name:"market-shock-agent:latest",id:$agent,build_input_sha256:$agent_build},tools:{name:"market-shock-tools:latest",id:$tools,build_input_sha256:$tools_build},model:{name:"vllm/vllm-openai:v0.27.1@sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967",id:$model}}}' \
  >"$runtime_tmp"
spark_atomic_replace "$runtime_tmp" "${SPARK_MANIFEST_DIR}/runtime-images.json"

#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

# Diagnostics deliberately avoid environment dumps and container inspection.
# All captured command output is bounded and scrubbed before it reaches disk.
spark_require_runtime_root
diagnostic_dir="${SPARK_RUNTIME_ROOT}/reports/diagnostics"
mkdir -p "$diagnostic_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
bundle="${diagnostic_dir}/diagnostic-${stamp}.log"
temp=$(mktemp "${diagnostic_dir}/.diagnostic.XXXXXX")
chmod 0600 "$temp"

redact() {
  sed -E \
    -e 's/([Aa]uthorization|[Xx]-?[Aa]pi-?[Kk]ey|[Tt]oken|[Cc]ookie|[Ss]ecret|[Pp]assword)[=: ][^[:space:]]+/\1=[REDACTED]/g' \
    -e 's/(api_key|access_token|refresh_token|client_secret)"?[=: ]+"?[^",[:space:]]+/\1=[REDACTED]/g' \
    -e 's/(https?:\/\/)[^/@[:space:]]+@/\1[REDACTED]@/g'
}

{
  printf 'schema_version=1\n'
  printf 'generated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'stage=%s\n' "${SPARK_FAILURE_STAGE:-unknown}"
  printf 'architecture=%s\n' "$(uname -m)"
  docker --version 2>/dev/null || true
  docker compose version 2>/dev/null || true
  printf 'artifacts models=%s scenario=%s foundation=%s\n' \
    "$([[ -r "${SPARK_MANIFEST_DIR}/models.json" ]] && echo present || echo missing)" \
    "$([[ -r "${SPARK_SCENARIO_DIR}/manifest.json" ]] && echo present || echo missing)" \
    "$(spark_json_status_pass "${SPARK_REPORT_DIR}/compatibility.json" && echo pass || echo missing-or-failed)"
  printf '\ncompose_state:\n'
  timeout 10 "${COMPOSE[@]}" ps 2>&1 || true
  printf '\nhealth:\n'
  for endpoint in http://127.0.0.1:3000/health http://127.0.0.1:3000/api/status; do
    printf '%s ' "$endpoint"
    timeout 5 curl --silent --show-error --max-time 3 "$endpoint" 2>&1 | head -c 2048 || true
    printf '\n'
  done
  printf '\nport_3000:\n'
  timeout 5 ss -ltnp 'sport = :3000' 2>&1 || true
  printf '\nhost_resources:\n'
  free -h 2>&1 || true
  df -h "${SPARK_RUNTIME_ROOT}" 2>&1 || true
  timeout 10 nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu --format=csv 2>&1 || true
  timeout 10 nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>&1 || true
  printf '\nservice_logs_tail:\n'
  timeout 15 "${COMPOSE[@]}" logs --no-color --tail 80 web tools model 2>&1 | tail -c 65536 || true
} | redact >"$temp"

mv -f -- "$temp" "$bundle"
chmod 0600 "$bundle"
printf 'diagnostic_bundle=%s\n' "$bundle"

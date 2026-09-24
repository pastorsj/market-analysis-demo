#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

if [[ $# -eq 0 ]]; then
  exec python3 "${REPO_ROOT}/scripts/spark/retention.py" guard-start -- \
    "$0" --retention-lock-held
fi
[[ $# -eq 1 && "$1" == --retention-lock-held ]] || spark_die "usage: $0"
retention_lock_fd=${SPARK_RETENTION_LOCK_FD:-}
[[ "$retention_lock_fd" =~ ^[0-9]+$ ]] \
  || spark_die "stop requires the lifecycle/retention lock"
python3 "${REPO_ROOT}/scripts/spark/retention.py" verify-start-lock \
  "$retention_lock_fd" >/dev/null \
  || spark_die "stop requires the lifecycle/retention lock"
spark_require_operator_tools
spark_require_compose_services
spark_openshell stop
"${COMPOSE[@]}" stop web tools model
spark_log "OpenShell agent and application containers stopped; artifacts, state, and networks were preserved"

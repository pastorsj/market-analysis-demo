#!/usr/bin/env bash
# One transport for operator probes and private grading. Never falls back.
set -Eeuo pipefail
export XDG_CONFIG_HOME=/srv/market-shock/openshell/config
export XDG_DATA_HOME=/srv/market-shock/openshell/data
export XDG_STATE_HOME=/srv/market-shock/openshell/state
exec /srv/market-shock/openshell/0.0.116/openshell -g market-shock \
  sandbox exec -n market-agent -- "$@"

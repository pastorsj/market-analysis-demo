#!/usr/bin/env bash
# Run the GPU tool tests inside the local tools image against the prepared scenario.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec docker run --rm --gpus all --ipc host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e PYTHONPATH=/repo/services/tools/src:/repo \
  -e NEMOTRON_EMBED_PATH=/srv/market-shock/models/hf/nemotron-3-embed-1b-bf16-9e0b248 \
  -v /srv/market-shock:/srv/market-shock:ro -v "$ROOT":/repo:ro -w /repo \
  --entrypoint python market-shock-tools:latest \
  -m pytest -q -p no:cacheprovider tests/gpu tests/tools "$@"

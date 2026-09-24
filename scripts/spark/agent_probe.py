#!/usr/bin/env python3
"""Operator probes of the running agent.

  agent_probe.py status [--attempts N]  classify the browser-facing /api/status
      exit 0: ready for research
      exit 3: prepared and healthy, but research is off (remote routing disabled)
      exit 1: not ready (dependency down, wrong scenario, or no answer)
  agent_probe.py remote                 from inside the sandbox, prove the approved
                                        inference endpoint answers (skipped when disabled)

Only a one-line summary is printed; never the raw status document, endpoint, or credential.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS_URL = "http://127.0.0.1:3000/api/status"
SCENARIO_MANIFEST = Path("/srv/market-shock/scenario/manifest.json")
READY, NOT_READY, REMOTE_OFF = 0, 1, 3
REMOTE_OFF_MESSAGE = (
    "prepared, but research needs REMOTE_ROUTING_ENABLED=true with NVIDIA_BASE_URL and "
    "NVIDIA_INFERENCE_API_KEY in the environment file; re-prepare OpenShell and run "
    "./demo start --recreate-agent"
)
# Runs inside the sandbox, where the provider injects the credential placeholder.
REMOTE_PROBE = r"""
import json, os, urllib.request
if os.environ.get("REMOTE_ROUTING_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
    print(json.dumps({"status": "skipped"}))
    raise SystemExit(0)
base = os.environ.get("NVIDIA_BASE_URL", "").rstrip("/")
key = os.environ.get("NVIDIA_INFERENCE_API_KEY", "")
if not base or not key:
    raise SystemExit(1)
try:
    request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(request, timeout=15) as response:
        ok = response.status == 200
except Exception:
    raise SystemExit(1) from None
print(json.dumps({"status": "pass" if ok else "fail"}))
"""


def classify(status: object, scenario_id: str) -> tuple[int, str]:
    """Reduce the public status to what operators need: dependencies, scenario, readiness."""
    if not isinstance(status, dict):
        return NOT_READY, "status is not a JSON object"
    dependencies = status.get("dependencies")
    if not isinstance(dependencies, dict) or not {"tools", "model", "events"} <= set(dependencies):
        return NOT_READY, "status has no dependency report"
    coverage = status.get("coverage")
    served = coverage.get("scenario_id") if isinstance(coverage, dict) else None
    if served != scenario_id:
        return NOT_READY, f"agent serves scenario {served!r}, but {scenario_id!r} is prepared"
    missing = sorted(name for name, ok in dependencies.items() if ok is not True)
    if missing:
        return NOT_READY, "unavailable: " + ", ".join(missing)
    if status.get("ready") is True:
        return READY, "ready"
    if status.get("remote_routing_enabled") is False:
        return REMOTE_OFF, REMOTE_OFF_MESSAGE
    return NOT_READY, str(status.get("reason") or "agent reports not ready")


def fetch_status(url: str = STATUS_URL) -> object:
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def wait_status(attempts: int, scenario_id: str) -> tuple[int, str]:
    code, message = NOT_READY, "no answer from http://localhost:3000/api/status"
    for attempt in range(attempts):
        try:
            code, message = classify(fetch_status(), scenario_id)
        except (OSError, ValueError):
            code, message = NOT_READY, "no answer from http://localhost:3000/api/status"
        if code != NOT_READY:
            break
        if attempt + 1 < attempts:
            time.sleep(1)
    return code, message


def remote_probe() -> tuple[int, str]:
    sys.path.insert(0, str(ROOT))
    from scripts.spark.openshell_runtime import NAME, shell

    try:
        raw = shell(
            "sandbox", "exec", "-n", NAME, "--", "/usr/local/bin/python3.12", "-c", REMOTE_PROBE, timeout=25
        )
        result = json.loads(raw).get("status")
    except (RuntimeError, OSError, ValueError, AttributeError):
        result = "fail"
    if result == "skipped":
        return READY, "remote routing disabled; no remote endpoint to probe"
    if result == "pass":
        return READY, "approved inference endpoint reachable from the sandbox"
    return NOT_READY, (
        "the sandbox could not reach or authenticate to the inference endpoint "
        "(no endpoint, credential, or response detail shown)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--attempts", type=int, default=1)
    status.add_argument("--scenario-manifest", type=Path, default=SCENARIO_MANIFEST)
    commands.add_parser("remote")
    args = parser.parse_args(argv)
    if args.command == "remote":
        code, message = remote_probe()
    else:
        try:
            scenario_id = json.loads(args.scenario_manifest.read_text())["scenario_id"]
        except (OSError, ValueError, KeyError, TypeError):
            print("prepared scenario manifest is unreadable")
            return NOT_READY
        code, message = wait_status(max(1, args.attempts), scenario_id)
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

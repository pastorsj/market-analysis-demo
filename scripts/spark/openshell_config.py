"""Prepare endpoint-bound OpenShell providers without persisting credentials.

Reads the operator environment file (COMPOSE_ENV_FILE, else .env, else
.env.spark.example), maps it onto the agent's fixed environment, stores the
credentials only in gateway-managed providers, and writes the non-secret rest
to the sandbox launch file. Needs PyYAML (host python3 or the agent virtualenv).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path("/srv/market-shock/openshell")
CLI = RUNTIME / "0.0.116/openshell"
PYTHON = "/usr/local/bin/python3.12"
# Agent variables that do not depend on operator configuration.
FIXED_ENV = {
    "MARKET_SHOCK_STATE_ROOT": "/srv/market-shock/state",
    "MARKET_SHOCK_SCENARIO_ROOT": "/srv/market-shock/scenario",
    "MARKET_SHOCK_EVENT_CATALOG_ROOT": "/srv/market-shock/events/current",
    "NEMO_RELAY_TRACE_DIRECTORY": "/srv/market-shock/traces",
}
# Agent variable -> (environment-file key, default when unset or empty).
FILE_ENV = {
    "REMOTE_ROUTING_ENABLED": ("REMOTE_ROUTING_ENABLED", "false"),
    "NVIDIA_BASE_URL": ("NVIDIA_BASE_URL", ""),
    "NVIDIA_INFERENCE_API_KEY": ("NVIDIA_INFERENCE_API_KEY", ""),
    "NEMO_RELAY_LANGSMITH_ENABLED": ("LANGSMITH_TRACING", "false"),
    "LANGSMITH_API_KEY": ("LANGSMITH_API_KEY", ""),
    "LANGSMITH_PROJECT": ("LANGSMITH_PROJECT", ""),
    "LANGSMITH_PROJECT_URL": ("LANGSMITH_PROJECT_URL", ""),
    "LANGSMITH_ENDPOINT": ("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
}
SECRET_KEYS = {"NVIDIA_INFERENCE_API_KEY", "LANGSMITH_API_KEY"}
SAFE_KEYS = (set(FIXED_ENV) | set(FILE_ENV)) - SECRET_KEYS


class PreparationError(RuntimeError):
    """A secret-safe preparation failure."""


def command(args: list[str], *, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreparationError(
            "Configuration command could not complete; no launch files were finalized."
        ) from None
    if result.returncode:
        # Neither argv nor CLI stderr is safe to forward after credential input.
        raise PreparationError(
            "Configuration command failed; inspect gateway readiness without exposing credentials."
        )
    return result.stdout


def cli_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SECRET_KEYS}
    for key, folder in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        env[key] = str(RUNTIME / folder)
    return env


def openshell(*args: str, credential: tuple[str, str] | None = None) -> str:
    env = cli_env()
    if credential:
        env[credential[0]] = credential[1]
    try:
        return command([str(CLI), "-g", "market-shock", *args], env=env)
    except PreparationError:
        stage = " ".join(args[:3] if args[:2] == ("provider", "profile") else args[:2])
        raise PreparationError(
            f"OpenShell {stage} failed; command output withheld to protect credentials."
        ) from None


def env_file() -> Path:
    if os.environ.get("COMPOSE_ENV_FILE"):
        return Path(os.environ["COMPOSE_ENV_FILE"])
    return ROOT / ".env" if (ROOT / ".env").is_file() else ROOT / ".env.spark.example"


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (comments, blank lines, and surrounding quotes allowed)."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.removeprefix("export ").partition("=")
        if not separator or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key.strip()):
            raise PreparationError("Environment file has a line that is not KEY=VALUE.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def agent_environment(values: dict[str, str]) -> dict[str, str]:
    """The agent's complete environment, including credentials (never written to disk)."""
    mapped = {name: values.get(key) or default for name, (key, default) in FILE_ENV.items()}
    return {**FIXED_ENV, **mapped}


def enabled(value: str) -> bool:
    if value.lower() not in {"true", "false", "1", "0", ""}:
        raise PreparationError("Enable flags must be explicit booleans.")
    return value.lower() in {"true", "1"}


def endpoint(url: str) -> dict:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c in url for c in "\n\r*{}")
        ):
            raise ValueError
        return {
            "host": parsed.hostname,
            "port": parsed.port or (443 if parsed.scheme == "https" else 80),
            "path": parsed.path.rstrip("/") + "/**",
            "protocol": "rest",
            "access": "full",
            "enforcement": "enforce",
        }
    except ValueError:
        raise PreparationError(
            "Provider URL must be an HTTP(S) endpoint without embedded credentials, query, or wildcard."
        ) from None


def project_link(url: str) -> str:
    """Validate browser presentation metadata without creating an egress grant."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "smith.langchain.com"
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c in url for c in "\n\r*{}")
            or re.fullmatch(r"/o/[A-Za-z0-9-]+/projects/p/[A-Za-z0-9-]+", parsed.path) is None
        ):
            raise ValueError
        return url
    except ValueError:
        raise PreparationError(
            "LangSmith project link must be an approved credential-free HTTPS project URL."
        ) from None


def prepare_inputs(environment: dict[str, str], policy: dict) -> tuple[dict, dict, list]:
    safe = {key: str(value) for key, value in environment.items() if key in SAFE_KEYS and value is not None}
    project = safe.get("LANGSMITH_PROJECT_URL", "").strip()
    tracing = enabled(safe.get("NEMO_RELAY_LANGSMITH_ENABLED", "false"))
    if tracing and not project:
        raise PreparationError("Enabled LangSmith export requires an explicit demo project link.")
    if project and not tracing:
        raise PreparationError("LangSmith project link requires enabled trace export.")
    if project:
        safe["LANGSMITH_PROJECT_URL"] = project_link(project)
    profiles = []
    for flag, url_key, secret_key, stem, category in (
        (
            "REMOTE_ROUTING_ENABLED",
            "NVIDIA_BASE_URL",
            "NVIDIA_INFERENCE_API_KEY",
            "market-inference",
            "inference",
        ),
        (
            "NEMO_RELAY_LANGSMITH_ENABLED",
            "LANGSMITH_ENDPOINT",
            "LANGSMITH_API_KEY",
            "market-langsmith",
            "other",
        ),
    ):
        if not enabled(safe.get(flag, "false")):
            continue
        if not environment.get(secret_key) or not safe.get(url_key):
            raise PreparationError("An enabled provider is missing its endpoint or credential.")
        bound = endpoint(safe[url_key])
        profile = {
            "display_name": stem,
            "category": category,
            "inference_capable": category == "inference",
            "credentials": [{"name": "api-key", "env_vars": [secret_key], "required": True}],
            "endpoints": [bound],
            "binaries": [PYTHON],
        }
        identity = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:12]
        profile["id"] = f"{stem}-{identity}"
        profiles.append((profile, secret_key, environment[secret_key]))
        policy["network_policies"][stem.replace("-", "_")] = {
            "name": stem,
            "endpoints": [bound],
            "binaries": [{"path": PYTHON}],
        }
    return safe, policy, profiles


def private_json(path: Path, value: object) -> None:
    # Refuse symlink destinations rather than following operator-controlled links.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")


def prepare(output: Path, base_policy: Path) -> None:
    # A failure invalidates the previous launch receipt instead of leaving stale readiness.
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = output / "providers.json"
    receipt.unlink(missing_ok=True)
    safe, policy, profiles = prepare_inputs(
        agent_environment(read_env_file(env_file())), yaml.safe_load(base_policy.read_text())
    )
    openshell("status")
    openshell("settings", "set", "--global", "--key", "providers_v2_enabled", "--value", "true", "--yes")
    existing = set(openshell("provider", "list", "--names", "--limit", "1000").splitlines())
    names = []
    for profile, key, secret in profiles:
        path = output / f"{profile['id']}.yaml"
        private_json(path, profile)  # JSON is a strict YAML subset; no secret fields.
        name = profile["id"]
        if name not in existing:
            openshell("provider", "profile", "lint", "-f", str(path))
            # Profile import is create-only. An interrupted earlier run must be
            # reconciled explicitly rather than guessing whether an error is benign.
            openshell("provider", "profile", "import", "-f", str(path))
            openshell(
                "provider",
                "create",
                "--name",
                name,
                "--type",
                name,
                "--credential",
                key,
                credential=(key, secret),
            )
        else:
            openshell("provider", "update", name, "--credential", key, credential=(key, secret))
        names.append(name)
    private_json(output / "env.json", safe)
    private_json(output / "policy.yaml", policy)
    private_json(receipt, names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RUNTIME / "prepared")
    parser.add_argument("--policy", type=Path, default=ROOT / "scripts/spark/openshell/policy.yaml")
    args = parser.parse_args()
    try:
        prepare(args.output, args.policy)
    except PreparationError as error:
        raise SystemExit(str(error)) from None
    except (KeyError, ValueError, OSError, yaml.YAMLError):
        raise SystemExit(
            "OpenShell configuration preparation failed; no credentials or command output were printed."
        ) from None
    print("OpenShell launch configuration prepared; credentials are gateway-managed.")


if __name__ == "__main__":
    main()

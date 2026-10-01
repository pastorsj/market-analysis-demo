"""Operate the single prepared OpenShell agent; never launch an unsandboxed agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
# Runs as a plain script; sibling modules are imported lazily below.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "0.1.2"
RUNTIME = Path("/srv/market-shock/openshell")
CLI = RUNTIME / VERSION / "openshell"
GATEWAY = CLI.with_name("openshell-gateway")
GATEWAY_NAME = "market-shock"
GATEWAY_SERVICE = "market-shock-openshell.service"
GATEWAY_CONFIG = ROOT / "scripts/spark/openshell/gateway.toml"
BASE_POLICY = ROOT / "scripts/spark/openshell/policy.yaml"
PREPARED = RUNTIME / "prepared"
RUNTIME_IMAGES = {
    "supervisor": f"ghcr.io/nvidia/openshell/supervisor:{VERSION}",
    "sandbox": f"ghcr.io/nvidia/openshell/sandbox:{VERSION}",
}
NAME = "market-agent"
AGENT_PORT = 2024
AGENT_COMMAND = [
    "/usr/local/bin/python3.12",
    "-m",
    "uvicorn",
    "market_agent.app:app",
    "--host",
    "127.0.0.1",
    "--port",
    str(AGENT_PORT),
]
CPU, MEMORY, MEMORY_BYTES, PIDS_LIMIT = "4", "4Gi", 4 * 1024**3, 512
DATA_MOUNTS = ("scenario", "events", "state", "traces")
WRITABLE_MOUNTS = frozenset({"state", "traces"})
# The Docker driver inserts these read-only paths and MCP revision default into
# every sandbox policy. No other grant or endpoint may differ from the base.
SERVER_READ_ONLY_DEFAULTS = frozenset({"/dev/urandom", "/var/log"})
SERVER_MCP_DEFAULT = {"versions": ["2025-11-25"]}
WORKLOAD_ENTRYPOINT = ["/.openshell/runtime/openshell-sandbox"]
CREDENTIAL_PLACEHOLDER = "openshell:resolve:env:"

RETENTION_LOCKED_ACTIONS = frozenset({"launch", "start", "recreate", "stop"})


def image_identity():
    from scripts.spark.build_inputs import build_input_digests

    info = json.loads(run(["docker", "image", "inspect", "market-shock-agent:latest"]))[0]
    digest = build_input_digests(ROOT)["agent"]
    if info["Config"]["Labels"].get("com.nvidia.market-shock.build-input-sha256") != digest:
        raise RuntimeError("Agent image source identity is stale")
    if any(k.startswith("com.docker.compose.") for k in info["Config"]["Labels"]):
        raise RuntimeError("Agent image must not claim Compose workload ownership")
    return info["Id"], digest


def runtime_image_ids():
    try:
        return {
            role: run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).strip()
            for role, image in RUNTIME_IMAGES.items()
        }
    except RuntimeError:
        raise RuntimeError("Pinned OpenShell supervisor or sandbox image is not local") from None


def prepare_receipt():
    from scripts.spark.openshell_receipt import create_receipt

    for binary in (CLI, GATEWAY):
        if run([binary, "--version"]).strip() != f"{binary.name} {VERSION}":
            raise RuntimeError("OpenShell version mismatch")
    image_id, digest = image_identity()
    files = dict(
        binary=CLI,
        gateway=GATEWAY,
        gateway_config=GATEWAY_CONFIG,
        policy=BASE_POLICY,
        env=PREPARED / "env.json",
        provider_list=PREPARED / "providers.json",
    )
    receipt = create_receipt(
        files, image_id=image_id, source_build_input=digest, runtime_images=runtime_image_ids()
    )
    temporary = PREPARED / "runtime.preparing.json"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(receipt, stream, indent=2)
    temporary.replace(PREPARED / "runtime.json")
    print("OpenShell preparation receipt published")


def verify():
    from scripts.spark.openshell_receipt import validate_receipt

    image_id, digest = image_identity()
    path = PREPARED / "runtime.json"
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError("Unsafe preparation receipt")
    receipt = json.loads(path.read_text())
    validate_receipt(
        receipt, image_id=image_id, source_build_input=digest, runtime_images=runtime_image_ids()
    )
    return receipt


def environment():
    """CLI state (gateway registration and mTLS client bundle) stays in the app runtime."""
    result = os.environ.copy()
    for key, folder in [
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ]:
        result[key] = str(RUNTIME / folder)
    result["NO_COLOR"] = "1"
    return result


def run(args, *, capture=True, timeout=120, discard=False):
    streams = (
        {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if discard
        else {"capture_output": capture}
    )
    try:
        result = subprocess.run(
            [str(v) for v in args],
            cwd=ROOT,
            env=environment(),
            # OpenShell exec and forward wait on an open non-TTY stdin.
            stdin=subprocess.DEVNULL,
            **streams,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # TimeoutExpired includes the complete argv in its public attributes and
        # string representation. Normalize it here so callers cannot accidentally
        # surface provider configuration embedded in an OpenShell command.
        raise RuntimeError(
            f"Operator command timed out ({Path(str(args[0])).name}); no private output was displayed"
        ) from None
    if result.returncode:
        # Arguments or gateway errors can contain private configuration.
        raise RuntimeError(
            f"Operator command failed ({Path(str(args[0])).name}); no private output was displayed"
        )
    return result.stdout if capture and not discard else ""


def shell(*args, **kwargs):
    return run([CLI, "-g", GATEWAY_NAME, *args], **kwargs)


def mounts(*, probe=False):
    rows = []
    for folder in DATA_MOUNTS:
        source = f"/srv/market-shock/{folder}"
        if probe and folder in WRITABLE_MOUNTS:
            source = str(RUNTIME / "probe" / folder)
            Path(source).mkdir(mode=0o700, parents=True, exist_ok=True)
        rows.append(
            dict(
                type="bind",
                source=source,
                target=f"/srv/market-shock/{folder}",
                read_only=folder not in WRITABLE_MOUNTS,
            )
        )
    return rows


def launch(name=NAME, *, probe=False):
    verify()
    wait_dependencies()
    env = json.loads((PREPARED / "env.json").read_text())
    providers = json.loads((PREPARED / "providers.json").read_text())
    image = (
        image_identity()[0]
        if not probe
        else run(["docker", "image", "inspect", "market-shock-agent:latest", "--format", "{{.Id}}"]).strip()
    )
    args = [
        "sandbox",
        "create",
        "--name",
        name,
        "--from",
        image,
        "--policy",
        str(BASE_POLICY),
        "--cpu",
        CPU,
        "--memory",
        MEMORY,
        "--driver-config-json",
        json.dumps({"docker": {"mounts": mounts(probe=probe)}}),
        "--detach",
        "--no-tty",
        "--no-auto-providers",
    ]
    for key, value in env.items():
        if key != "HOME":
            args.extend(["--env", f"{key}={value}"])
    for provider in providers:
        args.extend(["--provider", provider])
    args.extend(["--", *AGENT_COMMAND])
    shell(*args, timeout=300)
    print(f"OpenShell sandbox created: {name}; API health still requires verification")


def provider_layers(provider_ids):
    """Policy layers the gateway derives from each attached provider's profile."""
    layers = {}
    for name in provider_ids:
        profile = json.loads((PREPARED / f"{name}.yaml").read_text())
        body = {key: value for key, value in profile.items() if key != "id"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
        # providers.json (receipt-bound) names each profile by its content digest.
        if profile.get("id") != name or not name.endswith(f"-{digest}"):
            raise RuntimeError("Prepared provider profile differs from its identity")
        key = "_provider_" + name.replace("-", "_")
        layers[key] = {
            "name": key,
            "endpoints": profile["endpoints"],
            "binaries": [{"path": path} for path in profile["binaries"]],
        }
    return layers


def expected_policy(base, provider_ids=()):
    expected = json.loads(json.dumps(base))
    expected["network_policies"].update(provider_layers(provider_ids))
    filesystem = expected["filesystem_policy"]
    filesystem["read_only"] = list(filesystem["read_only"]) + sorted(SERVER_READ_ONLY_DEFAULTS)
    for rule in expected["network_policies"].values():
        for endpoint in rule["endpoints"]:
            if endpoint.get("protocol") == "mcp":
                endpoint.setdefault("mcp", SERVER_MCP_DEFAULT)
    return expected


def _normalized(policy):
    document = json.loads(json.dumps(policy))
    filesystem = document.get("filesystem_policy", {})
    for field in ("read_only", "read_write"):
        paths = filesystem.get(field)
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise RuntimeError("Sandbox filesystem policy is invalid")
        filesystem[field] = sorted(set(paths))
    return document


def validate_policy(base, observed, provider_ids=()):
    """The gateway accepted exactly the base policy plus the prepared provider layers."""
    admission = observed.get("configuration_admission") or {}
    conditions = {row.get("type"): row.get("status") for row in observed.get("conditions") or []}
    if (
        admission.get("state") != "accepted"
        or admission.get("error")
        or admission.get("policy_version") != observed.get("current_policy_version")
        or observed.get("policy_source") != "sandbox"
        or conditions.get("ConfigurationReady") != "True"
    ):
        raise RuntimeError("Sandbox policy is not effective")
    live = observed.get("policy")
    if not isinstance(live, dict):
        raise RuntimeError("Sandbox policy is missing")
    if _normalized(live) != _normalized(expected_policy(base, provider_ids)):
        raise RuntimeError("Sandbox policy differs from preparation")


def wait_dependencies(timeout=600):
    deadline = time.monotonic() + timeout
    while True:
        healthy = True
        for service in ("tools", "model"):
            ids = run(
                [
                    "docker",
                    "ps",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=market-shock",
                    "--filter",
                    f"label=com.docker.compose.service={service}",
                ]
            ).split()
            if len(ids) > 1:
                raise RuntimeError("Dependency container identity is ambiguous")
            if not ids:
                healthy = False
                continue
            state = json.loads(run(["docker", "inspect", ids[0], "--format", "{{json .State}}"]))
            healthy = (
                healthy
                and state.get("Running") is True
                and state.get("Health", {}).get("Status") == "healthy"
            )
        if healthy:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Tools or model did not become healthy; agent was not launched")
        time.sleep(2)


def managed_containers(sandbox_id, *, include_stopped=False):
    """The driver's workload and (while running) supervisor containers for one sandbox."""
    containers = {}
    for role in ("sandbox", "supervisor"):
        ids = run(
            [
                "docker",
                "ps",
                "-aq" if include_stopped else "-q",
                "--filter",
                f"label=openshell.ai/sandbox-id={sandbox_id}",
                "--filter",
                f"label=openshell.ai/isolation-role={role}",
                "--filter",
                f"label=openshell.ai/sandbox-namespace={GATEWAY_NAME}",
            ]
        ).split()
        if role == "supervisor" and include_stopped and not ids:
            continue
        if len(ids) != 1:
            raise RuntimeError("Managed workload identity is ambiguous")
        containers[role] = json.loads(run(["docker", "inspect", ids[0]]))[0]
    return containers


def validate_launch(workload, supervisor, receipt, main_process_spec):
    """Compare the driver's actual launch with preparation without exposing values in errors."""
    error = "Sandbox launch configuration differs from preparation; explicitly recreate the sandbox from prepared inputs"
    try:
        host = workload["HostConfig"]
        if (
            workload["Image"] != receipt["image_id"]
            or workload["Config"]["Entrypoint"] != WORKLOAD_ENTRYPOINT
            or workload["Config"]["User"] != "1000:1000"
            or host["NetworkMode"] != "none"
            or host.get("Privileged")
            or host.get("CapDrop") != ["ALL"]
            or host.get("CapAdd")
            or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
            or host.get("PidsLimit") != PIDS_LIMIT
            or host.get("NanoCpus") != int(CPU) * 1_000_000_000
            or host.get("Memory") != MEMORY_BYTES
        ):
            raise ValueError
        binds = [row for row in workload["Mounts"] if row["Type"] == "bind"]
        expected_binds = {(f"/srv/market-shock/{name}", name in WRITABLE_MOUNTS) for name in DATA_MOUNTS}
        if (
            len(binds) != len(DATA_MOUNTS)
            or {(row["Destination"], row["RW"]) for row in binds} != expected_binds
        ):
            raise ValueError
        if any(row["Source"] != row["Destination"] for row in binds):
            raise ValueError
        if any(
            row["Type"] != "bind" and not row["Destination"].startswith("/.openshell/")
            for row in workload["Mounts"]
        ):
            raise ValueError
        supervisor_host = supervisor["HostConfig"]
        if (
            supervisor["Image"] != receipt["runtime_images"]["supervisor"]
            or supervisor_host["NetworkMode"] != "host"
            or supervisor_host.get("Privileged")
            or supervisor_host.get("CapDrop") != ["ALL"]
            or "host.openshell.internal:127.0.0.1" not in (supervisor_host.get("ExtraHosts") or [])
        ):
            raise ValueError
        spec = json.loads(main_process_spec)
        if spec.get("command") != AGENT_COMMAND or spec.get("tty") is not False:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RuntimeError(error) from None


def validate_providers(prepared_providers, output):
    try:
        rows = json.loads(output)
        if rows.get("next_page_token"):
            raise ValueError
        names = [row["name"] for row in rows["providers"]]
        if (
            not isinstance(prepared_providers, list)
            or len(names) != len(set(names))
            or sorted(names) != sorted(prepared_providers)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RuntimeError("Sandbox provider attachments differ from preparation") from None


def supervisor_main_process_spec(supervisor):
    for entry in supervisor["Config"]["Env"]:
        key, _, value = entry.partition("=")
        if key == "OPENSHELL_MAIN_PROCESS_SPEC":
            return value
    raise RuntimeError("Agent supervisor has no main process")


# Runs inside the sandbox: agent readiness, the prepared non-secret environment,
# and whether each credential variable holds only an OpenShell placeholder.
SANDBOX_PROBE = r"""
import json, os, sys, urllib.request
safe, secret = json.loads(sys.argv[1]), json.loads(sys.argv[2])
health = json.loads(urllib.request.urlopen("http://127.0.0.1:2024/health/ready", timeout=10).read())
print(json.dumps({
    "health": health,
    "env": {key: os.environ.get(key) for key in safe},
    "placeholders": {key: os.environ.get(key, "").startswith(sys.argv[3]) for key in secret if key in os.environ},
}))
"""


def sandbox_probe(prepared_env):
    from scripts.spark.openshell_config import SECRET_KEYS

    safe = sorted(key for key in prepared_env if key != "HOME")
    result = json.loads(
        shell(
            "sandbox",
            "exec",
            "-n",
            NAME,
            "--no-tty",
            "--",
            "/usr/local/bin/python3.12",
            "-c",
            SANDBOX_PROBE,
            json.dumps(safe),
            json.dumps(sorted(SECRET_KEYS)),
            CREDENTIAL_PLACEHOLDER,
            timeout=60,
        )
    )
    if result.get("env") != {key: prepared_env[key] for key in safe}:
        raise RuntimeError("Sandbox environment differs from preparation")
    if not all(result.get("placeholders", {}).values()):
        raise RuntimeError("A credential reached the sandbox as a real value instead of a placeholder")
    return result["health"]


def get_sandbox(name=NAME):
    return json.loads(shell("sandbox", "get", name, "--output", "json"))


def list_sandboxes():
    page = json.loads(shell("sandbox", "list", "--output", "json"))
    if page.get("next_page_token"):
        raise RuntimeError("Agent sandbox identity is ambiguous")
    return page["sandboxes"]


def status():
    receipt = verify()
    observed = get_sandbox()
    conditions = {row.get("type"): row.get("status") for row in observed.get("conditions") or []}
    if observed["phase"] != "Ready" or conditions.get("Ready") != "True":
        raise RuntimeError("Agent sandbox is not ready")
    providers = json.loads((PREPARED / "providers.json").read_text())
    validate_policy(load_base_policy(), observed, providers)
    containers = managed_containers(observed["id"])
    workload, supervisor = containers["sandbox"], containers["supervisor"]
    validate_launch(workload, supervisor, receipt, supervisor_main_process_spec(supervisor))
    validate_providers(providers, shell("sandbox", "provider", "list", NAME, "--output", "json"))
    health = sandbox_probe(json.loads((PREPARED / "env.json").read_text()))
    # Remote routing may be off (research disabled); the sandbox is still healthy
    # when the agent answers and reaches its local tools and model.
    if health.get("service") != "agent" or health.get("tools") is not True or health.get("model") is not True:
        raise RuntimeError("Agent cannot reach its local tools and model")
    config_sha256 = hashlib.sha256(
        json.dumps(workload.get("Config"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    print(
        json.dumps(
            {
                "name": NAME,
                "phase": "Ready",
                "version": VERSION,
                "image_id": receipt["image_id"],
                "supervisor_image_id": receipt["runtime_images"]["supervisor"],
                "sandbox_id": observed["id"],
                "container_id": workload["Id"],
                "container_config_sha256": config_sha256,
                "forward": forward_alive(),
                "tools": health["tools"],
                "model": health["model"],
                "events": health.get("events"),
                "research_ready": health.get("ready") is True,
                "reason": health.get("reason"),
            }
        )
    )


def load_base_policy():
    import yaml

    return yaml.safe_load(BASE_POLICY.read_text())


def ensure_gateway():
    run(["systemctl", "--user", "start", GATEWAY_SERVICE])
    for attempt in range(30):
        try:
            shell("status")
            return
        except RuntimeError:
            if attempt == 29:
                raise RuntimeError("OpenShell gateway is not answering") from None
            time.sleep(1)


def start():
    receipt = verify()
    wait_dependencies()
    ensure_gateway()
    matching = [row for row in list_sandboxes() if row["name"] == NAME]
    if len(matching) > 1:
        raise RuntimeError("Agent sandbox identity is ambiguous")
    if not matching:
        launch()
    elif matching[0]["phase"] not in ("Ready", "Stopped"):
        raise RuntimeError("Existing agent needs explicit repair; no fallback")
    else:
        workload = managed_containers(matching[0]["id"], include_stopped=True)["sandbox"]
        if workload["Image"] != receipt["image_id"]:
            raise RuntimeError(
                "Prepared agent image changed; run ./demo start --recreate-agent during maintenance"
            )
        if matching[0]["phase"] == "Stopped":
            shell("sandbox", "start", NAME, timeout=300)
    for attempt in range(60):
        try:
            status()
            break
        except RuntimeError:
            if attempt == 59:
                raise
            time.sleep(2)
    forward()


def web_bridge_address():
    # Docker chooses the bridge subnet per host; web reaches the agent at its gateway.
    return run(
        [
            "docker",
            "network",
            "inspect",
            "market-shock_edge",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
        ]
    ).strip()


def tracked_forwards():
    rows = json.loads(shell("forward", "list", "--output", "json"))
    return [row for row in rows if row.get("sandbox") == NAME and row.get("port") == AGENT_PORT]


def forward_alive():
    address = web_bridge_address()
    return any(row.get("alive") is True and row.get("bind_address") == address for row in tracked_forwards())


def forward():
    stop_forward()
    # The CLI tracks background forwards; discard output so the detached
    # tunnel does not hold this process's pipes open.
    shell("forward", "start", "--background", f"{web_bridge_address()}:{AGENT_PORT}", NAME, discard=True)
    for attempt in range(20):
        if forward_alive():
            print("Agent API forwarded to the web bridge")
            return
        if attempt == 19:
            raise RuntimeError("Agent API forward did not start")
        time.sleep(0.5)


def stop_forward():
    # Let the pinned CLI validate its own tracked process before signalling it.
    # Never kill a process merely because it occupies the expected port.
    if tracked_forwards():
        shell("forward", "stop", str(AGENT_PORT), NAME, discard=True)


def stop():
    ensure_gateway()
    stop_forward()
    matching = [row for row in list_sandboxes() if row["name"] == NAME]
    if len(matching) > 1:
        raise RuntimeError("Agent sandbox identity is ambiguous")
    if matching and matching[0]["phase"] != "Stopped":
        shell("sandbox", "stop", NAME, timeout=300)
    print("Agent sandbox stopped; persistent application state preserved")


def recreate():
    """Explicit image/config/data refresh; never remove host state or traces."""
    verify()
    wait_dependencies()
    ensure_gateway()
    matching = [row for row in list_sandboxes() if row["name"] == NAME]
    if len(matching) > 1:
        raise RuntimeError("Agent sandbox identity is ambiguous")
    stop_forward()
    if matching:
        shell("sandbox", "delete", NAME, timeout=300)
        print("Replaced agent sandbox; host investigation state and traces retained")
    start()


def acquire_action_lock(action):
    """Lock direct sandbox creation/start once at the supported CLI boundary."""
    from scripts.spark import retention

    if action not in RETENTION_LOCKED_ACTIONS:
        return None
    inherited = os.environ.get("SPARK_RETENTION_LOCK_FD")
    try:
        if inherited is not None:
            if not inherited.isdigit():
                raise retention.RetentionError("invalid inherited retention lock")
            retention.verify_retention_lock(int(inherited), retention.ROOT)
            return None
        return retention.acquire_retention_lock(retention.ROOT)
    except retention.RetentionError:
        raise RuntimeError("OpenShell start/recreation is blocked by the retention/start lock") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "launch",
            "probe",
            "forward",
            "status",
            "stop",
            "start",
            "verify",
            "prepare-receipt",
            "recreate",
        ],
    )
    args = parser.parse_args()
    lock_fd = acquire_action_lock(args.action)
    try:
        if args.action == "launch":
            launch()
        elif args.action == "probe":
            launch("market-agent-probe", probe=True)
        elif args.action == "forward":
            forward()
        elif args.action == "status":
            status()
        elif args.action == "start":
            start()
        elif args.action == "recreate":
            recreate()
        elif args.action == "verify":
            verify()
            print("Prepared OpenShell artifacts verified")
        elif args.action == "prepare-receipt":
            prepare_receipt()
        else:
            stop()
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # RuntimeError messages above are deliberately fixed, safe operator advice.
        # Never expose raw JSON, subprocess argv/stderr, or OS exception details.
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f"OpenShell operation failed: {reason}; no fallback was started") from None

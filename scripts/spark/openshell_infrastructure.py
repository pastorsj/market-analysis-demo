"""Prepare only the application's isolated, pinned OpenShell infrastructure."""

from pathlib import Path
import json
import os
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path("/srv/market-shock/openshell")
SERVICE = "market-shock-openshell.service"
FORWARD_SERVICE = "market-shock-openshell-forward.service"


def run(args, *, env=None, input=None):
    result = subprocess.run(args, env=env, input=input, capture_output=True, timeout=60)
    if result.returncode:
        stage = " ".join([Path(args[0]).name, *args[1:3]])
        raise RuntimeError(f"OpenShell infrastructure stage {stage} failed; private output withheld")
    return result.stdout


def create_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


def prepare_keys(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = [directory / name for name in ("signing.pem", "public.pem", "kid")]
    if any(path.exists() or path.is_symlink() for path in paths):
        if not all(path.is_file() and not path.is_symlink() for path in paths):
            raise RuntimeError("Incomplete gateway keys; recover explicitly without rotating identity")
        if paths[0].stat().st_mode & 0o077:
            raise RuntimeError("Gateway signing key permissions must be owner-only")
        # Public material may have been created under the operator's normal
        # umask. Tighten permissions without rewriting any identity bytes.
        for path in paths[1:]:
            path.chmod(0o600)
        return
    private = run(["openssl", "genpkey", "-algorithm", "Ed25519"])
    public = run(["openssl", "pkey", "-pubout"], input=private)
    for path, value in zip(paths, [private, public, str(uuid.uuid4()).encode()]):
        create_private(path, value)


def service_text():
    return f'''[Unit]
Description=Market Shock OpenShell gateway (pinned 0.0.116)
After=network.target

[Service]
Environment=XDG_CONFIG_HOME={RUNTIME}/config
Environment=XDG_DATA_HOME={RUNTIME}/data
Environment=XDG_STATE_HOME={RUNTIME}/state
ExecStart={RUNTIME}/0.0.116/openshell-gateway --config "{ROOT}/scripts/spark/openshell/gateway.toml"
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
'''


def forward_service_text():
    return f'''[Unit]
Description=Market Shock OpenShell agent API forward
After={SERVICE}
Requires={SERVICE}

[Service]
Environment=XDG_CONFIG_HOME={RUNTIME}/config
Environment=XDG_DATA_HOME={RUNTIME}/data
Environment=XDG_STATE_HOME={RUNTIME}/state
ExecStart=/usr/bin/python3 "{ROOT}/scripts/spark/openshell_runtime.py" serve-forward
Restart=on-failure
RestartSec=2
UMask=0077
'''


def write_unit(unit, desired):
    if unit.is_symlink():
        raise RuntimeError("Refusing symlinked application service unit")
    if unit.exists() and unit.read_text() == desired:
        return False
    temporary = unit.with_suffix(".preparing")
    create_private(temporary, desired.encode())
    temporary.replace(unit)
    return True


def register_gateway(cli, env):
    registrations = json.loads(run([cli, "gateway", "list", "-o", "json"], env=env))
    matches = [item for item in registrations if item.get("name") == "market-shock"]
    if matches:
        if (
            len(matches) != 1
            or matches[0].get("endpoint") != "http://127.0.0.1:17671"
            or matches[0].get("is_remote")
        ):
            raise RuntimeError(
                "Existing market-shock gateway registration does not match the approved local endpoint"
            )
        return
    run([cli, "gateway", "add", "http://127.0.0.1:17671", "--name", "market-shock", "--local"], env=env)


def prepare_network():
    name = "market-shock_openshell"
    existing = (
        run(["docker", "network", "ls", "--filter", f"name=^{name}$", "--format", "{{.Name}}"])
        .decode()
        .splitlines()
    )
    if existing and existing != [name]:
        raise RuntimeError("OpenShell network identity is ambiguous")
    if not existing:
        run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--subnet",
                "172.22.0.0/16",
                "--gateway",
                "172.22.0.1",
                "--label",
                "com.nvidia.market-shock.owner=openshell",
                name,
            ]
        )
    networks = json.loads(run(["docker", "network", "inspect", name]))
    if len(networks) != 1:
        raise RuntimeError("OpenShell network identity is ambiguous")
    network = networks[0]
    configs = network.get("IPAM", {}).get("Config", [])
    if (
        network.get("Name") != name
        or network.get("Driver") != "bridge"
        or network.get("Internal")
        or len(configs) != 1
        or configs[0].get("Subnet") != "172.22.0.0/16"
        or configs[0].get("Gateway") != "172.22.0.1"
    ):
        raise RuntimeError(
            "Existing OpenShell bridge does not match the pinned callback network; no network was replaced"
        )


def main():
    os.umask(0o077)
    prepare_network()
    prepare_keys(RUNTIME / "keys")
    env = os.environ.copy()
    for key, directory in [
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ]:
        path = RUNTIME / directory
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[key] = str(path)
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    # Install, but never enable/start, the forward before app dependencies exist.
    gateway_changed = write_unit(units / SERVICE, service_text())
    forward_changed = write_unit(units / FORWARD_SERVICE, forward_service_text())
    if gateway_changed or forward_changed:
        run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", SERVICE])
    # start is deliberately idempotent: existing gateways are not restarted.
    run(["systemctl", "--user", "start", SERVICE])
    cli = str(RUNTIME / "0.0.116/openshell")
    register_gateway(cli, env)
    for attempt in range(30):
        try:
            run([cli, "-g", "market-shock", "status"], env=env)
            print("Application OpenShell gateway prepared; no existing keys were replaced")
            return
        except RuntimeError:
            if attempt == 29:
                raise
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(
            str(error) if isinstance(error, RuntimeError) else "OpenShell infrastructure preparation failed"
        ) from None

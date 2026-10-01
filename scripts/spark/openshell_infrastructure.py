"""Prepare only the application's isolated, pinned OpenShell gateway.

The gateway runs as a systemd user unit modeled on the upstream package unit:
config preflight and idempotent local mTLS generation run before every start.
All OpenShell state (database, TLS and JWT material, CLI registration) stays
under /srv/market-shock/openshell, separate from any personal OpenShell install.
"""

from pathlib import Path
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.spark.openshell_runtime import (  # noqa: E402
    CLI,
    GATEWAY,
    GATEWAY_CONFIG,
    GATEWAY_NAME,
    GATEWAY_SERVICE,
    RUNTIME,
    VERSION,
)

ENDPOINT = "https://127.0.0.1:17671"
TLS_DIR = RUNTIME / "tls"
# Removed in the 0.1.2 migration: the CLI now tracks background forwards itself.
RETIRED_UNITS = ("market-shock-openshell-forward.service",)


def run(args, *, env=None):
    result = subprocess.run(
        [str(arg) for arg in args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        stage = " ".join([Path(str(args[0])).name, *map(str, args[1:3])])
        raise RuntimeError(f"OpenShell infrastructure stage {stage} failed; private output withheld")
    return result.stdout


def create_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


def service_text():
    return f'''[Unit]
Description=Market Shock OpenShell gateway (pinned {VERSION})
After=default.target

[Service]
Type=simple
Environment=OPENSHELL_LOCAL_TLS_DIR={TLS_DIR}
Environment=XDG_CONFIG_HOME={RUNTIME}/config
Environment=XDG_DATA_HOME={RUNTIME}/data
Environment=XDG_STATE_HOME={RUNTIME}/state
ExecStartPre={GATEWAY} config preflight --path "{GATEWAY_CONFIG}"
ExecStartPre={GATEWAY} generate-certs --output-dir {TLS_DIR} --server-san host.openshell.internal
ExecStart={GATEWAY} --config "{GATEWAY_CONFIG}"
Restart=on-failure
RestartSec=5s
PrivateTmp=true
UMask=0077

[Install]
WantedBy=default.target
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


def retire_units(units):
    changed = False
    for name in RETIRED_UNITS:
        unit = units / name
        if unit.is_symlink() or unit.exists():
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", name],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
            )
            unit.unlink()
            changed = True
    return changed


def register_gateway(env):
    registrations = json.loads(run([CLI, "gateway", "list", "-o", "json"], env=env))
    matches = [item for item in registrations if item.get("name") == GATEWAY_NAME]
    if matches:
        if (
            len(matches) != 1
            or matches[0].get("endpoint") != ENDPOINT
            or matches[0].get("is_remote")
            or matches[0].get("auth") != "mtls"
        ):
            raise RuntimeError(
                "Existing market-shock gateway registration does not match the approved local mTLS endpoint"
            )
        return
    # --local copies the client bundle from OPENSHELL_LOCAL_TLS_DIR.
    run([CLI, "gateway", "add", ENDPOINT, "--name", GATEWAY_NAME, "--local"], env=env)


def main():
    os.umask(0o077)
    env = os.environ.copy()
    env["OPENSHELL_LOCAL_TLS_DIR"] = str(TLS_DIR)
    env["NO_COLOR"] = "1"
    for key, directory in [
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ]:
        path = RUNTIME / directory
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[key] = str(path)
    TLS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for binary in (CLI, GATEWAY):
        if run([binary, "--version"]).decode().strip() != f"{binary.name} {VERSION}":
            raise RuntimeError("OpenShell version mismatch; install the pinned release first")
    run([GATEWAY, "config", "preflight", "--path", GATEWAY_CONFIG], env=env)
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    changed = retire_units(units)
    changed = write_unit(units / GATEWAY_SERVICE, service_text()) or changed
    if changed:
        run(["systemctl", "--user", "daemon-reload"])
        # Apply a changed unit; an unchanged running gateway is left alone.
        run(["systemctl", "--user", "try-restart", GATEWAY_SERVICE])
    run(["systemctl", "--user", "enable", GATEWAY_SERVICE])
    run(["systemctl", "--user", "start", GATEWAY_SERVICE])
    for attempt in range(30):
        try:
            register_gateway(env)
            run([CLI, "-g", GATEWAY_NAME, "status"], env=env)
            print("Application OpenShell gateway prepared; no existing TLS identity was replaced")
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

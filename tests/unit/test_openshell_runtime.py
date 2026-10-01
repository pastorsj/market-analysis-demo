import copy
import json
import subprocess

import pytest
import yaml

from scripts.spark import openshell_infrastructure, openshell_receipt, openshell_runtime as runtime

AGENT_IMAGE = "sha256:" + "a" * 64
SUPERVISOR_IMAGE = "sha256:" + "b" * 64
SANDBOX_IMAGE = "sha256:" + "c" * 64
RECEIPT = {
    "image_id": AGENT_IMAGE,
    "runtime_images": {"supervisor": SUPERVISOR_IMAGE, "sandbox": SANDBOX_IMAGE},
}


def base_policy():
    return yaml.safe_load(runtime.BASE_POLICY.read_text())


def observed_sandbox(policy=None):
    """Shape of `openshell sandbox get -o json` from the 0.1.2 gateway."""
    return {
        "phase": "Ready",
        "policy_source": "sandbox",
        "current_policy_version": 2,
        "configuration_admission": {"state": "accepted", "error": "", "policy_version": 2},
        "conditions": [
            {"type": "Ready", "status": "True"},
            {"type": "ConfigurationReady", "status": "True"},
        ],
        "policy": runtime.expected_policy(base_policy()) if policy is None else policy,
    }


def test_policy_accepts_the_gateway_defaults_for_the_base_policy():
    runtime.validate_policy(base_policy(), observed_sandbox())


def test_policy_mcp_endpoint_pins_the_tools_and_inspected_revision():
    tools = runtime.expected_policy(base_policy())["network_policies"]["tools"]["endpoints"][0]
    assert tools["host"] == "host.openshell.internal" and tools["protocol"] == "mcp"
    assert tools["mcp"] == {"versions": ["2025-11-25"]}
    assert tools["enforcement"] == "enforce"


def test_policy_rejects_an_extra_grant():
    policy = runtime.expected_policy(base_policy())
    policy["network_policies"]["exfil"] = {
        "endpoints": [{"host": "example.com", "port": 443}],
        "binaries": [{"path": "/usr/local/bin/python3.12"}],
    }
    with pytest.raises(RuntimeError, match="differs"):
        runtime.validate_policy(base_policy(), observed_sandbox(policy))


def test_policy_rejects_a_widened_filesystem_grant():
    policy = runtime.expected_policy(base_policy())
    policy["filesystem_policy"]["read_write"].append("/srv/market-shock/scenario")
    with pytest.raises(RuntimeError, match="differs"):
        runtime.validate_policy(base_policy(), observed_sandbox(policy))


@pytest.mark.parametrize(
    "update",
    [
        {"configuration_admission": {"state": "rejected", "error": "bad", "policy_version": 2}},
        {"current_policy_version": 3},
        {"policy_source": "global"},
        {"conditions": [{"type": "ConfigurationReady", "status": "False"}]},
    ],
)
def test_policy_requires_gateway_admission(update):
    with pytest.raises(RuntimeError, match="not effective"):
        runtime.validate_policy(base_policy(), {**observed_sandbox(), **update})


def workload():
    """Shape of `docker inspect` for the driver's workload container."""
    binds = [
        {
            "Type": "bind",
            "Source": f"/srv/market-shock/{name}",
            "Destination": f"/srv/market-shock/{name}",
            "RW": name in runtime.WRITABLE_MOUNTS,
        }
        for name in runtime.DATA_MOUNTS
    ]
    return {
        "Id": "workload",
        "Image": AGENT_IMAGE,
        "Config": {"Entrypoint": runtime.WORKLOAD_ENTRYPOINT, "User": "1000:1000"},
        "HostConfig": {
            "NetworkMode": "none",
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": runtime.PIDS_LIMIT,
            "NanoCpus": 4_000_000_000,
            "Memory": runtime.MEMORY_BYTES,
        },
        "Mounts": binds
        + [
            {
                "Type": "volume",
                "Source": "/var/lib/docker/volumes/x",
                "Destination": "/.openshell/channel",
                "RW": True,
            }
        ],
    }


def supervisor():
    return {
        "Image": SUPERVISOR_IMAGE,
        "Config": {
            "Env": [
                "OPENSHELL_MAIN_PROCESS_SPEC="
                + json.dumps({"version": 1, "command": runtime.AGENT_COMMAND, "tty": False})
            ]
        },
        "HostConfig": {
            "NetworkMode": "host",
            "Privileged": False,
            "CapDrop": ["ALL"],
            "ExtraHosts": ["host.openshell.internal:127.0.0.1", "host.docker.internal:127.0.0.1"],
        },
    }


def validate(workload_doc, supervisor_doc):
    runtime.validate_launch(
        workload_doc, supervisor_doc, RECEIPT, runtime.supervisor_main_process_spec(supervisor_doc)
    )


def test_launch_accepts_the_prepared_two_container_layout():
    validate(workload(), supervisor())


def edit(document, path, value):
    document = copy.deepcopy(document)
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return document


@pytest.mark.parametrize(
    "path,value",
    [
        (("Image",), "sha256:" + "d" * 64),
        (("Config", "User"), "0:0"),
        (("Config", "Entrypoint"), ["/bin/sh"]),
        (("HostConfig", "NetworkMode"), "bridge"),
        (("HostConfig", "Privileged"), True),
        (("HostConfig", "CapAdd"), ["NET_ADMIN"]),
        (("HostConfig", "SecurityOpt"), []),
        (("HostConfig", "PidsLimit"), 0),
        (("HostConfig", "Memory"), 0),
        (("Mounts", 0, "RW"), True),
        (("Mounts", 2, "Source"), "/home/operator"),
    ],
)
def test_launch_rejects_workload_drift(path, value):
    with pytest.raises(RuntimeError, match="differs from preparation"):
        validate(edit(workload(), path, value), supervisor())


def test_launch_rejects_an_extra_host_mount():
    document = workload()
    document["Mounts"].append({"Type": "volume", "Source": "x", "Destination": "/sandbox/extra", "RW": True})
    with pytest.raises(RuntimeError, match="differs from preparation"):
        validate(document, supervisor())


@pytest.mark.parametrize(
    "path,value",
    [
        (("Image",), "sha256:" + "d" * 64),
        (("HostConfig", "NetworkMode"), "bridge"),
        (("HostConfig", "ExtraHosts"), ["host.openshell.internal:10.0.0.1"]),
        (
            ("Config", "Env"),
            ["OPENSHELL_MAIN_PROCESS_SPEC=" + json.dumps({"command": ["/bin/sh"], "tty": False})],
        ),
    ],
)
def test_launch_rejects_supervisor_drift(path, value):
    with pytest.raises(RuntimeError, match="differs from preparation"):
        validate(workload(), edit(supervisor(), path, value))


def test_providers_must_match_preparation_exactly():
    output = json.dumps({"providers": [{"name": "market-inference-1"}], "next_page_token": ""})
    runtime.validate_providers(["market-inference-1"], output)
    for prepared in ([], ["market-inference-1", "market-langsmith-2"]):
        with pytest.raises(RuntimeError, match="provider attachments"):
            runtime.validate_providers(prepared, output)


def test_operator_commands_never_inherit_stdin(monkeypatch):
    seen = {}

    def fake(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake)
    assert runtime.run(["openshell"]) == "ok"
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["env"]["XDG_CONFIG_HOME"] == str(runtime.RUNTIME / "config")


def receipt_files(tmp_path):
    files = {}
    for role in openshell_receipt.FILE_ROLES:
        path = tmp_path / role
        path.write_text(role)
        files[role] = path
    return files


def test_receipt_binds_the_runtime_images(tmp_path):
    files = receipt_files(tmp_path)
    pins = dict(image_id=AGENT_IMAGE, source_build_input="e" * 64, runtime_images=RECEIPT["runtime_images"])
    receipt = openshell_receipt.create_receipt(files, **pins)
    assert receipt["version"] == runtime.VERSION
    openshell_receipt.validate_receipt(receipt, **pins)
    with pytest.raises(openshell_receipt.ReceiptError):
        openshell_receipt.validate_receipt(
            receipt, **{**pins, "runtime_images": {"supervisor": SANDBOX_IMAGE, "sandbox": SANDBOX_IMAGE}}
        )
    files["policy"].write_text("widened")
    with pytest.raises(openshell_receipt.ReceiptError):
        openshell_receipt.validate_receipt(receipt, **pins)


def test_gateway_config_pins_the_runtime_images_and_loopback_listener():
    import tomllib

    config = tomllib.loads(runtime.GATEWAY_CONFIG.read_text())
    gateway, docker = config["openshell"]["gateway"], config["openshell"]["drivers"]["docker"]
    assert config["openshell"]["version"] == 2
    assert gateway["bind_address"] == "127.0.0.1:17671"
    assert "disable_tls" not in gateway
    assert docker["supervisor_image"] == runtime.RUNTIME_IMAGES["supervisor"]
    assert docker["sandbox_runtime_image"] == runtime.RUNTIME_IMAGES["sandbox"]
    assert docker["image_pull_policy"] == "never"


def test_gateway_unit_follows_the_packaged_preflight_and_local_tls_steps():
    unit = openshell_infrastructure.service_text()
    assert f"ExecStartPre={runtime.GATEWAY} config preflight" in unit
    assert (
        f"ExecStartPre={runtime.GATEWAY} generate-certs --output-dir {openshell_infrastructure.TLS_DIR}"
        in unit
    )
    assert f"Environment=OPENSHELL_LOCAL_TLS_DIR={openshell_infrastructure.TLS_DIR}" in unit
    assert "UMask=0077" in unit


@pytest.mark.parametrize(
    "sandboxes,stopped", [([], False), ([{"name": "market-agent", "phase": "Ready"}], True)]
)
def test_stop_tolerates_a_missing_sandbox(monkeypatch, sandboxes, stopped):
    calls = []
    monkeypatch.setattr(runtime, "ensure_gateway", lambda: None)
    monkeypatch.setattr(runtime, "stop_forward", lambda: calls.append("forward"))
    monkeypatch.setattr(runtime, "list_sandboxes", lambda: sandboxes)
    monkeypatch.setattr(runtime, "shell", lambda *args, **kwargs: calls.append(args))
    runtime.stop()
    assert calls[0] == "forward"
    assert (("sandbox", "stop", "market-agent") in calls) is stopped


def prepared_profile(tmp_path, monkeypatch, *, tamper=False):
    import hashlib

    monkeypatch.setattr(runtime, "PREPARED", tmp_path)
    profile = {
        "display_name": "market-inference",
        "endpoints": [{"host": "inference.example", "port": 443, "path": "/v1/**", "protocol": "rest"}],
        "binaries": ["/usr/local/bin/python3.12"],
    }
    name = "market-inference-" + hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:12]
    if tamper:
        profile["endpoints"][0]["host"] = "exfil.example"
    (tmp_path / f"{name}.yaml").write_text(json.dumps({**profile, "id": name}))
    return name


def test_policy_requires_exactly_the_prepared_provider_layers(tmp_path, monkeypatch):
    name = prepared_profile(tmp_path, monkeypatch)
    policy = runtime.expected_policy(base_policy(), [name])
    layer = policy["network_policies"]["_provider_" + name.replace("-", "_")]
    assert layer["endpoints"][0]["host"] == "inference.example"
    runtime.validate_policy(base_policy(), observed_sandbox(policy), [name])
    with pytest.raises(RuntimeError, match="differs"):
        runtime.validate_policy(base_policy(), observed_sandbox(policy), [])


def test_policy_rejects_a_profile_that_no_longer_matches_its_identity(tmp_path, monkeypatch):
    name = prepared_profile(tmp_path, monkeypatch, tamper=True)
    with pytest.raises(RuntimeError, match="identity"):
        runtime.expected_policy(base_policy(), [name])

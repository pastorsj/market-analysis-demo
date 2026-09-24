import json
import subprocess

import pytest

from scripts.spark import agent_probe, openshell_runtime, process_contract
from scripts.spark.process_contract import ContractError

SCENARIO = "market-shock-v2-0123456789abcdef"


def status(**updates):
    return {
        "ready": True,
        "reason": None,
        "remote_routing_enabled": True,
        "dependencies": {"tools": True, "model": True, "events": True},
        "coverage": {"scenario_id": SCENARIO, "first_session": "2022-01-03", "last_session": "2026-09-18"},
        **updates,
    }


def test_ready_status():
    assert agent_probe.classify(status(), SCENARIO) == (agent_probe.READY, "ready")


def test_remote_off_is_prepared_but_not_ready_with_guidance():
    code, message = agent_probe.classify(
        status(ready=False, reason="needs remote", remote_routing_enabled=False), SCENARIO
    )
    assert code == agent_probe.REMOTE_OFF
    assert "REMOTE_ROUTING_ENABLED=true" in message


@pytest.mark.parametrize(
    "document",
    [
        status(dependencies={"tools": True, "model": False, "events": True}, ready=False),
        status(coverage={"scenario_id": "market-shock-v2-other"}),
        status(dependencies=None),
        status(coverage="broken"),
        ["not", "an", "object"],
    ],
)
def test_not_ready_status(document):
    code, _ = agent_probe.classify(document, SCENARIO)
    assert code == agent_probe.NOT_READY


def test_dependency_outage_wins_over_remote_off():
    document = status(
        ready=False,
        remote_routing_enabled=False,
        dependencies={"tools": False, "model": True, "events": True},
    )
    assert agent_probe.classify(document, SCENARIO) == (agent_probe.NOT_READY, "unavailable: tools")


def compose(**services):
    base = {
        "web": {"ports": [{"host_ip": "127.0.0.1", "published": "3000", "target": 3000}]},
        "tools": {},
        "model": {"command": ["--model", "/models/x"]},
    }
    return json.dumps({"services": {**base, **services}}).encode()


def test_compose_contract_accepts_the_canonical_shape():
    process_contract.validate_compose_config(compose())
    process_contract.validate_static_ports(compose())


@pytest.mark.parametrize(
    "services",
    [
        {"agent": {}},
        {"tools": {"command": ["sh"]}},
        {"web": {"entrypoint": ["sh"]}},
        {"model": {}},
    ],
)
def test_compose_contract_rejects_overrides_and_extra_services(services):
    with pytest.raises(ContractError):
        process_contract.validate_compose_config(compose(**services))


@pytest.mark.parametrize(
    "services",
    [
        {"web": {"ports": [{"host_ip": "0.0.0.0", "published": "3000", "target": 3000}]}},
        {"tools": {"ports": [{"host_ip": "127.0.0.1", "published": "8000", "target": 8000}]}},
    ],
)
def test_only_loopback_web_is_published(services):
    with pytest.raises(ContractError):
        process_contract.validate_static_ports(compose(**services))


def test_running_ports():
    web = {
        "Service": "web",
        "Publishers": [{"URL": "127.0.0.1", "PublishedPort": 3000, "TargetPort": 3000, "Protocol": "tcp"}],
    }
    tools = {"Service": "tools", "Publishers": [{"URL": "", "PublishedPort": 0, "TargetPort": 8000}]}
    process_contract.validate_running_ports([json.dumps(web), json.dumps(tools)])
    with pytest.raises(ContractError):
        process_contract.validate_running_ports([json.dumps({"Service": "agent", "Publishers": []})])
    exposed = {**tools, "Publishers": [{"URL": "0.0.0.0", "PublishedPort": 8000, "TargetPort": 8000}]}
    with pytest.raises(ContractError):
        process_contract.validate_running_ports([json.dumps(exposed)])


def test_running_process_must_match_expected_command():
    process_contract.validate_running_process("model", '["vllm"]', '["vllm"]', '["--a"]', '["--a"]')
    with pytest.raises(ContractError):
        process_contract.validate_running_process("model", '["vllm"]', '["vllm"]', '["--a"]', '["--b"]')
    with pytest.raises(ContractError):
        process_contract.validate_running_process("web", '["nginx"]', '["sh"]', "null", "null")


def receipt_document():
    images = {}
    for index, (service, name) in enumerate(process_contract.IMAGE_NAMES.items()):
        images[service] = {"name": name, "id": "sha256:" + f"{index}" * 64}
        if service != "model":
            images[service]["build_input_sha256"] = f"{index}" * 64
    return {"schema_version": 1, "images": images}


def test_runtime_receipt(tmp_path):
    path = tmp_path / "runtime-images.json"
    path.write_text(json.dumps(receipt_document()))
    path.chmod(0o600)
    assert set(process_contract.runtime_receipt(path)) == {"web", "agent", "tools", "model"}
    path.chmod(0o644)
    with pytest.raises(ContractError):
        process_contract.runtime_receipt(path)
    document = receipt_document()
    document["images"]["agent"]["name"] = "other:latest"
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    with pytest.raises(ContractError):
        process_contract.runtime_receipt(path)


def test_openshell_timeout_never_discloses_command_details(monkeypatch):
    private_argument = "https://private-inference.invalid/secret"

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["openshell", private_argument], 20)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RuntimeError) as raised:
        openshell_runtime.run([openshell_runtime.CLI, private_argument], timeout=20)

    message = str(raised.value)
    assert private_argument not in message
    assert "no private output was displayed" in message

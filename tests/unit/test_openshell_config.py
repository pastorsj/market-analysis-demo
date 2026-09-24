import json
import subprocess

import pytest

from scripts.spark import openshell_config as config


def environment(**updates):
    return {
        "REMOTE_ROUTING_ENABLED": "true",
        "NVIDIA_BASE_URL": "https://inference.example/v1",
        "NVIDIA_INFERENCE_API_KEY": "secret-inference",
        "NEMO_RELAY_LANGSMITH_ENABLED": "true",
        "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com",
        "LANGSMITH_API_KEY": "secret-langsmith",
        "LANGSMITH_PROJECT_URL": "https://smith.langchain.com/o/11111111-1111-1111-1111-111111111111/projects/p/22222222-2222-2222-2222-222222222222",
        **updates,
    }


def test_endpoint_and_python_scoped_profiles():
    safe, policy, profiles = config.prepare_inputs(environment(), {"network_policies": {}})
    assert not config.SECRET_KEYS.intersection(safe)
    assert len(profiles) == 2
    profile, key, value = profiles[0]
    assert profile["endpoints"] == [
        {
            "host": "inference.example",
            "port": 443,
            "path": "/v1/**",
            "protocol": "rest",
            "access": "full",
            "enforcement": "enforce",
        }
    ]
    assert profile["binaries"] == [config.PYTHON]
    assert key == "NVIDIA_INFERENCE_API_KEY" and value == "secret-inference"
    assert "secret-inference" not in json.dumps([safe, policy, profile])
    assert safe["LANGSMITH_PROJECT_URL"].startswith("https://smith.langchain.com/o/")
    granted_hosts = {
        endpoint["host"] for item in policy["network_policies"].values() for endpoint in item["endpoints"]
    }
    assert "api.smith.langchain.com" in granted_hosts
    assert "smith.langchain.com" not in granted_hosts


def test_disabled_remote_and_telemetry_have_no_network_grants():
    safe, policy, profiles = config.prepare_inputs(
        environment(
            REMOTE_ROUTING_ENABLED="false", NEMO_RELAY_LANGSMITH_ENABLED="false", LANGSMITH_PROJECT_URL=""
        ),
        {"network_policies": {}},
    )
    assert profiles == [] and policy["network_policies"] == {}
    assert not config.SECRET_KEYS.intersection(safe)


@pytest.mark.parametrize(
    "updates",
    [
        {"NVIDIA_INFERENCE_API_KEY": ""},
        {"NVIDIA_BASE_URL": ""},
        {"REMOTE_ROUTING_ENABLED": "maybe"},
        {"NVIDIA_BASE_URL": "https://user:secret@example.com/v1"},
        {"LANGSMITH_ENDPOINT": "https://example.com?token=secret"},
        {"LANGSMITH_PROJECT_URL": "http://smith.langchain.com/o/a/projects/p/b"},
        {"LANGSMITH_PROJECT_URL": "https://evil.example/o/a/projects/p/b"},
        {"LANGSMITH_PROJECT_URL": "https://user:secret@smith.langchain.com/o/a/projects/p/b"},
        {"LANGSMITH_PROJECT_URL": "https://smith.langchain.com/o/a/projects/p/b?token=secret"},
        {"LANGSMITH_PROJECT_URL": "https://smith.langchain.com/o/a/projects/p/b#fragment"},
        {"LANGSMITH_PROJECT_URL": "https://smith.langchain.com/projects"},
        {"LANGSMITH_PROJECT_URL": ""},
    ],
)
def test_invalid_configuration_fails_closed(updates):
    with pytest.raises(config.PreparationError):
        config.prepare_inputs(environment(**updates), {"network_policies": {}})


def test_project_link_is_rejected_when_trace_export_is_disabled():
    with pytest.raises(config.PreparationError, match="project link"):
        config.prepare_inputs(environment(NEMO_RELAY_LANGSMITH_ENABLED="false"), {"network_policies": {}})


def test_credentials_only_in_child_environment(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "command", lambda args, env=None: calls.append((args, env)))
    config.openshell(
        "provider",
        "create",
        "--credential",
        "LANGSMITH_API_KEY",
        credential=("LANGSMITH_API_KEY", "secret-value"),
    )
    argv, env = calls[0]
    assert "secret-value" not in repr(argv)
    assert argv[:3] == [str(config.CLI), "-g", "market-shock"]
    assert env["LANGSMITH_API_KEY"] == "secret-value"
    assert env["XDG_CONFIG_HOME"] == str(config.RUNTIME / "config")


def test_command_failure_does_not_disclose_child_output(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "secret-stdout", "secret-stderr"),
    )
    with pytest.raises(config.PreparationError) as caught:
        config.command(["tool"])
    assert "secret" not in str(caught.value)
    assert capsys.readouterr() == ("", "")


def test_actionable_stage_error_omits_credential(monkeypatch):
    def fail(*args, **kwargs):
        raise config.PreparationError("secret-value")

    monkeypatch.setattr(config, "command", fail)
    with pytest.raises(config.PreparationError, match="OpenShell provider create failed") as caught:
        config.openshell(
            "provider",
            "create",
            "--credential",
            "LANGSMITH_API_KEY",
            credential=("LANGSMITH_API_KEY", "secret-value"),
        )
    assert "secret-value" not in str(caught.value)


def test_private_files_and_no_symlink_follow(tmp_path):
    path = tmp_path / "file.json"
    config.private_json(path, {"safe": True})
    assert path.stat().st_mode & 0o777 == 0o600
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        config.private_json(link, {})
    assert json.loads(path.read_text()) == {"safe": True}


ENV_FILE = """
# comment
REMOTE_ROUTING_ENABLED=true
NVIDIA_BASE_URL=https://inference.example/v1
NVIDIA_INFERENCE_API_KEY="secret-inference"
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=
LANGSMITH_API_KEY='secret-langsmith'
LANGSMITH_PROJECT=demo
LANGSMITH_PROJECT_URL=https://smith.langchain.com/o/11111111-1111-1111-1111-111111111111/projects/p/22222222-2222-2222-2222-222222222222
"""


def test_agent_environment_maps_the_env_file(tmp_path):
    path = tmp_path / "private.env"
    path.write_text(ENV_FILE)
    env = config.agent_environment(config.read_env_file(path))
    assert env["NVIDIA_INFERENCE_API_KEY"] == "secret-inference"
    assert env["LANGSMITH_API_KEY"] == "secret-langsmith"
    assert env["NEMO_RELAY_LANGSMITH_ENABLED"] == "true"
    assert env["LANGSMITH_ENDPOINT"] == "https://api.smith.langchain.com"  # empty -> default
    assert env["MARKET_SHOCK_SCENARIO_ROOT"] == "/srv/market-shock/scenario"
    assert set(env) == config.SAFE_KEYS | config.SECRET_KEYS


def test_defaults_keep_remote_routing_off():
    env = config.agent_environment({})
    assert env["REMOTE_ROUTING_ENABLED"] == "false"
    assert env["NEMO_RELAY_LANGSMITH_ENABLED"] == "false"


def test_env_file_rejects_non_assignments(tmp_path):
    path = tmp_path / "broken.env"
    path.write_text("REMOTE_ROUTING_ENABLED true\n")
    with pytest.raises(config.PreparationError):
        config.read_env_file(path)


def test_failed_preparation_invalidates_old_readiness(tmp_path, monkeypatch):
    (tmp_path / "providers.json").write_text("[]")
    monkeypatch.setenv("COMPOSE_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(OSError):
        config.prepare(tmp_path, tmp_path / "unused")
    assert not (tmp_path / "providers.json").exists()


def test_full_preparation_writes_no_secrets(tmp_path, monkeypatch):
    base = tmp_path / "base.yaml"
    base.write_text("network_policies: {}")
    env_file = tmp_path / "private.env"
    env_file.write_text(ENV_FILE)
    monkeypatch.setenv("COMPOSE_ENV_FILE", str(env_file))
    output = tmp_path / "prepared"
    calls = []

    def fake(args, env=None):
        calls.append((args, env))
        return ""

    monkeypatch.setattr(config, "command", fake)
    config.prepare(output, base)
    for path in output.iterdir():
        assert "secret-inference" not in path.read_text()
        assert "secret-langsmith" not in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
    assert len(json.loads((output / "providers.json").read_text())) == 2
    for argv, _env in calls:
        assert "secret-inference" not in repr(argv)
        assert "secret-langsmith" not in repr(argv)
    settings = next(argv for argv, _ in calls if "settings" in argv)
    assert "--yes" in settings
    launch_env = json.loads((output / "env.json").read_text())
    assert launch_env["REMOTE_ROUTING_ENABLED"] == "true"
    assert not config.SECRET_KEYS.intersection(launch_env)

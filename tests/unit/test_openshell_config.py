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
    safe, profiles = config.prepare_inputs(environment())
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
    assert profile["credentials"] == [
        {
            "name": "api_key",
            "env_vars": ["NVIDIA_INFERENCE_API_KEY"],
            "required": True,
            "auth_style": "bearer",
            "header_name": "authorization",
        }
    ]
    assert profile["id"].startswith("market-inference-")
    assert key == "NVIDIA_INFERENCE_API_KEY" and value == "secret-inference"
    assert "secret-inference" not in json.dumps([safe, profile])
    assert safe["LANGSMITH_PROJECT_URL"].startswith("https://smith.langchain.com/o/")
    # Attached profiles are the only remote grants; the browser-only project link is never one.
    granted_hosts = {endpoint["host"] for item, _, _ in profiles for endpoint in item["endpoints"]}
    assert granted_hosts == {"inference.example", "api.smith.langchain.com"}


def test_disabled_remote_and_telemetry_have_no_network_grants():
    safe, profiles = config.prepare_inputs(
        environment(
            REMOTE_ROUTING_ENABLED="false", NEMO_RELAY_LANGSMITH_ENABLED="false", LANGSMITH_PROJECT_URL=""
        )
    )
    assert profiles == []
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
        config.prepare_inputs(environment(**updates))


def test_project_link_is_rejected_when_trace_export_is_disabled():
    with pytest.raises(config.PreparationError, match="project link"):
        config.prepare_inputs(environment(NEMO_RELAY_LANGSMITH_ENABLED="false"))


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
        config.prepare(tmp_path)
    assert not (tmp_path / "providers.json").exists()


def fake_gateway(calls, *, profiles=(), providers=()):
    def fake(args, env=None):
        calls.append((args, env))
        if args[3:5] == ["profile", "list"]:
            return json.dumps([{"id": item} for item in profiles])
        if args[3:5] == ["provider", "list"]:
            return json.dumps({"providers": [{"name": item} for item in providers], "next_page_token": ""})
        return ""

    return fake


def test_full_preparation_writes_no_secrets(tmp_path, monkeypatch):
    env_file = tmp_path / "private.env"
    env_file.write_text(ENV_FILE)
    monkeypatch.setenv("COMPOSE_ENV_FILE", str(env_file))
    output = tmp_path / "prepared"
    calls = []
    monkeypatch.setattr(config, "command", fake_gateway(calls))
    config.prepare(output)
    for path in output.iterdir():
        assert "secret-inference" not in path.read_text()
        assert "secret-langsmith" not in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
    assert len(json.loads((output / "providers.json").read_text())) == 2
    for argv, _env in calls:
        assert "secret-inference" not in repr(argv)
        assert "secret-langsmith" not in repr(argv)
    stages = [argv[3:5] for argv, _ in calls]
    assert stages.count(["profile", "import"]) == 2 and stages.count(["provider", "create"]) == 2
    assert not (output / "policy.yaml").exists()
    launch_env = json.loads((output / "env.json").read_text())
    assert launch_env["REMOTE_ROUTING_ENABLED"] == "true"
    assert not config.SECRET_KEYS.intersection(launch_env)


def test_rerun_reuses_imported_profiles_and_rotates_credentials(tmp_path, monkeypatch):
    env_file = tmp_path / "private.env"
    env_file.write_text(ENV_FILE)
    monkeypatch.setenv("COMPOSE_ENV_FILE", str(env_file))
    _, profiles = config.prepare_inputs(config.agent_environment(config.read_env_file(env_file)))
    ids = [profile["id"] for profile, _, _ in profiles]
    calls = []
    monkeypatch.setattr(config, "command", fake_gateway(calls, profiles=ids, providers=ids))
    config.prepare(tmp_path / "prepared")
    stages = [argv[3:5] for argv, _ in calls]
    assert ["profile", "import"] not in stages and ["provider", "create"] not in stages
    assert stages.count(["provider", "update"]) == 2

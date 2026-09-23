from pathlib import Path
import subprocess

import pytest

from scripts.spark import openshell_runtime


ROOT = Path(__file__).resolve().parents[2]


def test_remote_admission_precedes_web_and_ready():
    library = (ROOT / "scripts/spark/lib.sh").read_text(encoding="utf-8")
    start = (ROOT / "scripts/spark/start.sh").read_text(encoding="utf-8")
    doctor = (ROOT / "scripts/spark/doctor.sh").read_text(encoding="utf-8")
    probe = library.split("spark_openshell_remote_provider_probe()", 1)[1].split(
        "spark_openshell_maintenance_acquire()", 1,
    )[0]

    admission = start.index("spark_openshell_remote_provider_probe")
    assert start.index("spark_openshell start") < admission
    assert start.index("spark_openshell recreate") < admission
    assert admission < start.index('"${SPARK_START_TIMEOUT_SECONDS:-600}" web')
    assert admission < start.index("READY FOR DEMO")
    assert 'base + "/models"' in probe
    assert "timeout=15" in probe and "timeout=20" in probe
    assert "response.read(" not in probe
    assert "no endpoint, credential, response, or transport detail was displayed" in probe
    assert 'check REMOTE_PROVIDER "approved inference endpoint reachable"' in doctor


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

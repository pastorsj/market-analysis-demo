"""The tools service reports exactly which verified scenario snapshots it loaded."""

from market_tools.scenario import Scenario


def test_identity_reports_every_checksum_bound_snapshot():
    scenario = object.__new__(Scenario)
    scenario.scenario_id = "market-shock-v2-0123456789abcdef"
    scenario.manifest_sha256 = "0" * 64
    scenario.manifest = {
        "market": {"manifest_sha256": "1" * 64},
        "documents": {"manifest_sha256": "2" * 64},
        "readiness": {"market": {"sha256": "3" * 64}, "documents": {"sha256": "4" * 64}},
    }
    assert scenario.identity() == {
        "scenario_id": "market-shock-v2-0123456789abcdef",
        "scenario_manifest_sha256": "0" * 64,
        "market_manifest_sha256": "1" * 64,
        "document_manifest_sha256": "2" * 64,
        "market_readiness_sha256": "3" * 64,
        "document_readiness_sha256": "4" * 64,
    }

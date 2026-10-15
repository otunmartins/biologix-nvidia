"""The aggregate availability report.

This report is what the API, the MCP server and the UI display, so it is the
single place a false claim of capability would do the most damage. The tests
check the invariants rather than the exact wording: every adapter reports a
verdict, an unavailable one always says why, and nothing is ever described as
verified when it has not run.
"""

from __future__ import annotations

import httpx
import respx

from biologix.nvidia import _PROBES, availability_report

EXPECTED_ADAPTERS = {
    "bionemo-esm2",
    "bionemo-genmol",
    "bionemo-molmim",
    "bionemo-boltz2",
    "bionemo-diffdock",
    "nvmolkit",
}


def test_every_adapter_is_reported(hosted_settings):
    report = availability_report(hosted_settings)
    assert {a["adapter"] for a in report["adapters"]} == EXPECTED_ADAPTERS
    assert report["summary"]["n_total"] == len(EXPECTED_ADAPTERS)


def test_probe_table_matches_the_adapters_it_names(hosted_settings):
    """A typo in the probe table would otherwise show up as a silently
    missing adapter rather than an error."""
    report = availability_report(hosted_settings)
    assert {name for name, _, _ in _PROBES} == {a["adapter"] for a in report["adapters"]}


def test_unavailable_adapters_always_give_a_reason(hosted_settings):
    for adapter in availability_report(hosted_settings)["adapters"]:
        if not adapter.get("available"):
            assert adapter.get("reason"), f"{adapter['adapter']} is unavailable with no reason"


def test_no_adapter_claims_to_be_verified_here(hosted_settings):
    report = availability_report(hosted_settings)
    for adapter in report["adapters"]:
        if "code_path_executed_in_this_build" in adapter:
            assert adapter["code_path_executed_in_this_build"] is False
    assert "bionemo-boltz2" in report["summary"]["unverified_adapters"]
    assert "nvmolkit" in report["summary"]["unverified_adapters"]


def test_degraded_mode_is_flagged_without_credentials(hosted_settings):
    summary = availability_report(hosted_settings)["summary"]
    assert summary["degraded_mode"] is True
    assert summary["n_available"] == 0
    assert "never simulated" in summary["note"]


def test_simulation_backend_is_reported_separately(hosted_settings):
    report = availability_report(hosted_settings)
    assert "simulation" in report
    # OpenMM is a hard dependency of the simulation extra and is installed in
    # the dev environment, so this must resolve to a real backend report.
    assert report["simulation"]["backend"] == "openmm"
    assert "cuda_available" in report["simulation"]


def test_a_broken_probe_does_not_hide_the_others(hosted_settings, monkeypatch):
    import biologix.nvidia.boltz2 as boltz2_module

    def explode(settings=None):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(boltz2_module, "boltz2_availability", explode)
    report = availability_report(hosted_settings)

    broken = next(a for a in report["adapters"] if a["adapter"] == "bionemo-boltz2")
    assert broken["available"] is False
    assert "probe failed" in broken["reason"]
    # Every other adapter still reported.
    assert len(report["adapters"]) == len(EXPECTED_ADAPTERS)


@respx.mock
def test_a_fully_local_stack_reports_available(local_settings):
    """With every NIM reachable locally, only nvmolkit stays unavailable
    (it needs a GPU, which the dev machine does not have)."""
    for port in (8000, 8001, 8002, 8003, 8010):
        respx.get(f"http://127.0.0.1:{port}/v1/health/ready").mock(
            return_value=httpx.Response(200)
        )
    report = availability_report(local_settings)
    by_name = {a["adapter"]: a for a in report["adapters"]}

    for name in ("bionemo-genmol", "bionemo-molmim", "bionemo-boltz2", "bionemo-diffdock"):
        assert by_name[name]["available"] is True, by_name[name].get("reason")
    assert by_name["nvmolkit"]["available"] is False
    assert report["summary"]["degraded_mode"] is True

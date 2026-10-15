"""The MCP surface.

An agent consuming this server cannot see the source, so the contract it
depends on is entirely in the payloads: every result states its evidence tier,
every refusal is readable data rather than a traceback, and no tool ever
returns a number in place of an unavailable one. These tests pin that.
"""

from __future__ import annotations

import asyncio

import pytest

from biologix import EVIDENCE_TIERS
from biologix.errors import AdapterUnavailable
from biologix.mcp_server import (
    _refusal,
    dock_candidate_to_target,
    embed_target,
    evidence_tiers,
    generate_candidates,
    list_targets,
    mcp,
    nvidia_status,
    predict_complex_affinity,
    screen_candidates,
)

EXPECTED_TOOLS = {
    "nvidia_status",
    "list_targets",
    "generate_candidates",
    "screen_candidates",
    "embed_target",
    "dock_candidate_to_target",
    "predict_complex_affinity",
}


def test_every_tool_is_registered_and_described():
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"


def test_server_instructions_state_the_evidence_discipline():
    assert "research prototype" in mcp.instructions
    for tier in EVIDENCE_TIERS:
        assert tier in mcp.instructions


def test_evidence_tiers_resource_disclaims_measured_data():
    text = evidence_tiers()
    for tier in EVIDENCE_TIERS:
        assert tier in text
    assert "no assay integration" in text


# -- refusals --------------------------------------------------------------


def test_refusal_carries_the_remedy_and_forbids_substitution():
    payload = _refusal(
        AdapterUnavailable("bionemo-boltz2", "no key", remedy="set NVIDIA_API_KEY")
    )
    assert payload["error"] == "AdapterUnavailable"
    assert payload["adapter"] == "bionemo-boltz2"
    assert payload["remedy"] == "set NVIDIA_API_KEY"
    assert "Do not substitute" in payload["note"]


def test_unavailable_adapter_returns_a_refusal_not_an_exception(monkeypatch):
    """A missing NIM must be legible to the agent, not a crash."""
    # Blank, not deleted. Deleting it defeated the autouse credential guard:
    # pydantic then fell through to the developer's own .env, and on a machine
    # with a real key this test made a live Boltz-2 call and asserted against
    # whatever the hosted GPU happened to do that day.
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    from biologix.settings import reset_settings_cache

    reset_settings_cache()
    payload = predict_complex_affinity("human-insulin", "[*]CCO[*]")
    reset_settings_cache()

    assert payload["error"] in ("AdapterUnavailable", "AdapterCallFailed")
    assert payload.get("remedy") or payload.get("reason")
    # Crucially: no affinity number came back.
    assert "affinity" not in payload


def test_embed_target_refuses_without_credentials():
    """Regression: this tool imported a module-level embed_biologic that does
    not exist, so it raised ImportError instead of returning a refusal. The
    refusal path is the only thing an agent can act on."""
    payload = embed_target("human-insulin")
    assert payload["error"] == "AdapterUnavailable"
    assert payload["adapter"] == "bionemo-esm2"
    assert payload["remedy"]
    assert "chains" not in payload


def test_embed_target_unknown_biologic_is_refused():
    assert embed_target("not-a-drug")["error"] == "KeyError"


def test_unknown_generator_is_refused():
    payload = generate_candidates(generator="magic")
    assert payload["error"] == "ValueError"
    assert "baseline" in payload["message"]


def test_unknown_target_is_refused():
    payload = dock_candidate_to_target("not-a-drug", "[*]CCO[*]")
    assert payload["error"] == "KeyError"


def test_invalid_repeat_unit_is_refused_before_any_adapter_call():
    """Benzene has no attachment points, so it must fail the repeat-unit gate
    rather than reaching DiffDock."""
    payload = dock_candidate_to_target("human-insulin", "c1ccccc1")
    assert payload["error"] == "ValidationFailure"
    assert payload["reason"]


def test_empty_screen_input_is_refused():
    assert screen_candidates([])["error"] == "ValueError"


def test_unknown_constraint_set_is_refused():
    assert screen_candidates(["[*]CCO[*]"], constraint_set="nonsense")["error"] == "ValueError"


# -- results ---------------------------------------------------------------


def test_status_lists_adapters_and_never_claims_verification():
    report = nvidia_status()
    assert report["biologix_version"]
    assert report["evidence_tiers"] == list(EVIDENCE_TIERS)
    assert len(report["adapters"]) == 6
    assert "unverified_adapters" in report["summary"]


def test_targets_are_listed_with_the_no_concatenation_note():
    report = list_targets()
    assert report["targets"]
    assert "never concatenated" in report["note"]


def test_baseline_generation_needs_no_nvidia_component():
    result = generate_candidates(n=5, generator="baseline")
    assert result["evidence"] == "hypothesis"
    assert len(result["candidates"]) == 5
    assert result["statistics"]
    for candidate in result["candidates"]:
        assert candidate["candidate_id"]
        assert all("*" in unit for unit in candidate["repeat_units"])
    assert "not predictions" in result["note"]


def test_baseline_generation_is_reproducible():
    first = generate_candidates(n=6, generator="baseline", seed=1234)
    second = generate_candidates(n=6, generator="baseline", seed=1234)
    assert [c["candidate_id"] for c in first["candidates"]] == [
        c["candidate_id"] for c in second["candidates"]
    ]


def test_screening_reports_the_oligomer_it_actually_measured():
    report = screen_candidates(["[*]CCO[*]", "[*]CC([*])N1CCCC1=O"])
    assert report["evidence"] == "computed"
    assert len(report["results"]) == 2
    for row in report["results"]:
        assert row["oligomer_smiles"]
        assert row["screening_dp"] >= 1
        assert isinstance(row["passed"], bool)
    assert report["constraints"]
    assert "not a polydisperse polymer" in report["note"]


def test_screening_separates_rejects_from_results():
    report = screen_candidates(["[*]CCO[*]", "c1ccccc1"])
    assert len(report["results"]) == 1
    assert len(report["rejected_before_screening"]) == 1
    assert report["rejected_before_screening"][0]["smiles"] == "c1ccccc1"


@pytest.mark.parametrize(
    "payload",
    [
        generate_candidates(n=2, generator="baseline"),
        screen_candidates(["[*]CCO[*]"]),
        list_targets(),
    ],
)
def test_every_successful_payload_carries_a_caveat(payload):
    assert payload.get("note"), "a payload reached the agent with no interpretation note"


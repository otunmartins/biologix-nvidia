"""Boltz-2 adapter: request construction, response parsing, and refusals.

These tests pin the behaviour that makes the affinity number safe to publish:
that it is never invented when the service does not return one, that it always
carries its out-of-distribution flag, and that the molecule it describes is
recorded as the oligomer it actually was.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from biologix import EVIDENCE_COMPUTED
from biologix.errors import AdapterCallFailed, AdapterUnavailable, SequenceTooLong
from biologix.nvidia.boltz2 import (
    Boltz2Adapter,
    _first_float,
    _parse_affinity,
    boltz2_availability,
    predict_complex,
)
from biologix.nvidia.cache import JsonCache
from biologix.targets.models import Biologic, Chain

HEALTH = "http://127.0.0.1:8003/v1/health/ready"
PREDICT = "http://127.0.0.1:8003/predict"


def _adapter(settings, tmp_path):
    return Boltz2Adapter(settings, cache=JsonCache("boltz2", directory=tmp_path / "c"))


def _ok_body(**overrides):
    body = {
        "structures": [
            {"structure": "data_test\n_entry.id TEST\n", "format": "mmcif", "name": "model_0"}
        ],
        "confidence_scores": [0.82],
        "affinities": {
            "affinity_pred_value": [1.5],
            "affinity_probability_binary": [0.63],
            "model_1_affinity_pred_value": [1.4],
            "model_2_affinity_pred_value": [1.6],
        },
        "metrics": {"runtime_seconds": 12.5},
    }
    body.update(overrides)
    return body


# -- availability ----------------------------------------------------------


def test_hosted_without_key_is_unavailable(hosted_settings):
    report = boltz2_availability(hosted_settings)
    assert report["available"] is False
    assert "NVIDIA_API_KEY" in report["reason"]
    assert report["requires_api_key"] is True
    # Hosted endpoints have no container health path, so it must not be probed
    # and reported as a failure.
    assert report["health"]["probed"] is False


def test_availability_never_claims_verification(hosted_settings):
    report = boltz2_availability(hosted_settings)
    assert report["code_path_executed_in_this_build"] is False
    note = report["verification_note"]
    # The note's wording changes as live attempts are made and recorded; what
    # must never change is that it denies a successful live prediction and
    # says the response contract is unverified.
    assert "No successful live prediction has been obtained" in note
    assert "unverified" in note


@respx.mock
def test_local_healthy_is_available(local_settings):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    report = boltz2_availability(local_settings)
    assert report["available"] is True
    assert report["reason"] is None
    assert report["requires_api_key"] is False


@respx.mock
def test_local_unreachable_reports_the_remedy(local_settings):
    respx.get(HEALTH).mock(side_effect=httpx.ConnectError("refused"))
    report = boltz2_availability(local_settings)
    assert report["available"] is False
    assert "docker run" in report["reason"]


# -- request construction --------------------------------------------------


def test_chains_are_kept_separate(local_settings, tmp_path, insulin):
    polymers, _ = _adapter(local_settings, tmp_path).polymers_for(insulin)
    assert [p["id"] for p in polymers] == ["A", "B"]
    assert [len(p["sequence"]) for p in polymers] == [21, 30]
    # The concatenated 51-mer must appear nowhere.
    assert all(len(p["sequence"]) != 51 for p in polymers)
    assert {p["molecule_type"] for p in polymers} == {"protein"}


def test_disulfides_are_reported_as_not_sent(local_settings, tmp_path, insulin):
    _, unrepresented = _adapter(local_settings, tmp_path).polymers_for(insulin)
    assert len(unrepresented) == 3
    assert all("infers disulfides" in note for note in unrepresented)


def test_pegylation_is_reported_as_absent(local_settings, tmp_path, pegylated_insulin):
    _, unrepresented = _adapter(local_settings, tmp_path).polymers_for(pegylated_insulin)
    peg = [n for n in unrepresented if "pegylation" in n]
    assert len(peg) == 1
    assert "no CCD mapping" in peg[0]


def test_long_chain_is_refused_not_truncated(local_settings, tmp_path):
    big = Biologic(name="big", chains=(Chain(chain_id="A", sequence="A" * 5000),))
    with pytest.raises(SequenceTooLong) as exc:
        _adapter(local_settings, tmp_path).polymers_for(big)
    assert exc.value.length == 5000
    assert exc.value.limit == 4096


def test_too_many_chains_is_refused(local_settings, tmp_path):
    many = Biologic(
        name="many",
        chains=tuple(Chain(chain_id=chr(65 + i), sequence="GIVEQ") for i in range(13)),
    )
    with pytest.raises(ValueError, match="Refusing to drop chains"):
        _adapter(local_settings, tmp_path).polymers_for(many)


@respx.mock
def test_two_affinity_ligands_are_refused(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    adapter = _adapter(local_settings, tmp_path)
    with pytest.raises(ValueError, match="at most one per request"):
        adapter.predict(
            [{"id": "A", "molecule_type": "protein", "sequence": "GIVEQ"}],
            [
                {"id": "L1", "smiles": "CCO", "predict_affinity": True},
                {"id": "L2", "smiles": "CCC", "predict_affinity": True},
            ],
        )


@respx.mock
def test_ligand_needs_smiles_or_ccd(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    adapter = _adapter(local_settings, tmp_path)
    with pytest.raises(ValueError, match="smiles or ccd"):
        adapter.predict(
            [{"id": "A", "molecule_type": "protein", "sequence": "GIVEQ"}], [{"id": "L1"}]
        )


@respx.mock
@pytest.mark.parametrize(
    "kwargs",
    [
        {"recycling_steps": 7},
        {"sampling_steps": 5},
        {"diffusion_samples": 0},
        {"step_scale": 9.0},
    ],
)
def test_out_of_range_parameters_fail_locally(local_settings, tmp_path, kwargs):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(PREDICT).mock(return_value=httpx.Response(200, json=_ok_body()))
    adapter = _adapter(local_settings, tmp_path)
    with pytest.raises(ValueError, match="outside the documented range"):
        adapter.predict(
            [{"id": "A", "molecule_type": "protein", "sequence": "GIVEQ"}],
            [{"id": "L", "smiles": "CCO"}],
            **kwargs,
        )
    # The point of client-side checks: no request was sent.
    assert not route.called


# -- response parsing ------------------------------------------------------


@respx.mock
def test_predict_complex_parses_structure_and_affinity(
    local_settings, tmp_path, insulin, pvp_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(PREDICT).mock(return_value=httpx.Response(200, json=_ok_body()))

    result = predict_complex(
        insulin,
        pvp_candidate,
        adapter=_adapter(local_settings, tmp_path),
        settings=local_settings,
        dp_override=3,
    )

    assert len(result.structures) == 1
    assert result.structures[0].format == "mmcif"
    assert result.confidence_scores == [0.82]
    assert result.best_confidence == 0.82

    aff = result.affinity
    assert aff is not None
    assert aff.affinity_pred_value == 1.5
    assert aff.binding_probability == 0.63
    assert aff.evidence == EVIDENCE_COMPUTED
    assert aff.interpretation == "model feature"
    # The flag that stops this being read as a Kd.
    assert aff.out_of_distribution is True
    assert "outside that distribution" in aff.out_of_distribution_reason


@respx.mock
def test_affinity_pic50_is_derived_and_labelled(local_settings, tmp_path, insulin, pvp_candidate):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(PREDICT).mock(return_value=httpx.Response(200, json=_ok_body()))
    result = predict_complex(
        insulin, pvp_candidate, adapter=_adapter(local_settings, tmp_path),
        settings=local_settings, dp_override=3,
    )
    # log10(IC50/uM) = 1.5  ->  pIC50 = 6 - 1.5
    assert result.affinity.pic50 == pytest.approx(4.5)
    assert "derived locally" in result.affinity.pic50_source


def test_reported_pic50_is_preferred_over_derivation():
    aff = _parse_affinity(
        {"affinity_pred_value": 1.5, "affinity_pic50": 7.25}, "LIG", mw_correction=False
    )
    assert aff.pic50 == 7.25
    assert "reported by the service" in aff.pic50_source


@respx.mock
def test_missing_affinity_block_yields_none_not_a_default(
    local_settings, tmp_path, insulin, pvp_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    body = _ok_body()
    del body["affinities"]
    respx.post(PREDICT).mock(return_value=httpx.Response(200, json=body))

    result = predict_complex(
        insulin, pvp_candidate, adapter=_adapter(local_settings, tmp_path),
        settings=local_settings, dp_override=3,
    )
    assert result.affinity is None
    assert any("no usable affinity block" in w for w in result.manifest.warnings)


def test_affinity_block_keyed_by_ligand_id():
    aff = _parse_affinity(
        {"LIG": {"affinity_pred_value": 2.0, "affinity_probability_binary": 0.5}},
        "LIG",
        mw_correction=False,
    )
    assert aff.affinity_pred_value == 2.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.5, 1.5),
        ([1.5], 1.5),
        ([1.5, 2.5], 1.5),
        ((3.0,), 3.0),
        ([], None),
        (None, None),
        ("nope", None),
        (True, None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_first_float_normalises_scalars_and_sequences(value, expected):
    assert _first_float(value) == expected


# -- provenance ------------------------------------------------------------


@respx.mock
def test_oligomer_identity_travels_with_the_result(
    local_settings, tmp_path, insulin, pvp_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(PREDICT).mock(return_value=httpx.Response(200, json=_ok_body()))

    result = predict_complex(
        insulin, pvp_candidate, adapter=_adapter(local_settings, tmp_path),
        settings=local_settings, dp_override=3,
    )
    olig = result.oligomer
    assert olig["degree_of_polymerisation"] == 3
    assert "not a polydisperse polymer" in olig["interpretation_note"]
    # The substitution of DP-3 for the candidate's requested DP-25 is stated.
    assert "DP=25" in olig["dp_override_reason"]
    assert result.single_sequence_mode is True
    assert "less accurate" in result.summary()["accuracy_note"]


@respx.mock
def test_unrepresented_chemistry_becomes_manifest_warnings(
    local_settings, tmp_path, pegylated_insulin, pvp_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(PREDICT).mock(return_value=httpx.Response(200, json=_ok_body()))
    result = predict_complex(
        pegylated_insulin, pvp_candidate, adapter=_adapter(local_settings, tmp_path),
        settings=local_settings, dp_override=3,
    )
    assert any("pegylation" in w for w in result.manifest.warnings)


# -- failure modes ---------------------------------------------------------


@respx.mock
def test_http_error_raises_call_failed_not_unavailable(
    local_settings, tmp_path, insulin, pvp_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(PREDICT).mock(return_value=httpx.Response(503, text="overloaded"))
    with pytest.raises(AdapterCallFailed) as exc:
        predict_complex(
            insulin, pvp_candidate, adapter=_adapter(local_settings, tmp_path),
            settings=local_settings, dp_override=3,
        )
    assert exc.value.status == 503


def test_no_credentials_raises_unavailable(hosted_settings, tmp_path, insulin, pvp_candidate):
    adapter = Boltz2Adapter(
        hosted_settings, cache=JsonCache("boltz2", directory=tmp_path / "c")
    )
    with pytest.raises(AdapterUnavailable):
        predict_complex(
            insulin, pvp_candidate, adapter=adapter, settings=hosted_settings, dp_override=3
        )


@respx.mock
def test_second_identical_call_is_served_from_cache(
    local_settings, tmp_path, insulin, pvp_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(PREDICT).mock(return_value=httpx.Response(200, json=_ok_body()))
    cache = JsonCache("boltz2", directory=tmp_path / "shared")

    first = predict_complex(
        insulin, pvp_candidate, adapter=Boltz2Adapter(local_settings, cache=cache),
        settings=local_settings, dp_override=3,
    )
    second = predict_complex(
        insulin, pvp_candidate, adapter=Boltz2Adapter(local_settings, cache=cache),
        settings=local_settings, dp_override=3,
    )

    assert route.call_count == 1
    assert first.cached is False
    assert second.cached is True
    # A cache hit must be recorded, not hidden: it is a different claim about
    # where the number came from.
    assert second.manifest.outputs["cached"] is True

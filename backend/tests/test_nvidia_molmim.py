"""MolMIM adapter: request shaping, the objective caveat, and repeat-unit yield.

The behaviour worth pinning here is the objective discipline. MolMIM's built-in
objectives are drug-likeness scores, which are the wrong target for an
excipient. The adapter must therefore default to plain similarity-constrained
sampling, must not send optimisation fields the service would ignore, and must
attach a caveat whenever CMA-ES is used anyway.
"""

from __future__ import annotations

import httpx
import numpy as np
import pytest
import respx

from biologix import EVIDENCE_HYPOTHESIS
from biologix.errors import AdapterCallFailed, AdapterUnavailable
from biologix.nvidia.cache import JsonCache
from biologix.nvidia.molmim import (
    MolMIMAdapter,
    MolMIMGenerator,
    molmim_availability,
    property_objective_caveat,
)

HEALTH = "http://127.0.0.1:8002/v1/health/ready"
GENERATE = "http://127.0.0.1:8002/generate"
EMBEDDING = "http://127.0.0.1:8002/embedding"

# Vinyl-amide and acrylate molecules, i.e. things that do convert to repeat
# units, plus one that does not.
GENERATED = [
    "C=CN1CCCC1=O",
    "C=CC(=O)N",
    "C=CC(=O)NC",
    "C=CC(=O)OCCO",
    "c1ccccc1",
]


def _adapter(settings, tmp_path, **kw):
    return MolMIMAdapter(
        settings, cache=JsonCache("molmim", directory=tmp_path / "c"), **kw
    )


# -- availability ----------------------------------------------------------


def test_hosted_without_key_is_unavailable(hosted_settings):
    report = molmim_availability(hosted_settings)
    assert report["available"] is False
    assert "NVIDIA_API_KEY" in report["reason"]


def test_availability_states_the_objective_mismatch(hosted_settings):
    report = molmim_availability(hosted_settings)
    assert report["default_algorithm"] == "none"
    assert "poor fit for excipient design" in report["objective_note"]


@respx.mock
def test_local_healthy_is_available(local_settings):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    assert molmim_availability(local_settings)["available"] is True


# -- request shaping -------------------------------------------------------


@respx.mock
def test_sampling_mode_omits_optimisation_fields(local_settings, tmp_path):
    """Fields the service ignores must not be sent, so the recorded request
    matches what the service actually acted on."""
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )

    _adapter(local_settings, tmp_path).generate_molecules("C=CN1CCCC1=O", algorithm="none")

    sent = route.calls[0].request
    import json

    payload = json.loads(sent.content)
    assert payload["algorithm"] == "none"
    assert "scaled_radius" in payload
    for omitted in ("property_name", "minimize", "min_similarity", "iterations"):
        assert omitted not in payload


@respx.mock
def test_cma_es_mode_sends_optimisation_fields(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )
    _adapter(local_settings, tmp_path).generate_molecules(
        "C=CN1CCCC1=O", algorithm="CMA-ES", property_name="plogP", minimize=True
    )
    import json

    payload = json.loads(route.calls[0].request.content)
    assert payload["property_name"] == "plogP"
    assert payload["minimize"] is True
    assert "scaled_radius" not in payload


@respx.mock
@pytest.mark.parametrize(
    "kwargs",
    [
        {"num_molecules": 0},
        {"num_molecules": 101},
        {"particles": 1},
        {"algorithm": "CMA-ES", "min_similarity": 0.9},
        {"algorithm": "CMA-ES", "iterations": 0},
        {"scaled_radius": 2.5},
    ],
)
def test_out_of_range_parameters_fail_locally(local_settings, tmp_path, kwargs):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )
    with pytest.raises(ValueError, match="outside the documented range"):
        _adapter(local_settings, tmp_path).generate_molecules("CCO", **kwargs)
    assert not route.called


@respx.mock
def test_unknown_algorithm_and_property_are_refused(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    adapter = _adapter(local_settings, tmp_path)
    with pytest.raises(ValueError, match="algorithm must be one of"):
        adapter.generate_molecules("CCO", algorithm="genetic")
    with pytest.raises(ValueError, match="property_name must be one of"):
        adapter.generate_molecules("CCO", algorithm="CMA-ES", property_name="logS")
    with pytest.raises(ValueError, match="non-empty SMILES"):
        adapter.generate_molecules("   ")


# -- response parsing ------------------------------------------------------


@respx.mock
def test_nested_generated_list_is_flattened(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": [["CCO", "CCC"], ["CCN"]]})
    )
    response = _adapter(local_settings, tmp_path).generate_molecules("CCO")
    assert response.smiles == ["CCO", "CCC", "CCN"]


@respx.mock
def test_wrong_generated_type_raises(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json={"generated": "CCO"}))
    with pytest.raises(AdapterCallFailed, match="expected a list"):
        _adapter(local_settings, tmp_path).generate_molecules("CCO")


@respx.mock
def test_blank_entries_are_dropped(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": ["CCO", "", None, "  ", "CCN"]})
    )
    assert _adapter(local_settings, tmp_path).generate_molecules("CCO").smiles == ["CCO", "CCN"]


# The hosted endpoint's body, as observed live on 2026-10-13: ``molecules`` is
# a JSON *string*, not a list, and there is no ``generated`` key at all.
HOSTED_BODY = {
    "molecules": (
        '[{"sample": "CN=C1CCCC1=O", "score": 0.1875}, '
        '{"sample": "CCNC1=NC(=O)CC1", "score": 0.13513513513513514}]'
    ),
    "score_type": "tanimoto_similarity",
}


@respx.mock
def test_hosted_molecules_string_is_parsed(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json=HOSTED_BODY))
    adapter = _adapter(local_settings, tmp_path)

    response = adapter.generate_molecules("C=CN1CCCC1=O")
    assert response.smiles == ["CN=C1CCCC1=O", "CCNC1=NC(=O)CC1"]
    assert response.scores == [0.1875, 0.13513513513513514]
    assert response.score_type == "tanimoto_similarity"

    # The scores survive the cache, not just the SMILES.
    again = adapter.generate_molecules("C=CN1CCCC1=O")
    assert again.cached is True
    assert again.smiles == response.smiles
    assert again.scores == response.scores
    assert again.score_type == "tanimoto_similarity"


@respx.mock
def test_hosted_molecules_that_are_not_json_raise(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json={"molecules": "not json"}))
    with pytest.raises(AdapterCallFailed, match="not JSON"):
        _adapter(local_settings, tmp_path).generate_molecules("CCO")


# -- embeddings ------------------------------------------------------------


@respx.mock
def test_embeddings_are_returned_as_a_2d_array(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(EMBEDDING).mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]})
    )
    array = _adapter(local_settings, tmp_path).embed(["CCO", "CCN"])
    assert array.shape == (2, 3)
    assert array.dtype == np.float32


@respx.mock
def test_embedding_row_count_mismatch_raises(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(EMBEDDING).mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
    )
    with pytest.raises(AdapterCallFailed, match="expected embeddings of shape"):
        _adapter(local_settings, tmp_path).embed(["CCO", "CCN"])


def test_embedding_empty_input_makes_no_call(local_settings, tmp_path):
    assert _adapter(local_settings, tmp_path).embed([]).shape == (0, 0)


# -- the objective caveat --------------------------------------------------


@pytest.mark.parametrize("prop", ["QED", "plogP"])
def test_caveat_names_the_objective_and_direction(prop):
    text = property_objective_caveat(prop, minimize=False)
    assert prop in text
    assert "maximised" in text
    assert "not" in text
    assert property_objective_caveat(prop, minimize=True).count("minimised") == 1


@respx.mock
def test_cma_es_run_carries_the_caveat_in_the_manifest(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )
    result = MolMIMGenerator(_adapter(local_settings, tmp_path), settings=local_settings).generate(
        n=3, seeds=(("vinylpyrrolidone", "C=CN1CCCC1=O"),), algorithm="CMA-ES"
    )
    assert any("QED" in w for w in result.manifest.warnings)


@respx.mock
def test_sampling_run_carries_no_objective_caveat(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )
    result = MolMIMGenerator(_adapter(local_settings, tmp_path), settings=local_settings).generate(
        n=3, seeds=(("vinylpyrrolidone", "C=CN1CCCC1=O"),), algorithm="none"
    )
    assert not any("QED" in w for w in result.manifest.warnings)
    assert result.manifest.parameters["property_name"] is None


# -- generation ------------------------------------------------------------


@respx.mock
def test_molecules_become_validated_repeat_units(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )
    result = MolMIMGenerator(_adapter(local_settings, tmp_path), settings=local_settings).generate(
        n=10, seeds=(("vinyl amide", "C=CC(=O)N"),)
    )
    assert result.generator == "molmim-assisted"
    assert result.manifest.evidence == EVIDENCE_HYPOTHESIS
    assert result.candidates, "expected at least one polymerisable molecule to convert"
    assert all(c.blocks[0].unit.source == "molmim" for c in result.candidates)
    # Benzene has no polymerisable handle and must be recorded as excluded,
    # not quietly dropped.
    assert any(e["stage"] == "monomer-to-repeat-unit" for e in result.manifest.exclusions)
    assert result.n_proposed > 0


@respx.mock
def test_unavailable_service_yields_an_empty_run_not_a_crash(hosted_settings, tmp_path):
    """A generator whose backend is missing reports zero candidates and says
    why, so a pipeline can carry on with the baseline generator."""
    adapter = MolMIMAdapter(
        hosted_settings, cache=JsonCache("molmim", directory=tmp_path / "c")
    )
    result = MolMIMGenerator(adapter, settings=hosted_settings).generate(n=5)
    assert result.candidates == []
    assert result.manifest.errors
    assert any("no molecules were generated" in w for w in result.manifest.warnings)


@respx.mock
def test_optimise_candidate_seeds_from_an_oligomer_not_a_repeat_unit(
    local_settings, tmp_path, pvp_candidate
):
    """A repeat-unit SMILES contains ``[*]`` dummy atoms, which MolMIM was
    never trained on, so the seed must be a real capped molecule."""
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )
    result = MolMIMGenerator(
        _adapter(local_settings, tmp_path), settings=local_settings
    ).optimise_candidate(pvp_candidate, n=3, dp_override=2)

    import json

    seed = json.loads(route.calls[0].request.content)["smi"]
    assert "*" not in seed
    assert result.manifest.inputs["parent_candidate_id"] == pvp_candidate.candidate_id
    assert result.manifest.inputs["parent_oligomer"]["degree_of_polymerisation"] == 2
    assert all(c.parent_id == pvp_candidate.candidate_id for c in result.candidates)
    assert all(c.generation_round == 1 for c in result.candidates)


def test_no_credentials_raises_unavailable(hosted_settings, tmp_path):
    adapter = MolMIMAdapter(
        hosted_settings, cache=JsonCache("molmim", directory=tmp_path / "c")
    )
    with pytest.raises(AdapterUnavailable):
        adapter.generate_molecules("CCO")


@respx.mock
def test_identical_call_is_served_from_cache(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"generated": GENERATED})
    )
    cache = JsonCache("molmim", directory=tmp_path / "shared")
    first = MolMIMAdapter(local_settings, cache=cache).generate_molecules("CCO")
    second = MolMIMAdapter(local_settings, cache=cache).generate_molecules("CCO")
    assert route.call_count == 1
    assert first.cached is False
    assert second.cached is True

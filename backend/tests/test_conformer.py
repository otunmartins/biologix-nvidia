"""3D coordinates for a candidate's own oligomer.

The panel these back is the one a user reads during triage, so the tests
cover the things that would make the picture wrong or misleading rather than
merely absent: that the coordinates are real and complete, that the depicted
chain length is stated rather than implied, and that a candidate which cannot
be embedded says why.
"""

from __future__ import annotations

import pytest

from biologix.polymers.conformer import (
    MAX_DEPICTION_DP,
    MIN_DEPICTION_DP,
    ConformerFailed,
    depiction_dp,
    embed_repeat_unit,
    embed_repeat_unit_cached,
)
from biologix.polymers.generate import candidates_from_smiles

# Poly(hydroxyethyl acrylamide) and polylactide: one vinyl, one ring-opening,
# so both derivation paths are covered.
ACRYLAMIDE = "*CC(*)C(=O)NCCO"
LACTIDE = "*OC(C)C(=O)OC(C)C(*)=O"


def _candidate(smiles: str, dp: int = 25):
    result = candidates_from_smiles([smiles], dp=dp)
    assert result.candidates, f"{smiles} should be a usable repeat unit"
    return result.candidates[0]


@pytest.mark.parametrize("smiles", [ACRYLAMIDE, LACTIDE])
def test_the_conformer_has_real_three_dimensional_coordinates(smiles: str) -> None:
    """Atoms must be spread over three axes, not collapsed onto a plane."""
    conformer = embed_repeat_unit(smiles)
    lines = [
        line
        for line in conformer.pdb.splitlines()
        if line.startswith(("ATOM", "HETATM"))
    ]
    assert len(lines) == conformer.n_atoms

    zs = {round(float(line[46:54]), 3) for line in lines}
    assert len(zs) > 5, "a flat structure would mean the embedding silently failed"


@pytest.mark.parametrize("smiles", [ACRYLAMIDE, LACTIDE])
def test_both_coordinate_formats_are_produced(smiles: str) -> None:
    conformer = embed_repeat_unit(smiles)
    assert conformer.pdb.strip()
    assert "V2000" in conformer.sdf or "V3000" in conformer.sdf


def test_the_depicted_chain_length_is_reported() -> None:
    """The picture is a short chain, and must never imply the real DP.

    A candidate at DP 50 is tens of thousands of atoms; the panel draws a
    handful of repeats and has to say so, or the render overstates what was
    computed.
    """
    conformer = embed_repeat_unit_cached(ACRYLAMIDE, 50)
    assert conformer.degree_of_polymerisation < 50
    assert conformer.metadata()["degree_of_polymerisation"] == (
        conformer.degree_of_polymerisation
    )


def test_a_bulky_repeat_unit_is_drawn_with_fewer_repeats() -> None:
    """Depiction length scales down with unit size, so pictures stay comparable."""
    small = depiction_dp(_candidate("*CC(*)C(N)=O"))
    bulky = depiction_dp(_candidate("*CC(*)C(=O)N1CCC(C(=O)N2CCC(C)CC2)CC1"))
    assert bulky < small
    for value in (small, bulky):
        assert MIN_DEPICTION_DP <= value <= MAX_DEPICTION_DP


def test_the_metadata_carries_the_caveat_and_the_force_field() -> None:
    """A 3D render is persuasive, so what it is must travel with it."""
    metadata = embed_repeat_unit(ACRYLAMIDE).metadata()
    assert metadata["evidence"] == "computed"
    assert "vacuum" in metadata["caveat"]
    assert metadata["force_field"] in ("MMFF94s", "UFF")
    assert metadata["n_heavy_atoms"] > 0
    assert metadata["molecular_weight"] > 0


def test_the_conformer_is_deterministic() -> None:
    """Two calls give the identical structure, not an equivalent one.

    A user comparing screenshots has to be comparing chemistry rather than
    two draws from a random embedder.
    """
    first = embed_repeat_unit(ACRYLAMIDE)
    second = embed_repeat_unit(ACRYLAMIDE)
    assert first.pdb == second.pdb


def test_the_cache_returns_the_same_structure() -> None:
    first = embed_repeat_unit_cached(ACRYLAMIDE, 25)
    second = embed_repeat_unit_cached(ACRYLAMIDE, 25)
    assert first.pdb == second.pdb


def test_an_unusable_repeat_unit_fails_with_a_reason() -> None:
    """Not an empty structure the viewer would render as nothing."""
    with pytest.raises(ConformerFailed) as caught:
        embed_repeat_unit_cached("CCO", 10)
    assert str(caught.value)


def test_an_oversized_oligomer_is_refused_rather_than_embedded() -> None:
    """The heavy-atom ceiling is a real limit, and says what to do about it."""
    from biologix.polymers.conformer import embed_candidate

    with pytest.raises(ConformerFailed) as caught:
        embed_candidate(_candidate(ACRYLAMIDE), dp=30, max_heavy_atoms=100)
    assert "heavy atoms" in str(caught.value)


def test_an_assembly_refusal_is_reported_as_a_conformer_failure() -> None:
    """Assembly has its own limits, and they must not escape as a 500.

    ``oligomer_for_screening`` refuses a chain of its own accord past 2000
    atoms. That is an answer about this candidate, so the endpoint has to see
    a ConformerFailed and return 422 rather than an unhandled error.
    """
    from biologix.polymers.conformer import embed_candidate

    with pytest.raises(ConformerFailed) as caught:
        embed_candidate(_candidate(ACRYLAMIDE), dp=400)
    assert "degree of polymerisation" in str(caught.value)


def test_metadata_is_json_serialisable() -> None:
    """It is returned by the API, so it has to serialise."""
    import json

    payload = embed_repeat_unit(ACRYLAMIDE).metadata()
    assert json.loads(json.dumps(payload)) == payload

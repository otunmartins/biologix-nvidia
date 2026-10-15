"""Can it be made, and does the claim hold up?

These are the tests that matter most in this repository, because they are the
ones standing between a generative pipeline and a shortlist of molecules
nobody can synthesise. Each case below is a specific chemical claim, and the
refusals are as important as the acceptances.
"""

from __future__ import annotations

import pytest

from biologix.polymers.derive import derivation_statistics, derive_repeat_units
from biologix.polymers.grafting import graft_molecule
from biologix.polymers.synthesis import (
    assess_route,
    attachment_span,
    recover_monomer,
    synthetic_accessibility,
)

# -- the monomer is recovered, not remembered ------------------------------


@pytest.mark.parametrize(
    ("repeat_unit", "mechanism", "monomer"),
    [
        ("*CC(*)C(N)=O", "chain_growth_vinyl", "C=CC(N)=O"),
        ("*CC(*)c1ccccc1", "chain_growth_vinyl", "C=Cc1ccccc1"),
        ("*CC(*)N1CCCC1=O", "chain_growth_vinyl", "C=CN1CCCC1=O"),
        ("*OCCO*", "step_growth_diol", "OCCO"),
        ("*NCCCCCCN*", "step_growth_diamine", "NCCCCCCN"),
        ("*CCO*", "ring_opening_epoxide", "C1CO1"),
    ],
)
def test_the_monomer_is_rebuilt_from_the_repeat_unit(
    repeat_unit: str, mechanism: str, monomer: str
) -> None:
    """The monomer shown must really map onto the stored repeat unit."""
    assert recover_monomer(repeat_unit, mechanism) == monomer


def test_a_mechanism_that_does_not_match_the_unit_recovers_nothing() -> None:
    """A PEG backbone unit is not a condensation of two alcohols.

    ``*OCC*`` has one attachment on oxygen and one on carbon. Handing the
    carbon a hydrogen would invent ethanol as "the monomer", which is exactly
    the kind of silent fabrication this returns None for instead.
    """
    assert recover_monomer("*OCC*", "step_growth_diol") is None


# -- the refusals that matter ---------------------------------------------


def test_an_internal_alkene_is_refused() -> None:
    """1,2-disubstituted alkenes do not homopolymerise. 2-butene is not a monomer."""
    route = assess_route("*C(CCCCCCCCCCCCCCCC)C(*)CCCCCCCCCCCCCCCCCCCCCCC", "chain_growth_vinyl")
    assert route.confidence == "refused"
    assert not route.defensible
    assert "internal" in route.reasoning
    assert "do not homopolymerise" in route.reasoning


def test_oxime_and_imidic_acid_are_not_a_diol() -> None:
    """The most common way to draw a polymer that cannot be made.

    Two O-H groups are not two alcohols: phenols, enols, oximes, imidic acids
    and carboxylic acids all present an O-H and none of them esterifies like a
    diol.
    """
    route = assess_route(r"*O/N=C(\C)C(=O)N1CCC(C(=N)O*)CC1", "step_growth_diol")
    assert route.confidence == "refused"
    assert "aliphatic alcohols" in route.reasoning


def test_an_aromatic_ring_amide_is_not_a_lactam() -> None:
    """The engine labels this a lactam ring-opening; it is nothing of the kind.

    The unit is an aromatic amine at one end and a carbonyl at the other, so
    the real route is an aramid-type condensation. Re-reading it that way is
    better than refusing it — but it must not be graded like peptide chemistry,
    because aromatic polyamides are rigid and insoluble.
    """
    route = assess_route("*Nc1ccc(NC(=O)C(*)=O)cc1", "ring_opening_lactam")
    assert route.mechanism == "step_growth_amino_acid"
    assert route.monomer_class == "aromatic amino acid"
    assert route.confidence == "plausible"
    assert "not the peptide route" in route.reasoning
    assert any("rigid" in concern for concern in route.concerns)


def test_a_tetrasubstituted_alkene_is_refused() -> None:
    route = assess_route("*C(C)(C)C(*)(C)C", "chain_growth_vinyl")
    assert route.confidence == "refused"


# -- the acceptances, with the right class and precedent ------------------


@pytest.mark.parametrize(
    ("repeat_unit", "monomer_class"),
    [
        ("*CC(*)C(N)=O", "acrylamide"),
        ("*CC(*)C(=O)OC", "acrylate"),
        ("*CC(*)C(=O)N1CCOCC1", "acrylamide"),
        ("*CC(*)N1CCCC1=O", "n-vinyl amide"),
        ("*CC(*)n1ccnc1", "n-vinyl amide"),
        ("*CC(*)c1ccccc1", "styrenic"),
        ("*CC(*)OC", "vinyl ether"),
    ],
)
def test_established_vinyl_classes(repeat_unit: str, monomer_class: str) -> None:
    route = assess_route(repeat_unit, "chain_growth_vinyl")
    assert route.confidence == "established", route.reasoning
    assert route.monomer_class == monomer_class
    assert route.precedent, "an established class must name a precedent polymer"
    assert route.retro_steps
    assert route.reagents


def test_poly_vinylpyrrolidone_is_recognised_as_established() -> None:
    """PVP is a parenteral excipient in current use; it must not be refused."""
    route = assess_route("*CC(*)N1CCCC1=O", "chain_growth_vinyl")
    assert route.defensible
    assert "vinylpyrrolidone" in (route.precedent or "")


def test_an_acrylamide_route_warns_about_residual_monomer() -> None:
    """Monomer toxicity is a real obstacle and must travel with the route."""
    route = assess_route("*CC(*)C(N)=O", "chain_growth_vinyl")
    assert any("residual" in concern for concern in route.concerns)


def test_alpha_substitution_moves_a_vinyl_ether_out_of_radical_chemistry() -> None:
    route = assess_route("*CC(*)(C)OC", "chain_growth_vinyl")
    assert route.confidence == "plausible"
    assert "cationically" in route.reasoning or "anionically" in route.reasoning


def test_an_ethylene_spaced_unit_is_a_copolymer_unit() -> None:
    """``-CH2-CH2-CH(R)-`` is EVOH-type chemistry, not a homopolymer unit."""
    assert attachment_span("*CCC(*)OC") == 2
    route = assess_route("*CCC(*)OC", "chain_growth_vinyl")
    assert route.monomer_class == "ethylene copolymer"
    assert route.monomer_smiles == "C=COC"
    assert "copolymer" in route.reasoning
    assert any("statistical" in concern for concern in route.concerns)


def test_an_amide_backbone_is_read_as_an_amino_acid_route() -> None:
    """One amine and one carbonyl is the amino-acid case, not a diamine."""
    route = assess_route("*NCC(*)=O", "step_growth_diamine")
    assert route.monomer_class == "amino acid"
    assert route.monomer_smiles == "NCC(=O)O"
    assert route.confidence == "established"
    assert "N-carboxyanhydride" in route.reasoning


def test_ring_opening_needs_a_ring_that_opens() -> None:
    assert assess_route("*CCO*", "ring_opening_epoxide").confidence == "established"
    lactone = assess_route("*OC(=O)CCCCC*", "ring_opening_lactone")
    assert lactone.confidence in ("established", "speculative")


# -- synthetic accessibility ----------------------------------------------


def test_sa_score_is_available_and_ordered_sensibly() -> None:
    simple = synthetic_accessibility("C=CC(N)=O")
    complex_molecule = synthetic_accessibility("CC1(C)SC2C(NC(=O)C(N)c3ccccc3)C(=O)N2C1C(=O)O")
    if simple is None:  # RDKit built without the contrib tree
        pytest.skip("SA score unavailable in this build")
    assert 1 <= simple <= 10
    assert complex_molecule is not None
    assert complex_molecule > simple


def test_a_hard_to_make_monomer_is_flagged_as_a_concern() -> None:
    route = assess_route("*CC(*)C(N)=O", "chain_growth_vinyl")
    assert route.sa_score is not None
    assert "SA score" in route.sa_note


# -- grafting: the workaround ---------------------------------------------


def test_an_alcohol_is_grafted_to_an_acrylate() -> None:
    """A molecule that is not a monomer becomes a pendant on one that is."""
    result = graft_molecule("OC1(CN2CCN(Cc3ccccc3)CC2)CCC1")
    assert result.grafted
    backbones = {route.backbone for route in result.routes}
    assert backbones == {"acrylate", "methacrylate"}

    route = result.routes[0]
    assert route.repeat_unit.count("*") == 2
    assert route.monomer.startswith("C=C")
    assert route.route.confidence == "established"
    assert route.route.derivation == "graft"
    # The route must say what was consumed, in order, with reagents.
    assert len(route.route.retro_steps) == 3
    assert "acryloyl chloride" in route.route.reagents[0]


def test_an_amine_is_grafted_to_an_acrylamide() -> None:
    result = graft_molecule("c1ccc(CNC2CCC3(CCCCC3)CC2)cc1")
    assert result.grafted
    assert {r.backbone for r in result.routes} == {"acrylamide", "methacrylamide"}
    assert all(r.monomer.startswith("C=C") for r in result.routes)


def test_two_handles_are_refused_because_they_would_crosslink() -> None:
    """A bis-acrylate is a crosslinker: it gives a gel, not a soluble polymer."""
    result = graft_molecule("OCCCCO")
    assert not result.grafted
    assert result.refusal_code == "would_crosslink"
    assert "gel" in (result.refusal or "")


def test_a_molecule_with_no_handle_is_refused_plainly() -> None:
    result = graft_molecule("NC(=O)c1sccc1-c1ccc(F)c(F)c1")
    assert not result.grafted
    assert result.refusal_code == "no_graftable_handle"


def test_an_acid_only_molecule_is_refused_with_the_longer_route_named() -> None:
    result = graft_molecule("O=C(O)CCc1coc2nc(Cl)ncc12")
    assert result.refusal_code == "acid_needs_coupling"
    assert "HEMA" in (result.refusal or "")


def test_the_graft_says_which_group_it_consumed() -> None:
    """If the graft ate the group that mattered, the user has to be able to see it."""
    result = graft_molecule("OC1(CN2CCN(Cc3ccccc3)CC2)CCC1")
    concerns = result.routes[0].route.concerns
    assert any("no longer available to interact" in concern for concern in concerns)


# -- the derivation layer end to end --------------------------------------


def test_a_real_monomer_derives_directly() -> None:
    result = derive_repeat_units("C=CC(N)=O")
    assert result.usable
    assert result.units[0].derivation == "direct"
    assert result.units[0].route.confidence == "established"


def test_a_drug_like_molecule_is_rescued_by_grafting() -> None:
    result = derive_repeat_units("OC1(CN2CCN(Cc3ccccc3)CC2)CCC1")
    assert result.usable
    assert all(unit.derivation == "graft" for unit in result.units)


def test_an_indefensible_route_is_dropped_and_explained() -> None:
    """The oxime pair must not reach a candidate list."""
    result = derive_repeat_units(r"CC(=N\O)C(=O)N1CCC(C(=N)O)CC1")
    if result.usable:
        # If it survived, it must be by a different, defensible route.
        assert all(unit.route.defensible for unit in result.units)
    else:
        assert "mechanism_not_feasible" in result.rejection_codes() or result.graft_refusal
        assert result.rejection_detail()


def test_keeping_indefensible_routes_is_opt_in() -> None:
    strict = derive_repeat_units("CCCCCCCC=CCCCCCCC", require_defensible=True)
    loose = derive_repeat_units("CCCCCCCC=CCCCCCCC", require_defensible=False, allow_grafting=False)
    assert not strict.usable
    assert loose.usable, "the structural reading is still available when asked for"
    assert not loose.units[0].route.defensible


def test_derivation_statistics_report_what_they_measure() -> None:
    results = [
        derive_repeat_units("C=CC(N)=O"),
        derive_repeat_units("OC1(CN2CCN(Cc3ccccc3)CC2)CCC1"),
        derive_repeat_units("NC(=O)c1sccc1-c1ccc(F)c(F)c1"),
    ]
    stats = derivation_statistics(results)
    assert stats["n_molecules"] == 3
    assert stats["n_usable"] == 2
    assert stats["n_direct"] == 1
    assert stats["n_graft_only"] == 1
    assert stats["defensible_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert "defensible_rate is the fraction" in stats["note"]

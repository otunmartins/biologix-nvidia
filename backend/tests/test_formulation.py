"""The sequence-derived formulation profile.

These tests pin the properties that make generation target-aware. The numbers
are checked against what the chemistry requires, not against whatever the
implementation currently returns: insulin is the more hydrophobic of the two
peptides and GLP-1 the more hydrophilic, and that ordering is the whole basis
for giving them different excipient chemistry.
"""

from __future__ import annotations

import pytest

from biologix.targets.formulation import (
    aromatic_fraction,
    free_cysteines,
    gravy,
    isoelectric_point,
    net_charge_at,
    profile_target,
)
from biologix.targets.models import Biologic, BiologicClass, Chain
from biologix.targets.registry import get_target


def test_insulin_isoelectric_point_matches_the_literature() -> None:
    """Human insulin's pI is about 5.3; the computed value has to be close.

    This is the one number in the profile with a well-known experimental
    value, so it is the check that the charge model is not merely
    self-consistent.
    """
    assert isoelectric_point(get_target("human-insulin")) == pytest.approx(5.3, abs=0.4)


def test_insulin_is_net_negative_at_physiological_ph() -> None:
    charge = net_charge_at(get_target("human-insulin"), 7.4)
    assert charge < 0, "insulin sits above its pI at pH 7.4, so it must be net negative"


def test_aspart_is_more_acidic_than_human_insulin() -> None:
    """B28 Pro->Asp adds a carboxylate, so the pI must fall."""
    human = isoelectric_point(get_target("human-insulin"))
    aspart = isoelectric_point(get_target("insulin-aspart"))
    assert aspart < human


def test_insulin_is_hydrophobic_and_glp1_is_not() -> None:
    """The ordering that drives the two targets to different chemistry."""
    assert gravy(get_target("human-insulin")) > gravy(get_target("glp1-7-37"))
    assert gravy(get_target("glp1-7-37")) < 0


def test_declared_disulfides_account_for_every_insulin_cysteine() -> None:
    """Six cysteines, three disulfides, so no free thiol is attributed."""
    assert free_cysteines(get_target("human-insulin")) == 0


def test_undeclared_cysteines_count_as_free_thiols() -> None:
    """A pasted sequence has no modifications, so its cysteines are free.

    The conservative reading matters: the consequence of being wrong is an
    unflagged Michael-addition risk against residual monomer.
    """
    bare = Biologic(
        name="two bare cysteines",
        biologic_class=BiologicClass.PEPTIDE,
        chains=(Chain(chain_id="A", sequence="ACDEFGHIKC"),),
    )
    assert free_cysteines(bare) == 2
    assert "michael-acceptor" in profile_target(bare).penalise


def test_glp1_has_no_cysteine_and_no_thiol_penalty() -> None:
    glp1 = get_target("glp1-7-37")
    assert free_cysteines(glp1) == 0
    assert "michael-acceptor" not in profile_target(glp1).penalise


def test_insulin_gets_an_amphiphilic_logp_window() -> None:
    """Insulin is mildly hydrophobic (GRAVY about +0.22), so it wants some
    lipophilicity rather than the most polar chain available.

    Ranking everything on "as polar as possible" gave every target the same
    cohort, so this window is what makes the profile actually differentiate.
    """
    profile = profile_target(get_target("human-insulin"))
    low, high = profile.logp_window
    assert high > 0, "an amphiphile needs some lipophilicity to adsorb onto a patch"
    assert high <= 1.2, "the window must stay inside the aqueous-parenteral ceiling"
    assert "amphiphilic" in profile.favour


def test_a_strongly_hydrophobic_target_wants_a_positive_logp_band() -> None:
    """Past the strong-hydrophobicity threshold the whole band sits above zero.

    A fully hydrophilic chain has nothing to adsorb onto an exposed non-polar
    patch with, which is the mechanism an excipient has to supply here.
    """
    greasy = Biologic(
        name="membrane-like",
        biologic_class=BiologicClass.PROTEIN,
        chains=(Chain(chain_id="A", sequence="LIVLIVLIVLFALFALFAVVLLIIVVLLAAFF"),),
    )
    profile = profile_target(greasy)
    low, high = profile.logp_window
    assert profile.gravy > 0.3
    assert low > 0
    assert high <= 1.2
    assert "amphiphilic" in profile.favour
    assert "polyether" in profile.favour


def test_hydrophilic_target_gets_a_polar_logp_window() -> None:
    profile = profile_target(get_target("glp1-7-37"))
    low, high = profile.logp_window
    assert high < 0, "a hydrophilic target wants the most polar chain available"
    assert "hydrophilic" in profile.favour


def test_the_two_peptides_get_different_windows() -> None:
    """The point of the whole module: different biologic, different chemistry."""
    insulin = profile_target(get_target("human-insulin")).logp_window
    glp1 = profile_target(get_target("glp1-7-37")).logp_window
    assert insulin != glp1


def test_a_strongly_positive_target_penalises_anionic_chemistry() -> None:
    """A polycation would complex a net-negative polymer and aggregate it."""
    lysine_rich = Biologic(
        name="polylysine-like",
        biologic_class=BiologicClass.PEPTIDE,
        chains=(Chain(chain_id="A", sequence="KKKKKKKKKKKKKKKKKKKK"),),
    )
    profile = profile_target(lysine_rich)
    assert profile.net_charge > 0
    assert "anionic" in profile.penalise
    assert "neutral" in profile.favour


def test_a_strongly_negative_target_penalises_cationic_chemistry() -> None:
    acidic = Biologic(
        name="polyglutamate-like",
        biologic_class=BiologicClass.PEPTIDE,
        chains=(Chain(chain_id="A", sequence="EEEEEEEEEEEEEEEEEEEE"),),
    )
    profile = profile_target(acidic)
    assert profile.net_charge < 0
    assert "cationic" in profile.penalise


def test_an_aromatic_rich_target_penalises_aromatic_chemistry() -> None:
    aromatic = Biologic(
        name="tryptophan-rich",
        biologic_class=BiologicClass.PEPTIDE,
        chains=(Chain(chain_id="A", sequence="WWWWWYYYYYFFFFFGGGGG"),),
    )
    profile = profile_target(aromatic)
    assert aromatic_fraction(aromatic) > 0.09
    assert "aromatic" in profile.penalise


def test_the_profile_works_on_an_arbitrary_pasted_sequence() -> None:
    """Nothing is curated: a sequence nobody has seen still gets a profile.

    This is the claim "works on any biologic" reduced to a test. An imported
    target has no PDB entry, no UniProt accession and no modifications, and
    must still come out with a usable profile and a stated rationale.
    """
    arbitrary = Biologic(
        name="unknown construct",
        biologic_class=BiologicClass.PROTEIN,
        chains=(
            Chain(chain_id="A", sequence="MKTIIALSYIFCLVFADYKDDDDKGSHMQVQLVQSGAEVKKPG"),
            Chain(chain_id="B", sequence="GGGGSGGGGSELQMTQSPSSLSASVGDRVTITCRASQSISSYL"),
        ),
    )
    profile = profile_target(arbitrary)
    assert profile.n_chains == 2
    assert profile.n_residues == 86
    assert profile.favour, "a profile must commit to some chemistry"
    assert profile.rationale, "every profile has to state its reasoning"
    assert profile.to_dict()["basis"]


def test_the_profile_round_trips_to_json_safe_primitives() -> None:
    """It is stored on the run, so it has to serialise."""
    import json

    payload = profile_target(get_target("human-insulin")).to_dict()
    assert json.loads(json.dumps(payload)) == payload


def test_charge_falls_monotonically_as_ph_rises() -> None:
    """A sanity check on the charge model itself."""
    target = get_target("human-insulin")
    charges = [net_charge_at(target, ph) for ph in (2.0, 5.0, 7.4, 10.0, 12.0)]
    assert charges == sorted(charges, reverse=True)

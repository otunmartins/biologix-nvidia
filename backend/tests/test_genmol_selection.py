"""GenMol's candidate selection: ranking, constructibility and target bias.

These cover the logic that turned a cohort of 24 candidates with 5 survivors
into one with 23, without touching a single screening threshold. None of them
calls the NIM: the selection is pure functions over SMILES, which is why it is
testable at all.
"""

from __future__ import annotations

from biologix.nvidia.genmol import (
    POLYMER_SEED_TEMPLATES,
    constructible_end_group,
    score_unit_fit,
    template_core,
    template_weight,
)
from biologix.polymers.models import EndGroup
from biologix.targets.formulation import profile_target
from biologix.targets.registry import get_target

INSULIN = profile_target(get_target("human-insulin"))
GLP1 = profile_target(get_target("glp1-7-37"))


# -- constructibility ------------------------------------------------------


def test_a_unit_whose_backbone_would_be_a_peroxide_is_refused() -> None:
    """Both attachment points on oxygen means O-O in the *backbone*.

    Polymerising this unit gives a poly(peroxide), which is an explosive
    hazard rather than an excipient. It ranked top on polarity, so without
    this gate the most attractive-looking candidates were the unsafe ones. No
    end group can rescue it, so it has to be refused outright.
    """
    cap, reason = constructible_end_group("*OCC(CO*)NC(=O)C=C")
    assert cap is None
    assert reason and "peroxide" in reason.lower()


def test_a_cap_is_chosen_to_suit_the_unit() -> None:
    """A polyester unit builds, but not with every cap.

    ``*OCC(=O)NCC(*)=O`` is a sound unit whose oxygen-attached end makes a
    peroxide against a hydroxyl cap. Choosing per unit is the fix; the old
    code applied methyl to everything and took what happened.
    """
    cap, reason = constructible_end_group("*OCC(=O)NCC(*)=O")
    assert cap is not None, reason
    assert cap != EndGroup.HYDROXYL


def test_an_ordinary_vinyl_unit_is_constructible() -> None:
    cap, reason = constructible_end_group("*CC(*)C(=O)NCCO")
    assert cap is not None, reason


# -- fit ranking -----------------------------------------------------------


def test_an_aromatic_lipophilic_unit_ranks_below_a_polar_one() -> None:
    """The failure that collapsed the cohort, as a test.

    GenMol decorates its polar seeds with aromatics, and those units fail the
    aqueous-parenteral gate on logP and aromatic fraction. Ranking has to put
    them below the units that pass, or a capped run surfaces a cohort the
    screen then throws away.
    """
    polar = score_unit_fit("*CC(*)C(=O)NC(CO)CO", ("neutral", "hydrophilic"), GLP1)
    greasy = score_unit_fit(
        "*CC(*)C(=O)NC1(c2ccccc2)CCN(C(=O)CC(=O)O)CC1", ("neutral",), GLP1
    )
    assert polar.score > greasy.score


def test_the_fit_reports_the_descriptors_it_judged_on() -> None:
    """The ranking has to be inspectable, not a bare number."""
    fit = score_unit_fit("*CC(*)c1ccccc1", ("neutral",), GLP1)
    assert fit.logp is not None
    assert fit.aromatic_fraction is not None and fit.aromatic_fraction > 0
    assert fit.reasons
    assert "fit_score" in fit.to_dict()


def test_a_unit_above_the_aromatic_ceiling_is_penalised() -> None:
    plain = score_unit_fit("*CC(*)C(N)=O", ("neutral",), None)
    aromatic = score_unit_fit("*CC(*)c1ccc2ccccc2c1", ("neutral",), None)
    assert plain.score > aromatic.score


def test_an_unparseable_unit_scores_zero_rather_than_raising() -> None:
    fit = score_unit_fit("not a smiles", ("neutral",), None)
    assert fit.score == 0.0
    assert fit.reasons == ["unparseable"]


# -- target bias -----------------------------------------------------------


def test_the_same_unit_is_scored_differently_for_different_targets() -> None:
    """Target-awareness has to change the ranking, not just the manifest.

    A mildly lipophilic amphiphile suits insulin, which has exposed non-polar
    patches, better than it suits GLP-1, which does not.
    """
    amphiphile = "*CC(*)C(=O)N1CCOCC1"
    for_insulin = score_unit_fit(amphiphile, ("neutral", "amphiphilic"), INSULIN)
    for_glp1 = score_unit_fit(amphiphile, ("neutral", "amphiphilic"), GLP1)
    assert for_insulin.score != for_glp1.score


def test_a_hydrophobic_target_prefers_the_amphiphile_over_the_polyol() -> None:
    """The ordering flips between the two targets, which is the whole point."""
    amphiphile = "*CC(*)C(=O)N1CCOCC1"
    polyol = "*CC(*)C(=O)NC(CO)CO"

    insulin_prefers_amphiphile = score_unit_fit(
        amphiphile, ("amphiphilic",), INSULIN
    ).score > score_unit_fit(polyol, ("hydrophilic",), INSULIN).score
    glp1_prefers_polyol = score_unit_fit(
        polyol, ("hydrophilic",), GLP1
    ).score > score_unit_fit(amphiphile, ("amphiphilic",), GLP1).score

    assert insulin_prefers_amphiphile
    assert glp1_prefers_polyol


def test_a_free_thiol_target_down_weights_michael_acceptor_seeds() -> None:
    """Residual acrylate would modify a free cysteine covalently."""
    from biologix.targets.models import Biologic, BiologicClass, Chain

    thiol_target = profile_target(
        Biologic(
            name="free cysteine construct",
            biologic_class=BiologicClass.PEPTIDE,
            chains=(Chain(chain_id="A", sequence="ACDEFGHIKC"),),
        )
    )
    assert "michael-acceptor" in thiol_target.penalise

    # Compared against an otherwise identical seed that is not an acceptor,
    # so the thiol penalty is isolated. Comparing against the unprofiled
    # weight would not: the favoured and penalised tags multiply together and
    # can legitimately cancel to 1.0.
    acceptor = ("neutral", "hydrophilic", "michael-acceptor")
    inert = ("neutral", "hydrophilic")
    assert template_weight(acceptor, thiol_target) < template_weight(inert, thiol_target)


def test_template_weights_stay_inside_their_bounds() -> None:
    """A seed is biased, never dropped: the profile is a prior, not a decision."""
    for _, _, tags in POLYMER_SEED_TEMPLATES:
        for profile in (INSULIN, GLP1, None):
            weight = template_weight(tags, profile)
            assert 0.25 <= weight <= 4.0


# -- seed templates --------------------------------------------------------


def test_every_seed_template_declares_a_parseable_core_and_tags() -> None:
    """A malformed seed would waste a whole NIM call, so pin the whole set."""
    for label, template, tags in POLYMER_SEED_TEMPLATES:
        assert label and template and tags
        assert "[*{" in template, f"{label} has no growth mask"
        assert len(template) <= 512, f"{label} exceeds GenMol's input limit"
        assert template_core(template) is not None, f"{label} has an unparseable core"


def test_the_seed_set_spans_more_than_one_mechanism_family() -> None:
    """One run should explore radical, ring-opening and step-growth chemistry."""
    tags = {tag for _, _, template_tags in POLYMER_SEED_TEMPLATES for tag in template_tags}
    assert {"hydrophilic", "amphiphilic", "polyether"} <= tags

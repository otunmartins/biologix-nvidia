"""Candidate ordering.

What these tests protect is the honesty of the ordering, not a particular
sort. An ordering users act on has to be auditable: the contributions must add
up to the score, the weights must be the user's, and a criterion that cannot
discriminate must say so rather than inventing a difference.
"""

from __future__ import annotations

import pytest

from biologix.ranking import (
    CRITERIA_BY_KEY,
    DEFAULT_WEIGHTS,
    normalise_weights,
    rank_candidates,
)

# Three candidates that differ on every criterion, so each one can be checked.
DESCRIPTORS = {
    "a": {"logp_per_repeat": -0.5, "polar_fraction": 0.40, "rotatable_per_repeat": 2.0},
    "b": {"logp_per_repeat": 0.5, "polar_fraction": 0.20, "rotatable_per_repeat": 1.0},
    "c": {"logp_per_repeat": 1.0, "polar_fraction": 0.10, "rotatable_per_repeat": 0.5},
}


def test_the_contributions_explain_the_score():
    """The score is auditable: it is exactly the sum of what is shown."""
    ranking = rank_candidates(DESCRIPTORS)
    for row in ranking.ranked:
        shown = sum(c.contribution for c in row.contributions)
        assert row.order_score == pytest.approx(shown, abs=1e-6)


def test_weights_sum_to_one_and_are_reported():
    ranking = rank_candidates(DESCRIPTORS)
    assert sum(ranking.weights.values()) == pytest.approx(1.0)
    assert ranking.weights == pytest.approx(DEFAULT_WEIGHTS)


def test_direction_is_respected_per_criterion():
    """Lower logP must help, not hurt; higher polarity must help."""
    ranking = rank_candidates(DESCRIPTORS, weights={"hydrophilicity": 1.0})
    assert [r.candidate_id for r in ranking.ranked] == ["a", "b", "c"]

    ranking = rank_candidates(DESCRIPTORS, weights={"polarity": 1.0})
    assert [r.candidate_id for r in ranking.ranked] == ["a", "b", "c"]

    # Flipping which criterion carries the weight must be able to flip the order.
    flipped = rank_candidates(
        {
            "a": {"logp_per_repeat": -0.5, "rotatable_per_repeat": 0.5},
            "b": {"logp_per_repeat": 1.0, "rotatable_per_repeat": 2.0},
        },
        weights={"flexibility": 1.0},
    )
    assert [r.candidate_id for r in flipped.ranked] == ["b", "a"]


def test_the_users_weights_change_the_order():
    """A weighting is the user's input, not decoration."""
    one = rank_candidates(
        {
            "x": {"logp_per_repeat": -2.0, "rotatable_per_repeat": 0.5},
            "y": {"logp_per_repeat": 0.9, "rotatable_per_repeat": 3.0},
        },
        weights={"hydrophilicity": 1.0, "flexibility": 0.0},
    )
    other = rank_candidates(
        {
            "x": {"logp_per_repeat": -2.0, "rotatable_per_repeat": 0.5},
            "y": {"logp_per_repeat": 0.9, "rotatable_per_repeat": 3.0},
        },
        weights={"hydrophilicity": 0.0, "flexibility": 1.0},
    )
    assert [r.candidate_id for r in one.ranked] == ["x", "y"]
    assert [r.candidate_id for r in other.ranked] == ["y", "x"]


def test_a_criterion_that_cannot_discriminate_says_so():
    """Identical values carry no information, and must not fake any."""
    identical = {
        "a": {"logp_per_repeat": 0.4, "polar_fraction": 0.3},
        "b": {"logp_per_repeat": 0.4, "polar_fraction": 0.3},
    }
    ranking = rank_candidates(identical, weights={"hydrophilicity": 1.0})
    assert "hydrophilicity" in ranking.degenerate
    for row in ranking.ranked:
        assert all(c.normalised == 0.5 and c.degenerate for c in row.contributions)
    # Equal inputs, equal scores.
    assert ranking.ranked[0].order_score == pytest.approx(ranking.ranked[1].order_score)


def test_diversity_is_unavailable_rather_than_guessed():
    """Without a cohort distance, the diversity criterion must not be invented;
    its weight goes to the criteria that do have values."""
    ranking = rank_candidates(DESCRIPTORS)
    assert "diversity" in ranking.degenerate
    for row in ranking.ranked:
        assert all(c.key != "diversity" for c in row.contributions)
        # The surviving criteria still carry the full weight between them.
        assert sum(c.weight for c in row.contributions) == pytest.approx(1.0)


def test_diversity_counts_when_supplied():
    ranking = rank_candidates(
        {"a": {"logp_per_repeat": 0.4}, "b": {"logp_per_repeat": 0.4}},
        weights={"diversity": 1.0},
        diversity={"a": 0.1, "b": 0.9},
    )
    assert "diversity" not in ranking.degenerate
    assert [r.candidate_id for r in ranking.ranked] == ["b", "a"]


def test_a_missing_descriptor_is_named_not_treated_as_zero():
    """A candidate whose descriptors failed must not be silently ranked last
    as though it scored zero on merit."""
    ranking = rank_candidates(
        {"has": {"logp_per_repeat": 0.1}, "lacks": {}},
        weights={"hydrophilicity": 1.0},
    )
    lacking = next(r for r in ranking.ranked if r.candidate_id == "lacks")
    assert lacking.missing == ["logp_per_repeat"]
    assert all(c.raw is None for c in lacking.contributions)


def test_ties_are_broken_deterministically():
    identical = {k: {"logp_per_repeat": 0.4} for k in ("z", "y", "x")}
    first = [r.candidate_id for r in rank_candidates(identical).ranked]
    second = [r.candidate_id for r in rank_candidates(identical).ranked]
    assert first == second == ["x", "y", "z"]


def test_the_basis_is_always_carried():
    """The ordering's meaning travels with it, so a UI cannot drop it."""
    ranking = rank_candidates(DESCRIPTORS)
    assert "not a prediction" in ranking.basis
    assert "not comparable across runs" in ranking.basis
    assert "not a prediction" in ranking.to_dict()["basis"]


def test_every_criterion_states_a_rationale_and_direction():
    for criterion in CRITERIA_BY_KEY.values():
        described = criterion.describe()
        assert described["rationale"].strip()
        assert described["direction"] in ("higher is better", "lower is better")


@pytest.mark.parametrize(
    ("weights", "message"),
    [
        ({"nonsense": 1.0}, "unknown ranking criteria"),
        ({"hydrophilicity": -1.0}, "negative weight"),
        ({"hydrophilicity": 0.0}, "above zero"),
    ],
)
def test_bad_weights_are_refused_with_a_reason(weights, message):
    with pytest.raises(ValueError, match=message):
        normalise_weights(weights)


def test_an_empty_cohort_ranks_to_nothing():
    ranking = rank_candidates({})
    assert ranking.ranked == []
    assert sum(ranking.weights.values()) == pytest.approx(1.0)

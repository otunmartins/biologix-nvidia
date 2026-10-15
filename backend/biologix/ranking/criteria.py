"""Transparent, weighted ordering of screened candidates.

What this is, and what it deliberately is not
---------------------------------------------
This module orders candidates so a user has somewhere to start reading. It is
**not** a suitability model, and there is no single number here that claims a
candidate is a good excipient. A composite "suitability score" would be the
easiest thing to build and the most misleading thing to ship: nothing in this
repository has been validated against a formulation outcome, so a 0-100 figure
would imply evidence that does not exist.

What is offered instead:

* every criterion is **named, directional and carries its rationale**, drawn
  from the same reasoning as the screening constraints;
* weights are the **user's**, not the author's, and travel with the result;
* each candidate reports its **per-criterion contribution**, so an ordering can
  be audited row by row and a user can see exactly why one row sits above
  another;
* the aggregate is called an ``order_score`` and is documented as a sorting
  key, so it is never mistaken for a prediction.

The relativity that matters
---------------------------
Criteria are normalised **within the cohort being ranked** (min-max over the
candidates in this run). That makes the ordering readable — the best flexibility
in this run scores 1.0 — but it also means a candidate's score is *relative to
the run it was generated in* and is not comparable across runs. That is stated
in :data:`BASIS_NOTE` and returned with every ranking, because a number that
silently changes meaning between screens is worse than no number.

A cohort where every candidate has the same value for a criterion carries no
information for that criterion; rather than divide by zero or pretend to
discriminate, every candidate is given 0.5 and the criterion is reported as
``degenerate``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BASIS_NOTE = (
    "An ordering, not a prediction. Criteria are normalised within this run, so "
    "a candidate's order_score describes where it sits among the candidates "
    "generated alongside it and is not comparable across runs. Nothing here has "
    "been validated against a formulation, stability or clinical outcome."
)


@dataclass(frozen=True)
class Criterion:
    """One directional, weighted reason to prefer a candidate."""

    key: str
    label: str
    descriptor: str
    higher_is_better: bool
    rationale: str
    default_weight: float

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "descriptor": self.descriptor,
            "direction": "higher is better" if self.higher_is_better else "lower is better",
            "rationale": self.rationale,
            "default_weight": self.default_weight,
        }


# The rationales deliberately echo screening/filters.py: a constraint says
# "this is disqualifying", a criterion says "among the survivors, prefer this".
# Keeping the reasoning identical stops the two from quietly disagreeing.
CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        key="hydrophilicity",
        label="Hydrophilicity",
        descriptor="logp_per_repeat",
        higher_is_better=False,
        rationale=(
            "An excipient for an aqueous parenteral formulation has to stay in "
            "solution. A lower logP per repeat unit is preferred; strongly "
            "lipophilic units adsorb at interfaces instead of remaining soluble."
        ),
        default_weight=0.30,
    ),
    Criterion(
        key="polarity",
        label="Polarity density",
        descriptor="polar_fraction",
        higher_is_better=True,
        rationale=(
            "Polar surface distributed along the chain is what makes a polymer "
            "water-soluble and able to hydrogen-bond to a protein surface."
        ),
        default_weight=0.20,
    ),
    Criterion(
        key="flexibility",
        label="Backbone flexibility",
        descriptor="rotatable_per_repeat",
        higher_is_better=True,
        rationale=(
            "A flexible backbone can wrap a protein surface and shield "
            "aggregation-prone patches; a rigid chain cannot conform to it."
        ),
        default_weight=0.18,
    ),
    Criterion(
        key="synthesizability",
        label="Ease of synthesis",
        descriptor="_synthesis_sa",
        higher_is_better=False,
        rationale=(
            "A candidate nobody can make is not a candidate. This is Ertl's "
            "synthetic accessibility score for the *monomer* the route needs — "
            "lower is easier — which matters doubly for an excipient, since it "
            "has to be made at formulation scale and to parenteral purity. It "
            "says nothing about whether the polymer works."
        ),
        default_weight=0.15,
    ),
    Criterion(
        key="diversity",
        label="Distance from the rest",
        descriptor="_diversity",
        higher_is_better=True,
        rationale=(
            "Prefers candidates unlike the others in the run, so a shortlist "
            "explores several chemistries rather than ten variations of one. "
            "This is computed from fingerprint distance to the cohort, not from "
            "a descriptor of the molecule itself."
        ),
        default_weight=0.17,
    ),
)

CRITERIA_BY_KEY = {c.key: c for c in CRITERIA}
DEFAULT_WEIGHTS: dict[str, float] = {c.key: c.default_weight for c in CRITERIA}


@dataclass
class Contribution:
    """One criterion's effect on one candidate's position."""

    key: str
    raw: float | None
    normalised: float | None
    weight: float
    contribution: float
    degenerate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "raw": self.raw,
            "normalised": self.normalised,
            "weight": self.weight,
            "contribution": self.contribution,
            "degenerate": self.degenerate,
        }


@dataclass
class RankedCandidate:
    candidate_id: str
    order_score: float
    contributions: list[Contribution] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "order_score": round(self.order_score, 4),
            "contributions": [c.to_dict() for c in self.contributions],
            "missing_descriptors": self.missing,
        }


@dataclass
class Ranking:
    ranked: list[RankedCandidate]
    weights: dict[str, float]
    criteria: list[dict[str, Any]]
    degenerate: list[str]
    basis: str = BASIS_NOTE

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranked": [r.to_dict() for r in self.ranked],
            "weights": self.weights,
            "criteria": self.criteria,
            "degenerate_criteria": self.degenerate,
            "basis": self.basis,
        }


def normalise_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Validate weights and scale them to sum to 1.

    Weights arrive from a user interface, so every failure mode here is
    reachable: unknown keys, negatives, and all-zero. Each is rejected with a
    message rather than silently repaired, except the final scaling.
    """
    if weights is None:
        return dict(DEFAULT_WEIGHTS)

    unknown = sorted(set(weights) - set(CRITERIA_BY_KEY))
    if unknown:
        raise ValueError(
            f"unknown ranking criteria: {', '.join(unknown)}. "
            f"Known criteria: {', '.join(CRITERIA_BY_KEY)}"
        )
    negative = sorted(k for k, v in weights.items() if v < 0)
    if negative:
        raise ValueError(
            f"negative weight for {', '.join(negative)}; a criterion's direction is "
            "fixed by its definition, so flip the criterion rather than the weight"
        )

    chosen = {k: float(weights.get(k, 0.0)) for k in CRITERIA_BY_KEY}
    total = sum(chosen.values())
    if total <= 0:
        raise ValueError("at least one criterion must carry a weight above zero")
    return {k: v / total for k, v in chosen.items()}


def _diversity_values(
    descriptors: dict[str, dict[str, Any]],
    diversity: dict[str, float] | None,
) -> dict[str, float | None]:
    """Per-candidate distance from the cohort, if it was supplied."""
    if diversity:
        return {cid: diversity.get(cid) for cid in descriptors}
    return {cid: None for cid in descriptors}


def _external_values(
    descriptors: dict[str, dict[str, Any]],
    supplied: dict[str, float] | None,
) -> dict[str, float | None]:
    """A per-candidate figure that is not a descriptor of the molecule.

    Synthetic accessibility belongs to the *monomer* a route needs, not to the
    repeat unit, so it arrives alongside the descriptors rather than inside
    them. A candidate with no route on record gets no value and the criterion
    contributes nothing for it, rather than a guess.
    """
    if supplied:
        return {cid: supplied.get(cid) for cid in descriptors}
    return {cid: None for cid in descriptors}


def rank_candidates(
    descriptors: dict[str, dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
    diversity: dict[str, float] | None = None,
    synthesis: dict[str, float] | None = None,
) -> Ranking:
    """Order candidates by weighted, cohort-normalised criteria.

    ``descriptors`` maps candidate id to that candidate's descriptor values.
    ``diversity`` optionally maps candidate id to its mean fingerprint distance
    from the rest of the cohort, and ``synthesis`` to its route's synthetic
    accessibility score. Without either, that criterion is reported as
    unavailable rather than guessed at, and its weight is redistributed over
    the criteria that *are* available.
    """
    resolved = normalise_weights(weights)
    diversity_by_id = _diversity_values(descriptors, diversity)
    synthesis_by_id = _external_values(descriptors, synthesis)

    def value_of(candidate_id: str, criterion: Criterion) -> float | None:
        if criterion.descriptor == "_diversity":
            return diversity_by_id.get(candidate_id)
        if criterion.descriptor == "_synthesis_sa":
            return synthesis_by_id.get(candidate_id)
        raw = descriptors.get(candidate_id, {}).get(criterion.descriptor)
        return float(raw) if isinstance(raw, (int, float)) else None

    # A criterion nobody has a value for cannot contribute; its weight is
    # redistributed rather than silently shrinking every score toward zero.
    usable: list[Criterion] = []
    degenerate: list[str] = []
    spans: dict[str, tuple[float, float]] = {}
    for criterion in CRITERIA:
        if resolved.get(criterion.key, 0.0) <= 0:
            continue
        values = [v for cid in descriptors if (v := value_of(cid, criterion)) is not None]
        if not values:
            degenerate.append(criterion.key)
            continue
        low, high = min(values), max(values)
        spans[criterion.key] = (low, high)
        if high - low < 1e-12:
            degenerate.append(criterion.key)
        usable.append(criterion)

    live_total = sum(resolved[c.key] for c in usable)
    effective = (
        {c.key: resolved[c.key] / live_total for c in usable} if live_total > 0 else {}
    )

    ranked: list[RankedCandidate] = []
    for candidate_id in descriptors:
        contributions: list[Contribution] = []
        missing: list[str] = []
        score = 0.0
        for criterion in usable:
            raw = value_of(candidate_id, criterion)
            weight = effective[criterion.key]
            if raw is None:
                missing.append(criterion.descriptor)
                contributions.append(
                    Contribution(criterion.key, None, None, weight, 0.0)
                )
                continue
            low, high = spans[criterion.key]
            if high - low < 1e-12:
                # No information in this criterion for this cohort.
                normalised = 0.5
                is_degenerate = True
            else:
                normalised = (raw - low) / (high - low)
                if not criterion.higher_is_better:
                    normalised = 1.0 - normalised
                is_degenerate = False
            # Rounded before it is summed, not after: the figures a user reads
            # must be the figures that add up to the score they are reading.
            contribution = round(normalised * weight, 4)
            score += contribution
            contributions.append(
                Contribution(
                    criterion.key,
                    round(raw, 4),
                    round(normalised, 4),
                    round(weight, 4),
                    contribution,
                    is_degenerate,
                )
            )
        ranked.append(RankedCandidate(candidate_id, score, contributions, missing))

    # Ties broken by candidate id, so the same inputs always give the same order.
    ranked.sort(key=lambda r: (-r.order_score, r.candidate_id))
    return Ranking(
        ranked=ranked,
        weights=resolved,
        criteria=[c.describe() for c in CRITERIA],
        degenerate=sorted(set(degenerate)),
    )


__all__ = [
    "BASIS_NOTE",
    "CRITERIA",
    "CRITERIA_BY_KEY",
    "DEFAULT_WEIGHTS",
    "Criterion",
    "Ranking",
    "RankedCandidate",
    "normalise_weights",
    "rank_candidates",
]

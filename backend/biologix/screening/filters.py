"""Configurable chemistry and manufacturability constraints.

Every constraint states its own threshold and rationale, and a failure names
the constraint. Nothing here is a safety assessment: see
:class:`ResidualMonomerFlag`, which is explicitly scoped to the *monomer* and
says so, because monomer toxicology does not transfer to the polymer and
polymer safety does not follow from monomer safety either.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdkit import Chem

from biologix.polymers.library import regulatory_context
from biologix.polymers.models import PolymerCandidate
from biologix.screening.descriptors import DescriptorSet


@dataclass(frozen=True)
class Constraint:
    """One numeric constraint on a descriptor."""

    descriptor: str
    minimum: float | None = None
    maximum: float | None = None
    rationale: str = ""
    # A soft constraint records a warning instead of excluding the candidate.
    hard: bool = True

    def check(self, value: float) -> tuple[bool, str | None]:
        if self.minimum is not None and value < self.minimum:
            return False, (
                f"{self.descriptor}={value:.4g} below minimum {self.minimum:g}"
                + (f" ({self.rationale})" if self.rationale else "")
            )
        if self.maximum is not None and value > self.maximum:
            return False, (
                f"{self.descriptor}={value:.4g} above maximum {self.maximum:g}"
                + (f" ({self.rationale})" if self.rationale else "")
            )
        return True, None

    def describe(self) -> dict[str, object]:
        return {
            "descriptor": self.descriptor,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "rationale": self.rationale,
            "hard": self.hard,
        }


@dataclass(frozen=True)
class ConstraintSet:
    """A named, inspectable collection of constraints.

    The default set encodes choices a formulation scientist should be able to
    see and change, which is why every threshold carries a rationale rather
    than being a bare magic number.
    """

    name: str
    constraints: tuple[Constraint, ...]
    description: str = ""

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "constraints": [c.describe() for c in self.constraints],
        }


DEFAULT_CONSTRAINTS = ConstraintSet(
    name="aqueous-parenteral-default",
    description=(
        "Starting constraints for a water-soluble, non-reactive excipient "
        "intended for an aqueous parenteral formulation. These are editable "
        "heuristics, not regulatory requirements."
    ),
    constraints=(
        Constraint(
            descriptor="logp_per_repeat",
            maximum=1.2,
            rationale="strongly lipophilic repeat units tend to be poorly water soluble "
            "and to adsorb at interfaces",
        ),
        Constraint(
            descriptor="logp_per_repeat",
            minimum=-3.0,
            rationale="extremely hydrophilic units may not interact with the protein at all",
            hard=False,
        ),
        Constraint(
            descriptor="polar_fraction",
            minimum=0.08,
            rationale="some polarity density is needed for aqueous solubility",
        ),
        Constraint(
            descriptor="aromatic_fraction",
            maximum=0.35,
            rationale="extended aromatic content is associated with poor solubility "
            "and with pi-stacking against aromatic protein residues",
        ),
        Constraint(
            descriptor="rotatable_per_repeat",
            minimum=0.5,
            rationale="a flexible backbone is needed to wrap a protein surface",
            hard=False,
        ),
        Constraint(
            descriptor="repeat_mw",
            minimum=30.0,
            maximum=400.0,
            rationale="keeps the repeat unit in a range where polymerisation chemistry "
            "is plausible",
        ),
        Constraint(
            descriptor="charge_per_repeat",
            minimum=-1.0,
            maximum=1.0,
            rationale="more than one charge per repeat unit gives a polyelectrolyte that "
            "is likely to drive precipitation",
        ),
    ),
)

# A permissive set for exploratory runs, where the researcher wants to see
# what the generator produces before narrowing.
EXPLORATORY_CONSTRAINTS = ConstraintSet(
    name="exploratory",
    description="Minimal constraints: only excludes candidates that cannot be built.",
    constraints=(),
)

CONSTRAINT_SETS: dict[str, ConstraintSet] = {
    DEFAULT_CONSTRAINTS.name: DEFAULT_CONSTRAINTS,
    EXPLORATORY_CONSTRAINTS.name: EXPLORATORY_CONSTRAINTS,
}


# Monomers whose residual presence is a recognised concern. The flag is about
# the free monomer that may remain after polymerisation, which is a different
# molecule from the polymer.
_RESIDUAL_MONOMER_ALERTS: tuple[tuple[str, str, str], ...] = (
    (
        "acrylamide-type monomer",
        "[CX3]=[CX3][CX3](=O)[NX3]",
        "free acrylamide and many N-substituted acrylamides are classified as "
        "hazardous; residual monomer specification and testing would be required",
    ),
    (
        "acrylate/methacrylate ester monomer",
        "[CX3]=[CX3][CX3](=O)[OX2]",
        "free acrylate esters are sensitisers and irritants",
    ),
    (
        "vinyl amide monomer",
        "[CX3]=[CX3][NX3]",
        "N-vinyl monomers such as N-vinylpyrrolidone carry residual-monomer limits",
    ),
    (
        "epoxide monomer",
        "[OX2r3]1[#6r3][#6r3]1",
        "ethylene and propylene oxide are volatile genotoxic monomers with strict limits",
    ),
    (
        "vinyl ester monomer",
        "[CX3]=[CX3][OX2][CX3]=O",
        "vinyl esters hydrolyse and the free monomer is an irritant",
    ),
)


@dataclass
class ResidualMonomerFlag:
    """A flag about the *monomer*, deliberately not about the polymer."""

    monomer_class: str
    concern: str
    scope: str = "residual free monomer only"
    does_not_imply: str = (
        "This is not an assessment of the polymer. It says nothing about whether "
        "the polymer is safe, and the polymer being acceptable would not remove "
        "the need for a residual-monomer specification."
    )

    def to_dict(self) -> dict[str, str]:
        return {
            "monomer_class": self.monomer_class,
            "concern": self.concern,
            "scope": self.scope,
            "does_not_imply": self.does_not_imply,
        }


@dataclass
class FilterOutcome:
    """Result of applying a constraint set to one candidate."""

    candidate_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    residual_monomer_flags: list[ResidualMonomerFlag] = field(default_factory=list)
    regulatory_context: list[dict[str, object]] = field(default_factory=list)
    constraint_set: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "passed": self.passed,
            "failures": self.failures,
            "warnings": self.warnings,
            "residual_monomer_flags": [f.to_dict() for f in self.residual_monomer_flags],
            "regulatory_context": self.regulatory_context,
            "constraint_set": self.constraint_set,
            "safety_assessment": (
                "none performed — this prototype does not assess polymer safety"
            ),
        }


def residual_monomer_flags(candidate: PolymerCandidate) -> list[ResidualMonomerFlag]:
    """Flags for the free monomers implied by this candidate's repeat units.

    The repeat unit is converted back to a plausible monomer by replacing its
    two attachment points with a double bond between the attachment
    neighbours, which is the common vinyl-polymerisation case. Ring-opening
    chemistries are detected by matching the repeat unit itself.
    """
    flags: list[ResidualMonomerFlag] = []
    seen: set[str] = set()
    for block in candidate.blocks:
        probes = [block.unit.smiles]
        monomer = _implied_vinyl_monomer(block.unit.smiles)
        if monomer:
            probes.append(monomer)
        for label, smarts, concern in _RESIDUAL_MONOMER_ALERTS:
            if label in seen:
                continue
            patt = Chem.MolFromSmarts(smarts)
            if patt is None:
                continue
            for probe in probes:
                mol = Chem.MolFromSmiles(probe)
                if mol is not None and mol.HasSubstructMatch(patt):
                    flags.append(ResidualMonomerFlag(monomer_class=label, concern=concern))
                    seen.add(label)
                    break
    return flags


def _implied_vinyl_monomer(repeat_smiles: str) -> str | None:
    """Reconstruct the vinyl monomer a repeat unit would have come from.

    Replaces the two attachment points with a C=C between their neighbours.
    Returns ``None`` when the repeat unit is not a vinyl-type backbone.
    """
    mol = Chem.MolFromSmiles(repeat_smiles, sanitize=False)
    if mol is None:
        return None
    dummies = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if len(dummies) != 2:
        return None
    rw = Chem.RWMol(mol)
    neighbours = []
    for d in dummies:
        nbrs = list(rw.GetAtomWithIdx(d).GetNeighbors())
        if len(nbrs) != 1:
            return None
        neighbours.append(nbrs[0].GetIdx())
    a, b = neighbours
    if a == b:
        return None
    bond = rw.GetBondBetweenAtoms(a, b)
    if bond is None:
        # Not adjacent: a ring-opening or condensation unit, not vinyl.
        return None
    bond.SetBondType(Chem.BondType.DOUBLE)
    for idx in sorted(dummies, reverse=True):
        rw.RemoveAtom(idx)
    out = rw.GetMol()
    try:
        Chem.SanitizeMol(out)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        return None
    return Chem.MolToSmiles(out)


#: Route confidences that clear the synthesis gate. "speculative" is kept out
#: deliberately: a mechanism that *might* apply, with a stated reason to doubt
#: it, is not something to put on a shortlist as if it were makeable.
DEFENSIBLE_CONFIDENCE = ("established", "plausible")

#: Above this, Ertl's score says the monomer is a bespoke multi-step synthesis.
#: A soft limit: expensive is a reason to think twice, not a disqualification.
SA_SOFT_LIMIT = 6.0


def apply_synthesis_gate(
    outcome: FilterOutcome, synthesis: dict[str, object] | None, *, enforce: bool = True
) -> FilterOutcome:
    """Fail a candidate whose synthesis route does not hold up.

    This is the gate that stops the most embarrassing failure mode of a
    generative pipeline: a shortlist of molecules that cannot be made. It reads
    the route assessed in :mod:`biologix.polymers.synthesis`, so the chemistry
    argument lives in one place and this is only the decision.

    A candidate with no route on record is *warned about*, not failed: absence
    of an assessment is not evidence of a bad route, and saying so is more
    honest than inventing a verdict.
    """
    if synthesis is None:
        outcome.warnings.append(
            "no synthesis route on record for this candidate, so its makeability has "
            "not been assessed"
        )
        return outcome

    route = synthesis.get("route") if isinstance(synthesis, dict) else None
    if not isinstance(route, dict):
        outcome.warnings.append("the stored synthesis route could not be read")
        return outcome

    confidence = str(route.get("confidence", "unknown"))
    reasoning = str(route.get("reasoning", "")).strip()
    if confidence not in DEFENSIBLE_CONFIDENCE:
        message = (
            f"no defensible synthesis route: the {route.get('mechanism', 'mechanism')} "
            f"reading is {confidence}"
            + (f" — {reasoning}" if reasoning else "")
        )
        if enforce:
            outcome.passed = False
            outcome.failures.append(message)
        else:
            outcome.warnings.append(message)
        return outcome

    sa = route.get("sa_score")
    if isinstance(sa, (int, float)) and sa >= SA_SOFT_LIMIT:
        outcome.warnings.append(
            f"the monomer's synthetic accessibility score is {sa}/10, so making it is "
            "likely a bespoke multi-step synthesis — costly at excipient scale"
        )
    for concern in route.get("concerns", []) or []:
        outcome.warnings.append(f"synthesis: {concern}")
    return outcome


def apply_filters(
    candidate: PolymerCandidate,
    descriptors: DescriptorSet,
    constraint_set: ConstraintSet = DEFAULT_CONSTRAINTS,
) -> FilterOutcome:
    """Apply ``constraint_set`` to one candidate's descriptors."""
    outcome = FilterOutcome(
        candidate_id=candidate.candidate_id,
        passed=True,
        constraint_set=constraint_set.name,
    )

    if not descriptors.ok:
        outcome.passed = False
        outcome.failures.append(descriptors.error or "descriptors unavailable")
        return outcome

    for constraint in constraint_set.constraints:
        if constraint.descriptor not in descriptors.values:
            outcome.warnings.append(
                f"constraint references unknown descriptor {constraint.descriptor!r}; skipped"
            )
            continue
        ok, message = constraint.check(descriptors.values[constraint.descriptor])
        if ok:
            continue
        if constraint.hard:
            outcome.passed = False
            outcome.failures.append(message or "constraint failed")
        else:
            outcome.warnings.append(message or "soft constraint failed")

    outcome.residual_monomer_flags = residual_monomer_flags(candidate)
    outcome.regulatory_context = [
        {"repeat_unit": b.unit.smiles, **regulatory_context(b.unit.smiles)}
        for b in candidate.blocks
    ]
    return outcome


def get_constraint_set(name: str) -> ConstraintSet:
    try:
        return CONSTRAINT_SETS[name]
    except KeyError:
        raise KeyError(
            f"unknown constraint set {name!r}; available: {', '.join(sorted(CONSTRAINT_SETS))}"
        ) from None


def register_constraint_set(cs: ConstraintSet) -> None:
    CONSTRAINT_SETS[cs.name] = cs

"""From a generated molecule to a defensible repeat unit.

One function, used by every learned generator, so GenMol and MolMIM cannot
drift apart in how they treat what they produce. It composes the three pieces
that already exist and adds the judgement that was missing:

1. :mod:`monomer_to_repeat` finds bonds a named mechanism could open. This is
   a structural search and will happily propose nonsense.
2. :mod:`synthesis` then asks whether that mechanism really works on the
   monomer it implies, and **drops the routes that cannot**. This is the step
   that stops an oxime pair being called a diol, or a 1,2-dialkyl alkene being
   called a vinyl monomer.
3. :mod:`grafting` rescues the rest. A molecule with no polymerisable handle
   still has chemistry worth keeping, so it is hung off an acrylate or
   acrylamide backbone — which is how functional polymers are actually made —
   and the derivation is recorded as a graft rather than passed off as a
   found monomer.

The yield figures this produces are the honest ones for a learned generator:
of the molecules it proposed, how many gave a repeat unit whose synthesis can
be *defended*, by which route, and why the others could not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biologix.polymers.grafting import graft_molecule
from biologix.polymers.monomer_to_repeat import convert_molecule
from biologix.polymers.synthesis import SynthesisRoute, assess_route


@dataclass
class DerivedUnit:
    """One repeat unit, with the route that defends it."""

    repeat_unit: str
    mechanism: str
    route: SynthesisRoute
    derivation: str  # direct | graft
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "repeat_unit": self.repeat_unit,
            "mechanism": self.mechanism,
            "derivation": self.derivation,
            "label": self.label,
            "route": self.route.to_dict(),
        }


@dataclass
class DerivationResult:
    """Everything that happened to one generated molecule."""

    input_smiles: str
    units: list[DerivedUnit] = field(default_factory=list)
    #: Routes found structurally but refused on chemistry, kept because
    #: "this looks polymerisable but is not, and here is why" is the most
    #: useful thing a generator run can tell a chemist.
    refused: list[SynthesisRoute] = field(default_factory=list)
    graft_refusal: str | None = None
    graft_refusal_code: str | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.units)

    def rejection_codes(self) -> list[str]:
        if self.error:
            return ["unparseable"]
        if self.usable:
            return []
        if self.refused:
            return ["mechanism_not_feasible"]
        return [self.graft_refusal_code or "not_polymerisable"]

    def rejection_detail(self) -> str:
        if self.error:
            return self.error
        if self.usable:
            return ""
        parts: list[str] = []
        if self.refused:
            # Name the mechanism that looked right and the chemistry that rules
            # it out, rather than a bare "not polymerisable".
            first = self.refused[0]
            parts.append(
                f"a {first.mechanism} site was found but the chemistry does not hold: "
                f"{first.reasoning}"
            )
        if self.graft_refusal:
            parts.append(
                f"grafting onto a polymerisable backbone also failed: {self.graft_refusal}"
            )
        return " ".join(parts) or "no defensible polymerisation route"

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_smiles": self.input_smiles,
            "usable": self.usable,
            "units": [u.to_dict() for u in self.units],
            "refused": [r.to_dict() for r in self.refused],
            "graft_refusal": self.graft_refusal,
            "graft_refusal_code": self.graft_refusal_code,
            "error": self.error,
        }


def derive_repeat_units(
    smiles: str,
    *,
    allow_grafting: bool = True,
    require_defensible: bool = True,
    max_units: int = 3,
) -> DerivationResult:
    """Every defensible repeat unit a molecule yields, with its route.

    ``require_defensible`` drops routes whose chemistry does not hold. Turning
    it off keeps them, which is useful for studying a generator's output but
    must not be the default: a candidate nobody can make is not a candidate.
    """
    conversion = convert_molecule(smiles)
    if conversion.error:
        return DerivationResult(input_smiles=smiles, error=conversion.error)

    canonical_input = conversion.canonical_input or smiles
    result = DerivationResult(input_smiles=canonical_input)

    for unit_smiles, mechanism in conversion.repeat_units:
        route = assess_route(unit_smiles, mechanism)
        if require_defensible and not route.defensible:
            result.refused.append(route)
            continue
        result.units.append(
            DerivedUnit(
                repeat_unit=unit_smiles,
                mechanism=str(mechanism),
                route=route,
                derivation="direct",
                label=str(mechanism),
            )
        )
        if len(result.units) >= max_units:
            return result

    if result.units or not allow_grafting:
        return result

    # Nothing direct held up. Keep the pendant chemistry by building a monomer
    # around it instead of discarding the molecule.
    graft = graft_molecule(canonical_input)
    if not graft.grafted:
        result.graft_refusal = graft.refusal
        result.graft_refusal_code = graft.refusal_code
        return result

    for graft_route in graft.routes:
        result.units.append(
            DerivedUnit(
                repeat_unit=graft_route.repeat_unit,
                mechanism="chain_growth_vinyl",
                route=graft_route.route,
                derivation="graft",
                label=f"graft:{graft_route.backbone}",
            )
        )
        if len(result.units) >= max_units:
            break
    return result


def derivation_statistics(results: list[DerivationResult]) -> dict[str, Any]:
    """Yield figures for a learned generator, stated as what they measure."""
    n = len(results)
    usable = sum(1 for r in results if r.usable)
    direct = sum(1 for r in results if any(u.derivation == "direct" for u in r.units))
    grafted = sum(1 for r in results if r.units and all(u.derivation == "graft" for u in r.units))
    refused_on_chemistry = sum(1 for r in results if not r.usable and r.refused)
    by_confidence: dict[str, int] = {}
    by_mechanism: dict[str, int] = {}
    for r in results:
        for unit in r.units:
            by_confidence[unit.route.confidence] = by_confidence.get(unit.route.confidence, 0) + 1
            by_mechanism[unit.label] = by_mechanism.get(unit.label, 0) + 1

    return {
        "n_molecules": n,
        "n_usable": usable,
        "n_direct": direct,
        "n_graft_only": grafted,
        "n_refused_on_chemistry": refused_on_chemistry,
        "defensible_rate": round(usable / n, 4) if n else 0.0,
        "by_confidence": dict(sorted(by_confidence.items(), key=lambda kv: -kv[1])),
        "by_route": dict(sorted(by_mechanism.items(), key=lambda kv: -kv[1])),
        "note": (
            "defensible_rate is the fraction of generated molecules that yielded a "
            "repeat unit whose synthesis route holds up — a named mechanism acting on "
            "a monomer that really polymerises that way, or a one-step graft onto a "
            "backbone that does. Molecules counted under n_refused_on_chemistry had a "
            "site that looked polymerisable and was not; that count is the useful "
            "signal about what the generator is doing wrong."
        ),
    }


__all__ = [
    "DerivationResult",
    "DerivedUnit",
    "derivation_statistics",
    "derive_repeat_units",
]

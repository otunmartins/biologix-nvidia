"""Polymerisation rules: is this repeat unit actually polymerisable?

A valid small molecule is not a valid polymer repeat unit. A repeat unit needs
attachment points in the right number and position, a backbone that can
propagate, correct valence once connected, and chemistry we are willing to put
in a drug formulation. Every rejection carries a machine-readable reason code
so a generator's yield can be audited instead of guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors

from biologix.polymers.models import PolymerCandidate, RepeatUnit

# RDKit writes parse failures to stderr; we capture them as structured
# reasons instead, so silence the global logger.
RDLogger.DisableLog("rdApp.*")


class RejectionCode(StrEnum):
    """Why a candidate repeat unit was rejected."""

    UNPARSEABLE = "unparseable"
    NO_ATTACHMENT_POINTS = "no_attachment_points"
    WRONG_ATTACHMENT_COUNT = "wrong_attachment_count"
    ATTACHMENT_NOT_TERMINAL = "attachment_not_terminal"
    ATTACHMENT_SAME_ATOM = "attachment_on_same_atom"
    ATTACHMENT_ON_HETEROATOM_RING = "attachment_in_ring"
    DISALLOWED_ELEMENT = "disallowed_element"
    BAD_VALENCE = "bad_valence_when_connected"
    NO_BACKBONE_PATH = "no_backbone_path_between_attachments"
    TOO_SMALL = "repeat_unit_too_small"
    TOO_LARGE = "repeat_unit_too_large"
    CHARGED = "net_charge_not_allowed"
    REACTIVE_GROUP = "reactive_or_unstable_group"
    RADICAL = "unpaired_electrons"
    ISOTOPE = "isotope_label_present"
    ARCHITECTURE_NOT_BUILDABLE = "architecture_not_buildable"
    SANITIZATION_FAILED = "sanitization_failed"


@dataclass(frozen=True)
class PolymerizationRules:
    """Configurable constraints applied before any expensive calculation."""

    # Elements permitted in an excipient repeat unit.
    allowed_elements: frozenset[str] = frozenset({"C", "H", "N", "O", "S", "P", "F", "Si"})
    required_attachment_points: int = 2
    min_heavy_atoms: int = 2
    max_heavy_atoms: int = 60
    min_repeat_mw: float = 26.0
    max_repeat_mw: float = 800.0
    allow_net_charge: bool = True
    max_abs_charge: int = 2
    allow_rings_in_backbone: bool = True
    # Require a bonded path between the two attachment points, which is what
    # makes the unit propagate a backbone rather than dangle.
    require_backbone_path: bool = True
    forbid_isotopes: bool = True

    # SMARTS for groups we refuse in a formulation excipient: they hydrolyse,
    # react with protein nucleophiles, or are known sensitisers.
    reactive_smarts: tuple[tuple[str, str], ...] = (
        ("acyl halide", "[CX3](=O)[F,Cl,Br,I]"),
        ("sulfonyl halide", "[SX4](=O)(=O)[F,Cl,Br,I]"),
        ("isocyanate", "[NX2]=[CX2]=[OX1]"),
        ("isothiocyanate", "[NX2]=[CX2]=[SX1]"),
        ("epoxide", "[OX2r3]1[#6r3][#6r3]1"),
        ("aziridine", "[NX3r3]1[#6r3][#6r3]1"),
        ("azide", "[NX2]=[NX2+0]=[NX1-,NX2]"),
        ("diazo", "[CX3]=[NX2+]=[NX1-]"),
        ("peroxide", "[OX2][OX2]"),
        ("michael acceptor aldehyde", "[CX3H1](=O)[CX3]=[CX3]"),
        ("acrylate ester", "[CX3](=O)[OX2][CX3]=[CX3]"),
        ("maleimide", "O=C1[CX3]=[CX3]C(=O)N1"),
        ("alkyl halide", "[CX4][F,Cl,Br,I]"),
        ("anhydride", "[CX3](=O)[OX2][CX3]=O"),
        ("thiol", "[SX2H1]"),
        ("aldehyde", "[CX3H1](=O)[#6]"),
    )
    # Groups allowed but worth flagging to the researcher.
    caution_smarts: tuple[tuple[str, str], ...] = (
        ("ester (hydrolysable)", "[CX3](=O)[OX2H0][#6]"),
        ("primary amine", "[NX3;H2][CX4]"),
        ("quaternary ammonium", "[NX4+]"),
        ("aromatic nitro", "[$([NX3](=O)=O),$([NX3+](=O)[O-])][c]"),
    )


@dataclass
class ValidationResult:
    """Outcome of validating one repeat unit."""

    valid: bool
    smiles: str
    reasons: list[tuple[RejectionCode, str]] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    canonical_smiles: str | None = None
    n_attachment_points: int = 0
    heavy_atoms: int = 0
    repeat_mw: float = 0.0
    net_charge: int = 0

    @property
    def reason_codes(self) -> list[str]:
        return [str(c) for c, _ in self.reasons]

    @property
    def summary(self) -> str:
        if self.valid:
            return "valid"
        return "; ".join(f"{code}: {detail}" for code, detail in self.reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "smiles": self.smiles,
            "canonical_smiles": self.canonical_smiles,
            "reasons": [{"code": str(c), "detail": d} for c, d in self.reasons],
            "cautions": self.cautions,
            "n_attachment_points": self.n_attachment_points,
            "heavy_atoms": self.heavy_atoms,
            "repeat_mw": round(self.repeat_mw, 3),
            "net_charge": self.net_charge,
        }


def parse_repeat_unit(smiles: str) -> Chem.Mol | None:
    """Parse repeat-unit SMILES without sanitising, so we can inspect dummies.

    Sanitisation is attempted separately and its failure is itself a reason
    code, which is more useful than a bare ``None``.
    """
    return Chem.MolFromSmiles(smiles, sanitize=False)


def attachment_atoms(mol: Chem.Mol) -> list[Chem.Atom]:
    """Dummy atoms (atomic number 0) that act as attachment points."""
    return [a for a in mol.GetAtoms() if a.GetAtomicNum() == 0]


def validate_repeat_unit(
    smiles: str, rules: PolymerizationRules | None = None
) -> ValidationResult:
    """Apply every rule and collect *all* failures, not just the first.

    Collecting all of them means a generator's rejection statistics show the
    real distribution of failure modes.
    """
    rules = rules or PolymerizationRules()
    result = ValidationResult(valid=False, smiles=smiles)

    mol = parse_repeat_unit(smiles)
    if mol is None:
        result.reasons.append((RejectionCode.UNPARSEABLE, "RDKit could not parse the SMILES"))
        return result

    dummies = attachment_atoms(mol)
    result.n_attachment_points = len(dummies)

    if not dummies:
        result.reasons.append(
            (
                RejectionCode.NO_ATTACHMENT_POINTS,
                "no [*] dummy atoms; a molecule without attachment points cannot "
                "propagate a chain",
            )
        )
    elif len(dummies) != rules.required_attachment_points:
        result.reasons.append(
            (
                RejectionCode.WRONG_ATTACHMENT_COUNT,
                f"found {len(dummies)} attachment points, need exactly "
                f"{rules.required_attachment_points}",
            )
        )

    # Each attachment point must be a terminal monovalent stub.
    neighbours: list[int] = []
    for d in dummies:
        nbrs = list(d.GetNeighbors())
        if len(nbrs) != 1:
            result.reasons.append(
                (
                    RejectionCode.ATTACHMENT_NOT_TERMINAL,
                    f"attachment atom {d.GetIdx()} has {len(nbrs)} neighbours; "
                    "it must have exactly one",
                )
            )
            continue
        if d.IsInRing():
            result.reasons.append(
                (
                    RejectionCode.ATTACHMENT_ON_HETEROATOM_RING,
                    f"attachment atom {d.GetIdx()} is inside a ring",
                )
            )
        neighbours.append(nbrs[0].GetIdx())

    if len(neighbours) == 2 and neighbours[0] == neighbours[1]:
        result.reasons.append(
            (
                RejectionCode.ATTACHMENT_SAME_ATOM,
                f"both attachment points are on atom {neighbours[0]}; connecting "
                "this unit would form a ring, not a chain",
            )
        )

    # Elements.
    heavy = 0
    bad_elements: set[str] = set()
    for a in mol.GetAtoms():
        if a.GetAtomicNum() == 0:
            continue
        heavy += 1
        sym = a.GetSymbol()
        if sym not in rules.allowed_elements:
            bad_elements.add(sym)
        if rules.forbid_isotopes and a.GetIsotope() != 0:
            result.reasons.append(
                (RejectionCode.ISOTOPE, f"atom {a.GetIdx()} carries isotope label {a.GetIsotope()}")
            )
    result.heavy_atoms = heavy
    if bad_elements:
        result.reasons.append(
            (
                RejectionCode.DISALLOWED_ELEMENT,
                f"contains {', '.join(sorted(bad_elements))}; allowed: "
                f"{', '.join(sorted(rules.allowed_elements))}",
            )
        )

    if heavy < rules.min_heavy_atoms:
        result.reasons.append(
            (RejectionCode.TOO_SMALL, f"{heavy} heavy atoms, minimum {rules.min_heavy_atoms}")
        )
    if heavy > rules.max_heavy_atoms:
        result.reasons.append(
            (RejectionCode.TOO_LARGE, f"{heavy} heavy atoms, maximum {rules.max_heavy_atoms}")
        )

    # Sanitisation checks valence with the dummies in place.
    work = Chem.Mol(mol)
    try:
        Chem.SanitizeMol(work)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError) as exc:
        result.reasons.append((RejectionCode.SANITIZATION_FAILED, f"{type(exc).__name__}: {exc}"))
        return result

    # Valence as it will be once polymerised: replace each dummy with carbon,
    # which is the heaviest-valence substituent a backbone neighbour will see.
    if len(dummies) == rules.required_attachment_points and not any(
        c is RejectionCode.ATTACHMENT_NOT_TERMINAL for c, _ in result.reasons
    ):
        probe = Chem.RWMol(mol)
        for a in probe.GetAtoms():
            if a.GetAtomicNum() == 0:
                a.SetAtomicNum(6)
                a.SetNoImplicit(False)
                a.SetNumExplicitHs(0)
        try:
            Chem.SanitizeMol(probe)
        except (Chem.AtomValenceException, Chem.KekulizeException, ValueError) as exc:
            result.reasons.append(
                (
                    RejectionCode.BAD_VALENCE,
                    f"valence error once the attachment points are bonded: {exc}",
                )
            )

        # A backbone path must exist between the two attachment neighbours.
        # GetShortestPath raises on identical endpoints, so the same-atom case
        # is excluded first; it is already reported above.
        if (
            rules.require_backbone_path
            and len(neighbours) == 2
            and neighbours[0] != neighbours[1]
        ):
            if not Chem.GetShortestPath(work, neighbours[0], neighbours[1]):
                result.reasons.append(
                    (
                        RejectionCode.NO_BACKBONE_PATH,
                        "the two attachment points are on disconnected fragments",
                    )
                )

    # Radicals.
    radicals = sum(a.GetNumRadicalElectrons() for a in work.GetAtoms())
    if radicals:
        result.reasons.append(
            (RejectionCode.RADICAL, f"{radicals} unpaired electrons present")
        )

    # Charge and mass, computed on the dummy-free skeleton so the numbers are
    # meaningful: dummies contribute no mass but do affect formal charge sums.
    charge = Chem.GetFormalCharge(work)
    result.net_charge = charge
    if not rules.allow_net_charge and charge != 0:
        result.reasons.append((RejectionCode.CHARGED, f"net charge {charge}, neutral required"))
    elif abs(charge) > rules.max_abs_charge:
        result.reasons.append(
            (RejectionCode.CHARGED, f"|net charge| {abs(charge)} exceeds {rules.max_abs_charge}")
        )

    # Repeat-unit mass excludes the dummy atoms, since they are replaced by
    # real bonds in the chain.
    stripped = Chem.RWMol(work)
    for idx in sorted((a.GetIdx() for a in work.GetAtoms() if a.GetAtomicNum() == 0), reverse=True):
        stripped.RemoveAtom(idx)
    try:
        stripped_mol = stripped.GetMol()
        Chem.SanitizeMol(stripped_mol)
        result.repeat_mw = Descriptors.MolWt(stripped_mol)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        result.repeat_mw = Descriptors.MolWt(work)

    if result.repeat_mw < rules.min_repeat_mw:
        result.reasons.append(
            (RejectionCode.TOO_SMALL, f"repeat-unit MW {result.repeat_mw:.1f} below minimum")
        )
    if result.repeat_mw > rules.max_repeat_mw:
        result.reasons.append(
            (RejectionCode.TOO_LARGE, f"repeat-unit MW {result.repeat_mw:.1f} above maximum")
        )

    if not rules.allow_rings_in_backbone and work.GetRingInfo().NumRings():
        result.reasons.append(
            (RejectionCode.REACTIVE_GROUP, "rings are disallowed by the active rule set")
        )

    # Reactive chemistry.
    for label, smarts in rules.reactive_smarts:
        patt = Chem.MolFromSmarts(smarts)
        if patt is not None and work.HasSubstructMatch(patt):
            result.reasons.append(
                (RejectionCode.REACTIVE_GROUP, f"contains {label} ({smarts})")
            )

    for label, smarts in rules.caution_smarts:
        patt = Chem.MolFromSmarts(smarts)
        if patt is not None and work.HasSubstructMatch(patt):
            result.cautions.append(label)

    if not result.reasons:
        result.valid = True
        result.canonical_smiles = Chem.MolToSmiles(work)
    return result


def validate_candidate(
    candidate: PolymerCandidate, rules: PolymerizationRules | None = None
) -> ValidationResult:
    """Validate every block of a candidate, plus candidate-level constraints."""
    rules = rules or PolymerizationRules()
    combined = ValidationResult(valid=True, smiles=candidate.display_name())

    for block in candidate.blocks:
        sub = validate_repeat_unit(block.unit.smiles, rules)
        combined.heavy_atoms += sub.heavy_atoms
        combined.repeat_mw += sub.repeat_mw * block.mole_fraction
        combined.net_charge += sub.net_charge
        combined.cautions.extend(sub.cautions)
        if not sub.valid:
            combined.valid = False
            combined.reasons.extend(
                (code, f"[{block.unit.smiles}] {detail}") for code, detail in sub.reasons
            )

    if not candidate.architecture.buildable:
        combined.valid = False
        combined.reasons.append(
            (
                RejectionCode.ARCHITECTURE_NOT_BUILDABLE,
                f"architecture {candidate.architecture} is recorded as metadata but "
                "no oligomer construction rule is implemented for it; it cannot be "
                "screened or simulated",
            )
        )
    return combined


def normalise_repeat_unit(smiles: str) -> str | None:
    """Canonical form of a repeat unit, or ``None`` if it cannot be parsed.

    Used for deduplication so that ``[*]CCO[*]`` and ``[*]OCC[*]`` written
    differently collapse to one identity.
    """
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None:
        return None
    try:
        Chem.SanitizeMol(mol)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        return None
    return Chem.MolToSmiles(mol)


def repeat_unit_from_smiles(
    smiles: str,
    *,
    name: str | None = None,
    family: str | None = None,
    source: str = "manual",
    rules: PolymerizationRules | None = None,
) -> tuple[RepeatUnit | None, ValidationResult]:
    """Validate then construct a :class:`RepeatUnit`, or report why not."""
    result = validate_repeat_unit(smiles, rules)
    if not result.valid:
        return None, result
    unit = RepeatUnit(
        smiles=result.canonical_smiles or smiles,
        name=name,
        family=family,
        source=source,
    )
    return unit, result

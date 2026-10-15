"""Build explicit capped oligomers from a polymer specification.

Fingerprints, conformers and simulation all need an ordinary molecule. A
polymer is not one. This module constructs a finite, capped oligomer and
returns the exact construction rules alongside it, because an observable
computed on a DP-8 methyl-capped oligomer is a statement about *that
molecule*, not about the polymer, and the report has to say so.

Convention for attachment-point ordering: within a repeat unit, the dummy
atom with the lower atom index is the *head*, the higher is the *tail*. Units
are joined tail-to-head in sequence. This is recorded in every
:class:`OligomerBuild` so results stay interpretable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import Descriptors

from biologix.errors import PolymerizationError
from biologix.polymers.models import (
    Architecture,
    CopolymerKind,
    EndGroup,
    PolymerCandidate,
)

# Property keys used to track atoms across RDKit graph surgery. Index-based
# bookkeeping breaks as soon as atoms are removed, so roles travel with the
# atoms themselves.
_ROLE = "_bx_role"
_SLOT = "_bx_slot"


@dataclass
class OligomerBuild:
    """A built oligomer plus every rule used to build it."""

    mol: Chem.Mol
    smiles: str
    sequence: tuple[str, ...]
    degree_of_polymerisation: int
    end_group: str
    architecture: str
    copolymer_kind: str
    molecular_weight: float
    n_heavy_atoms: int
    construction_rules: dict[str, object] = field(default_factory=dict)

    def provenance(self) -> dict[str, object]:
        """The record that must accompany any number computed from this."""
        return {
            "oligomer_smiles": self.smiles,
            "degree_of_polymerisation": self.degree_of_polymerisation,
            "monomer_sequence": list(self.sequence),
            "end_group": self.end_group,
            "architecture": self.architecture,
            "copolymer_kind": self.copolymer_kind,
            "molecular_weight": round(self.molecular_weight, 3),
            "n_heavy_atoms": self.n_heavy_atoms,
            "construction_rules": self.construction_rules,
            "interpretation_note": (
                "Observables computed from this molecule describe a finite capped "
                f"DP-{self.degree_of_polymerisation} oligomer, not a polydisperse "
                "polymer. Do not read them as polymer properties without stating "
                "the oligomer length."
            ),
        }


def _tagged_copy(smiles: str, slot: int) -> Chem.Mol:
    """Parse a fragment and tag its dummy atoms with head/tail roles."""
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None:
        raise PolymerizationError(f"cannot parse fragment {smiles!r}")
    dummies = sorted(
        (a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0),
    )
    if not dummies:
        raise PolymerizationError(f"fragment {smiles!r} has no [*] attachment point")
    rw = Chem.RWMol(mol)
    # Lower index is head, higher is tail. With one dummy (a cap) it is "cap".
    if len(dummies) == 1:
        rw.GetAtomWithIdx(dummies[0]).SetProp(_ROLE, "cap")
        rw.GetAtomWithIdx(dummies[0]).SetIntProp(_SLOT, slot)
    else:
        rw.GetAtomWithIdx(dummies[0]).SetProp(_ROLE, "head")
        rw.GetAtomWithIdx(dummies[0]).SetIntProp(_SLOT, slot)
        rw.GetAtomWithIdx(dummies[-1]).SetProp(_ROLE, "tail")
        rw.GetAtomWithIdx(dummies[-1]).SetIntProp(_SLOT, slot)
        # Any extra dummies beyond two are pendant and get capped with H.
        for extra in dummies[1:-1]:
            rw.GetAtomWithIdx(extra).SetProp(_ROLE, "pendant")
            rw.GetAtomWithIdx(extra).SetIntProp(_SLOT, slot)
    return rw.GetMol()


def _find_tagged(mol: Chem.Mol, role: str, slot: int) -> int | None:
    for a in mol.GetAtoms():
        if (
            a.HasProp(_ROLE)
            and a.GetProp(_ROLE) == role
            and a.HasProp(_SLOT)
            and a.GetIntProp(_SLOT) == slot
        ):
            return a.GetIdx()
    return None


def _sole_neighbour(mol: Chem.Mol, idx: int) -> int:
    nbrs = list(mol.GetAtomWithIdx(idx).GetNeighbors())
    if len(nbrs) != 1:
        raise PolymerizationError(
            f"attachment atom {idx} has {len(nbrs)} neighbours; expected exactly one"
        )
    return nbrs[0].GetIdx()


def _bond_type_at(mol: Chem.Mol, dummy_idx: int) -> Chem.BondType:
    """Bond order at an attachment point, preserved through the join."""
    nbr = _sole_neighbour(mol, dummy_idx)
    bond = mol.GetBondBetweenAtoms(dummy_idx, nbr)
    return bond.GetBondType() if bond is not None else Chem.BondType.SINGLE


def _monomer_sequence(candidate: PolymerCandidate) -> list[int]:
    """Block indices along the chain, as a deterministic sequence.

    ``RANDOM`` copolymers are *not* randomised here: a seeded random sequence
    would make the built molecule depend on RNG state, which would then have
    to be threaded into every cache key and manifest. Instead a random
    copolymer is built as the deterministic composition-matched interleave,
    and the approximation is recorded in the construction rules.
    """
    dp = candidate.chain_length.degree_of_polymerisation
    n_blocks = len(candidate.blocks)
    if n_blocks == 1:
        return [0] * dp

    kind = candidate.copolymer_kind
    if kind is CopolymerKind.BLOCK:
        seq: list[int] = []
        for i, block in enumerate(candidate.blocks):
            count = round(block.mole_fraction * dp)
            seq.extend([i] * count)
        # Fix rounding drift by padding or trimming the last block.
        while len(seq) < dp:
            seq.append(n_blocks - 1)
        return seq[:dp]

    if kind is CopolymerKind.ALTERNATING:
        return [i % n_blocks for i in range(dp)]

    # RANDOM / anything else: largest-remainder interleave that matches the
    # requested mole fractions as closely as an integer sequence can.
    targets = [b.mole_fraction for b in candidate.blocks]
    counts = [0] * n_blocks
    seq = []
    for position in range(dp):
        # Pick the block furthest below its target share so far.
        deficits = [
            targets[i] * (position + 1) - counts[i] for i in range(n_blocks)
        ]
        pick = max(range(n_blocks), key=lambda i: deficits[i])
        counts[pick] += 1
        seq.append(pick)
    return seq


def build_oligomer(candidate: PolymerCandidate, *, max_atoms: int = 2000) -> OligomerBuild:
    """Construct a capped oligomer for ``candidate``.

    Raises :class:`PolymerizationError` rather than returning a partial or
    silently-different molecule.
    """
    if not candidate.architecture.buildable:
        raise PolymerizationError(
            f"no construction rule for architecture {candidate.architecture!r}; "
            "it can be recorded as metadata but not built, screened or simulated"
        )
    if candidate.architecture is Architecture.GRAFT:
        raise PolymerizationError(
            "graft architecture requires side-chain definitions that this "
            "candidate does not carry; build it as a linear backbone and attach "
            "grafts explicitly"
        )

    sequence = _monomer_sequence(candidate)
    dp = len(sequence)

    # Reject oversized builds before doing the work.
    per_unit_atoms = []
    for block in candidate.blocks:
        probe = Chem.MolFromSmiles(block.unit.smiles, sanitize=False)
        if probe is None:
            raise PolymerizationError(f"cannot parse repeat unit {block.unit.smiles!r}")
        per_unit_atoms.append(probe.GetNumAtoms())
    estimate = sum(per_unit_atoms[i] for i in sequence)
    if estimate > max_atoms:
        raise PolymerizationError(
            f"oligomer would have about {estimate} atoms, over the {max_atoms} limit; "
            f"reduce the degree of polymerisation (currently {dp})"
        )

    # Tag each unit copy with a unique slot so roles stay findable after
    # atoms are removed.
    fragments = [
        _tagged_copy(candidate.blocks[b].unit.smiles, slot)
        for slot, b in enumerate(sequence)
    ]

    combined = fragments[0]
    for slot in range(1, dp):
        combined = Chem.CombineMols(combined, fragments[slot])

    rw = Chem.RWMol(combined)

    # Join tail(slot) -> head(slot+1).
    to_remove: list[int] = []
    for slot in range(dp - 1):
        tail = _find_tagged(rw, "tail", slot)
        head = _find_tagged(rw, "head", slot + 1)
        if tail is None or head is None:
            raise PolymerizationError(
                f"missing attachment point joining unit {slot} to {slot + 1}"
            )
        a = _sole_neighbour(rw, tail)
        b = _sole_neighbour(rw, head)
        bond_type = _bond_type_at(rw, tail)
        if rw.GetBondBetweenAtoms(a, b) is None:
            rw.AddBond(a, b, bond_type)
        to_remove.extend((tail, head))

    # Cap the two surviving chain ends and any pendant dummies. The caps are
    # located by role, not by index, because the removals below renumber atoms.
    cap_smiles = candidate.end_group.smiles_fragment
    mol = rw.GetMol()
    # Remove the consumed dummies in descending index order so earlier
    # indices stay valid.
    rw2 = Chem.RWMol(mol)
    for idx in sorted(set(to_remove), reverse=True):
        rw2.RemoveAtom(idx)
    mol = rw2.GetMol()

    # Caps are attached by converting the remaining dummy into the cap's
    # first atom when the cap is a single atom, or by joining a cap fragment.
    mol = _attach_caps(mol, cap_smiles, candidate.end_group)

    # Pendant dummies become hydrogens.
    mol = _attach_caps(mol, "[*][H]", EndGroup.HYDROGEN, roles=("pendant",))

    # Clear working properties so they do not leak into output SMILES.
    rw3 = Chem.RWMol(mol)
    for a in rw3.GetAtoms():
        for key in (_ROLE, _SLOT):
            if a.HasProp(key):
                a.ClearProp(key)
    mol = rw3.GetMol()

    try:
        Chem.SanitizeMol(mol)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError) as exc:
        raise PolymerizationError(
            f"built oligomer failed sanitisation: {type(exc).__name__}: {exc}. "
            "The repeat unit is not polymerisable under these rules."
        ) from exc

    leftover = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if leftover:
        raise PolymerizationError(
            f"{len(leftover)} attachment points remain uncapped after construction"
        )

    mol = Chem.RemoveHs(mol)
    smiles = Chem.MolToSmiles(mol)

    # Capping can create chemistry that neither the repeat unit nor the cap
    # contained on its own: a hydroxyl cap on an oxygen-terminated unit makes
    # a peroxide, for instance. Re-screen the assembled molecule rather than
    # trusting that valid parts compose into a valid whole.
    introduced = _caps_introduced_reactive_groups(mol, cap_smiles)
    if introduced:
        raise PolymerizationError(
            f"capping this repeat unit with {candidate.end_group} created "
            f"{', '.join(introduced)} at the chain terminus ({smiles}). "
            "Choose a different end group for this chemistry."
        )

    rules = {
        "attachment_convention": "lower dummy atom index = head, higher = tail",
        "join_direction": "tail(i) bonded to head(i+1)",
        "cap_fragment_smiles": cap_smiles,
        "pendant_dummy_policy": "capped with hydrogen",
        "sequence_policy": (
            "deterministic largest-remainder interleave matching the requested "
            "mole fractions"
            if candidate.copolymer_kind is CopolymerKind.RANDOM
            else f"{candidate.copolymer_kind} sequence"
        ),
        "hydrogens": "implicit in the returned molecule; added explicitly before simulation",
    }
    if candidate.copolymer_kind is CopolymerKind.RANDOM:
        rules["approximation"] = (
            "a real random copolymer is an ensemble of sequences; this is one "
            "deterministic composition-matched sequence"
        )

    return OligomerBuild(
        mol=mol,
        smiles=smiles,
        sequence=tuple(candidate.blocks[b].unit.smiles for b in sequence),
        degree_of_polymerisation=dp,
        end_group=str(candidate.end_group),
        architecture=str(candidate.architecture),
        copolymer_kind=str(candidate.copolymer_kind),
        molecular_weight=Descriptors.MolWt(mol),
        n_heavy_atoms=mol.GetNumHeavyAtoms(),
        construction_rules=rules,
    )


# Groups that only ever arise from joining a cap to a terminus, never from a
# repeat unit that already passed validation. Checked on the assembled
# oligomer.
_CAP_ARTEFACT_SMARTS: tuple[tuple[str, str], ...] = (
    ("peroxide (O-O)", "[OX2][OX2]"),
    ("hydrazine-like (N-N)", "[NX3][NX3]"),
    ("disulfide (S-S)", "[SX2][SX2]"),
    ("hemiacetal/gem-diol", "[CX4]([OX2H1])[OX2H1]"),
    ("anhydride", "[CX3](=O)[OX2][CX3]=O"),
)


def _caps_introduced_reactive_groups(mol: Chem.Mol, cap_smiles: str) -> list[str]:
    """Reactive groups present in the assembled oligomer."""
    found: list[str] = []
    for label, smarts in _CAP_ARTEFACT_SMARTS:
        patt = Chem.MolFromSmarts(smarts)
        if patt is not None and mol.HasSubstructMatch(patt):
            found.append(label)
    return found


def _attach_caps(
    mol: Chem.Mol,
    cap_smiles: str,
    end_group: EndGroup,
    roles: tuple[str, ...] = ("head", "tail"),
) -> Chem.Mol:
    """Replace every dummy atom in ``roles`` with the cap fragment."""
    while True:
        target = None
        for a in mol.GetAtoms():
            if a.GetAtomicNum() != 0:
                continue
            if a.HasProp(_ROLE) and a.GetProp(_ROLE) in roles:
                target = a.GetIdx()
                break
        if target is None:
            return mol

        cap_core = cap_smiles.replace("[*]", "", 1)
        rw = Chem.RWMol(mol)

        # Single-atom caps: mutate the dummy in place, which avoids any
        # index churn.
        single = {
            EndGroup.HYDROGEN: 1,
            EndGroup.METHYL: 6,
            EndGroup.HYDROXYL: 8,
        }
        if end_group in single:
            atom = rw.GetAtomWithIdx(target)
            atom.SetAtomicNum(single[end_group])
            atom.SetNoImplicit(False)
            atom.SetNumExplicitHs(0)
            atom.ClearProp(_ROLE)
            mol = rw.GetMol()
            continue

        # Multi-atom cap: combine, bond, then drop the dummy.
        frag = Chem.MolFromSmiles(cap_core, sanitize=False)
        if frag is None:
            raise PolymerizationError(f"cannot parse cap fragment {cap_core!r}")
        anchor = _sole_neighbour(mol, target)
        offset = mol.GetNumAtoms()
        merged = Chem.RWMol(Chem.CombineMols(mol, frag))
        merged.AddBond(anchor, offset, Chem.BondType.SINGLE)
        merged.RemoveAtom(target)
        mol = merged.GetMol()


def oligomer_for_screening(
    candidate: PolymerCandidate, *, dp_override: int | None = None
) -> OligomerBuild:
    """Oligomer used for descriptor and fingerprint work.

    Screening uses a *fixed short* oligomer rather than the candidate's full
    DP, so that descriptors compare like with like across candidates whose
    requested DP differs. The override is recorded in the build rules.
    """
    if dp_override is None:
        return build_oligomer(candidate)

    trimmed = candidate.model_copy(
        update={
            "chain_length": candidate.chain_length.model_copy(
                update={"degree_of_polymerisation": dp_override}
            )
        }
    )
    build = build_oligomer(trimmed)
    build.construction_rules["dp_override"] = (
        f"screening used DP={dp_override} instead of the candidate's requested "
        f"DP={candidate.chain_length.degree_of_polymerisation} so that descriptors "
        "are comparable across candidates"
    )
    return build

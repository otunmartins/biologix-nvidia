"""Convert a small molecule into candidate repeat units.

A molecule generator proposes *molecules*. A polymer needs *repeat units with
attachment points*. The conversion is not automatic and not unique: one
molecule may be polymerisable by several mechanisms, or by none. This module
applies named polymerisation mechanisms explicitly, returns every repeat unit
each one yields, and reports when a molecule has no polymerisable handle at
all.

This is the step that stops "valid small molecule" being mistaken for "valid
polymer". Conversion is deliberately separate from
:mod:`biologix.polymers.rules`: a repeat unit produced here still has to pass
validation before it becomes a candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from rdkit import Chem


class Mechanism(StrEnum):
    """Polymerisation mechanisms this module can apply."""

    CHAIN_GROWTH_VINYL = "chain_growth_vinyl"
    RING_OPENING_EPOXIDE = "ring_opening_epoxide"
    RING_OPENING_LACTONE = "ring_opening_lactone"
    RING_OPENING_LACTAM = "ring_opening_lactam"
    RING_OPENING_CYCLIC_CARBONATE = "ring_opening_cyclic_carbonate"
    STEP_GROWTH_DIOL = "step_growth_diol"
    STEP_GROWTH_DIAMINE = "step_growth_diamine"
    STEP_GROWTH_DIACID = "step_growth_diacid"


@dataclass
class ConversionAttempt:
    """One mechanism applied to one molecule."""

    mechanism: Mechanism
    applied: bool
    repeat_units: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass
class ConversionResult:
    """All mechanisms tried for one input molecule."""

    input_smiles: str
    canonical_input: str | None
    attempts: list[ConversionAttempt] = field(default_factory=list)
    error: str | None = None

    @property
    def repeat_units(self) -> list[tuple[str, Mechanism]]:
        """Every distinct repeat unit found, with the mechanism that produced it."""
        seen: set[str] = set()
        out: list[tuple[str, Mechanism]] = []
        for attempt in self.attempts:
            if not attempt.applied:
                continue
            for smiles in attempt.repeat_units:
                if smiles in seen:
                    continue
                seen.add(smiles)
                out.append((smiles, attempt.mechanism))
        return out

    @property
    def polymerisable(self) -> bool:
        return bool(self.repeat_units)

    def rejection_reason(self) -> str:
        """Why this molecule yielded no repeat unit."""
        if self.error:
            return self.error
        if self.polymerisable:
            return ""
        tried = ", ".join(str(a.mechanism) for a in self.attempts)
        return (
            "no polymerisable handle found. The molecule may be a perfectly valid "
            f"small molecule but it has no site that any implemented mechanism can "
            f"open into a two-ended repeat unit. Mechanisms tried: {tried}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "input_smiles": self.input_smiles,
            "canonical_input": self.canonical_input,
            "polymerisable": self.polymerisable,
            "repeat_units": [
                {"smiles": s, "mechanism": str(m)} for s, m in self.repeat_units
            ],
            "attempts": [
                {
                    "mechanism": str(a.mechanism),
                    "applied": a.applied,
                    "n_repeat_units": len(a.repeat_units),
                    "reason": a.reason,
                }
                for a in self.attempts
            ],
            "rejection_reason": self.rejection_reason() or None,
        }


def _finalise(rw: Chem.RWMol) -> str | None:
    """Sanitise a modified molecule and return canonical SMILES, or None."""
    mol = rw.GetMol()
    try:
        Chem.SanitizeMol(mol)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        return None
    # A repeat unit must be a single connected fragment.
    if len(Chem.GetMolFrags(mol)) != 1:
        return None
    return Chem.MolToSmiles(mol)


def _add_dummy(rw: Chem.RWMol, atom_idx: int) -> None:
    dummy = rw.AddAtom(Chem.Atom(0))
    rw.AddBond(atom_idx, dummy, Chem.BondType.SINGLE)


def _open_ring_bond(mol: Chem.Mol, i: int, j: int) -> str | None:
    """Break the ring bond ``i-j`` and cap both ends with attachment points."""
    rw = Chem.RWMol(mol)
    if rw.GetBondBetweenAtoms(i, j) is None:
        return None
    rw.RemoveBond(i, j)
    _add_dummy(rw, i)
    _add_dummy(rw, j)
    for idx in (i, j):
        atom = rw.GetAtomWithIdx(idx)
        atom.SetNoImplicit(False)
        atom.SetNumExplicitHs(0)
    return _finalise(rw)


def _reduce_double_bond(mol: Chem.Mol, i: int, j: int) -> str | None:
    """Turn ``i=j`` into a single bond and cap both carbons."""
    rw = Chem.RWMol(mol)
    bond = rw.GetBondBetweenAtoms(i, j)
    if bond is None or bond.GetBondType() is not Chem.BondType.DOUBLE:
        return None
    bond.SetBondType(Chem.BondType.SINGLE)
    for idx in (i, j):
        atom = rw.GetAtomWithIdx(idx)
        atom.SetNoImplicit(False)
        atom.SetNumExplicitHs(0)
    _add_dummy(rw, i)
    _add_dummy(rw, j)
    return _finalise(rw)


def _dehydrogenate_heteroatoms(mol: Chem.Mol, atom_indices: list[int]) -> str | None:
    """Replace one H on each named heteroatom with an attachment point.

    This is the condensation case: a diol becomes ``[*]O-R-O[*]``.
    """
    rw = Chem.RWMol(mol)
    for idx in atom_indices:
        atom = rw.GetAtomWithIdx(idx)
        total_h = atom.GetTotalNumHs()
        if total_h < 1:
            return None
        atom.SetNoImplicit(True)
        atom.SetNumExplicitHs(total_h - 1)
        _add_dummy(rw, idx)
    return _finalise(rw)


def _drop_leaving_group(mol: Chem.Mol, carbonyl_c: int, hydroxyl_o: int) -> str | None:
    """Remove a carboxylic -OH and cap the carbonyl carbon.

    A diacid becomes ``[*]C(=O)-R-C(=O)[*]``.
    """
    rw = Chem.RWMol(mol)
    rw.RemoveBond(carbonyl_c, hydroxyl_o)
    _add_dummy(rw, carbonyl_c)
    atom = rw.GetAtomWithIdx(carbonyl_c)
    atom.SetNoImplicit(False)
    atom.SetNumExplicitHs(0)
    rw.RemoveAtom(hydroxyl_o)
    return _finalise(rw)


# Site-finding SMARTS for each mechanism.
_VINYL = Chem.MolFromSmarts("[CX3;!R:1]=[CX3;!R:2]")
_EPOXIDE = Chem.MolFromSmarts("[OX2r3:1]1[#6r3:2][#6r3:3]1")
# The trailing [#6] excludes cyclic carbonates, whose carbonyl carries two
# ring oxygens; they get their own mechanism so the label stays truthful.
_LACTONE = Chem.MolFromSmarts("[OX2R:1][CX3R:2](=[OX1])[#6]")
_LACTAM = Chem.MolFromSmarts("[NX3R:1][CX3R:2]=[OX1]")
_CYCLIC_CARBONATE = Chem.MolFromSmarts("[OX2R:1][CX3R:2](=[OX1])[OX2R]")
_HYDROXYL = Chem.MolFromSmarts("[OX2H1]")
_PRIMARY_AMINE = Chem.MolFromSmarts("[NX3;H2;!$(NC=O)]")
_CARBOXYLIC_ACID = Chem.MolFromSmarts("[CX3:1](=[OX1])[OX2H1:2]")


def convert_molecule(smiles: str, *, max_units_per_mechanism: int = 3) -> ConversionResult:
    """Apply every mechanism to ``smiles`` and collect the repeat units.

    ``max_units_per_mechanism`` caps how many distinct sites of one kind are
    opened, so a molecule with many double bonds does not explode the output.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return ConversionResult(
            input_smiles=smiles,
            canonical_input=None,
            error=f"RDKit could not parse {smiles!r}",
        )

    result = ConversionResult(
        input_smiles=smiles, canonical_input=Chem.MolToSmiles(mol)
    )

    # --- chain growth across a non-ring C=C ------------------------------
    matches = mol.GetSubstructMatches(_VINYL)[:max_units_per_mechanism]
    units: list[str] = []
    for i, j in matches:
        out = _reduce_double_bond(mol, i, j)
        if out:
            units.append(out)
    result.attempts.append(
        ConversionAttempt(
            Mechanism.CHAIN_GROWTH_VINYL,
            applied=bool(units),
            repeat_units=units,
            reason=None if units else "no non-ring C=C available",
        )
    )

    # --- ring-opening mechanisms -----------------------------------------
    for mechanism, patt, bond_atoms in (
        (Mechanism.RING_OPENING_EPOXIDE, _EPOXIDE, (0, 1)),
        (Mechanism.RING_OPENING_LACTONE, _LACTONE, (0, 1)),
        (Mechanism.RING_OPENING_LACTAM, _LACTAM, (0, 1)),
        (Mechanism.RING_OPENING_CYCLIC_CARBONATE, _CYCLIC_CARBONATE, (0, 1)),
    ):
        if patt is None:
            continue
        found = mol.GetSubstructMatches(patt)[:max_units_per_mechanism]
        units = []
        for match in found:
            i, j = match[bond_atoms[0]], match[bond_atoms[1]]
            out = _open_ring_bond(mol, i, j)
            if out:
                units.append(out)
        result.attempts.append(
            ConversionAttempt(
                mechanism,
                applied=bool(units),
                repeat_units=units,
                reason=None if units else "no matching ring site",
            )
        )

    # --- step growth: exactly two matching functional groups -------------
    for mechanism, patt, label in (
        (Mechanism.STEP_GROWTH_DIOL, _HYDROXYL, "hydroxyl"),
        (Mechanism.STEP_GROWTH_DIAMINE, _PRIMARY_AMINE, "primary amine"),
    ):
        if patt is None:
            continue
        found = [m[0] for m in mol.GetSubstructMatches(patt)]
        if len(found) == 2:
            out = _dehydrogenate_heteroatoms(mol, found)
            result.attempts.append(
                ConversionAttempt(
                    mechanism,
                    applied=bool(out),
                    repeat_units=[out] if out else [],
                    reason=None if out else "dehydrogenation produced an invalid structure",
                )
            )
        else:
            result.attempts.append(
                ConversionAttempt(
                    mechanism,
                    applied=False,
                    reason=f"needs exactly two {label} groups, found {len(found)}",
                )
            )

    if _CARBOXYLIC_ACID is not None:
        acids = mol.GetSubstructMatches(_CARBOXYLIC_ACID)
        if len(acids) == 2:
            rw = Chem.RWMol(mol)
            # Remove both hydroxyls, highest index first so indices stay valid.
            pairs = sorted(((m[0], m[2]) for m in acids), key=lambda p: -p[1])
            ok = True
            for carbonyl_c, hydroxyl_o in pairs:
                if rw.GetBondBetweenAtoms(carbonyl_c, hydroxyl_o) is None:
                    ok = False
                    break
                rw.RemoveBond(carbonyl_c, hydroxyl_o)
                _add_dummy(rw, carbonyl_c)
                rw.GetAtomWithIdx(carbonyl_c).SetNoImplicit(False)
                rw.GetAtomWithIdx(carbonyl_c).SetNumExplicitHs(0)
                rw.RemoveAtom(hydroxyl_o)
            out = _finalise(rw) if ok else None
            result.attempts.append(
                ConversionAttempt(
                    Mechanism.STEP_GROWTH_DIACID,
                    applied=bool(out),
                    repeat_units=[out] if out else [],
                    reason=None if out else "could not remove both leaving groups",
                )
            )
        else:
            result.attempts.append(
                ConversionAttempt(
                    Mechanism.STEP_GROWTH_DIACID,
                    applied=False,
                    reason=f"needs exactly two carboxylic acids, found {len(acids)}",
                )
            )

    return result


def convert_many(
    smiles_list: list[str], *, max_units_per_mechanism: int = 3
) -> list[ConversionResult]:
    return [
        convert_molecule(s, max_units_per_mechanism=max_units_per_mechanism)
        for s in smiles_list
    ]


def conversion_statistics(results: list[ConversionResult]) -> dict[str, object]:
    """Aggregate conversion yield, which is the headline GenMol metric.

    The fraction of generated molecules that become valid repeat units is the
    number that matters; generating valid *molecules* is not the task.
    """
    n = len(results)
    polymerisable = sum(1 for r in results if r.polymerisable)
    by_mechanism: dict[str, int] = {}
    for r in results:
        for _, mech in r.repeat_units:
            by_mechanism[str(mech)] = by_mechanism.get(str(mech), 0) + 1
    return {
        "n_molecules": n,
        "n_polymerisable": polymerisable,
        "n_not_polymerisable": n - polymerisable,
        "conversion_rate": round(polymerisable / n, 4) if n else 0.0,
        "repeat_units_found": sum(len(r.repeat_units) for r in results),
        "by_mechanism": dict(sorted(by_mechanism.items(), key=lambda kv: -kv[1])),
        "note": (
            "conversion_rate is the fraction of generated molecules with any "
            "polymerisable handle. It is an upper bound on usefulness: each "
            "resulting repeat unit must still pass polymerisation validation."
        ),
    }

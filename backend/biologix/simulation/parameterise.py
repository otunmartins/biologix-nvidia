"""Parameterise a polymer oligomer for OpenMM from MMFF94.

Why MMFF94: a novel polymer has no residue template, so a protein force field
cannot type it, and the usual small-molecule route (OpenFF/GAFF via
``openmmforcefields``) adds heavy conda-only dependencies. RDKit already
carries a complete MMFF94 implementation and exposes its parameters, so the
terms are read out and translated into OpenMM forces.

**Approximations, all of them deliberate and recorded in
:class:`ParameterisationReport`:**

1. MMFF's cubic bond-stretch term is truncated to harmonic.
2. MMFF's cubic angle-bend term is truncated to harmonic.
3. Stretch-bend coupling is omitted.
4. Out-of-plane bending is omitted. Planarity at sp2 centres is maintained
   only by torsion terms.
5. MMFF's buffered 14-7 van der Waals form is approximated by Lennard-Jones
   12-6 with :math:`\\sigma = R^*/2^{1/6}`, and MMFF's combination rules are
   replaced by OpenMM's Lorentz-Berthelot.
6. 1-4 interactions are unscaled, which is what MMFF94 specifies.

The resulting force field is suitable for *relative* comparison of polymer
candidates against the same target under identical settings. It is not a
quantitative free-energy model.

Unit conventions: RDKit returns MMFF parameters in MMFF's own units
(kcal/mol, angstrom, degrees, millidyne). OpenMM uses kJ/mol, nanometres and
radians. Every conversion constant below is named and derived.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from rdkit import Chem
from rdkit.Chem import AllChem

from biologix.errors import ValidationFailure
from biologix.polymers.oligomer import OligomerBuild

# --- unit conversions ------------------------------------------------------
KCAL_PER_MOL_TO_KJ = 4.184
ANGSTROM_TO_NM = 0.1
# MMFF bond/angle force constants are in millidyne units. 143.9325 converts
# md/A to kcal/(mol*A^2) for bonds, and md*A/rad^2 to kcal/(mol*rad^2) for
# angles (equal to 0.043844 * (180/pi)^2).
MMFF_MD_TO_KCAL = 143.9325
# kcal/(mol*A^2) -> kJ/(mol*nm^2): *4.184 kJ/kcal, *100 nm^-2 per A^-2.
BOND_K_TO_OPENMM = MMFF_MD_TO_KCAL * KCAL_PER_MOL_TO_KJ * 100.0
# kcal/(mol*rad^2) -> kJ/(mol*rad^2)
ANGLE_K_TO_OPENMM = MMFF_MD_TO_KCAL * KCAL_PER_MOL_TO_KJ
# LJ minimum sits at r = 2^(1/6) * sigma, so sigma = R_star / 2^(1/6).
SIGMA_FROM_RSTAR = 1.0 / (2.0 ** (1.0 / 6.0))

# Standard OBC implicit-solvent radii (nm) and scale factors by element.
# Used when the caller requests implicit solvent, since a novel polymer has
# no residue template to look them up from.
OBC_RADII_NM: dict[int, float] = {
    1: 0.120, 6: 0.170, 7: 0.155, 8: 0.150, 9: 0.150,
    14: 0.210, 15: 0.185, 16: 0.180, 17: 0.170,
}
OBC_SCALES: dict[int, float] = {
    1: 0.85, 6: 0.72, 7: 0.79, 8: 0.85, 9: 0.88,
    14: 0.80, 15: 0.86, 16: 0.96, 17: 0.80,
}
DEFAULT_OBC_RADIUS_NM = 0.150
DEFAULT_OBC_SCALE = 0.80


@dataclass
class ParameterisationReport:
    """Whether a polymer was parameterised well enough to simulate."""

    ok: bool
    n_atoms: int
    n_bonds: int
    n_angles: int
    n_torsions: int
    missing_bond_params: list[tuple[int, int]] = field(default_factory=list)
    missing_angle_params: list[tuple[int, int, int]] = field(default_factory=list)
    missing_torsion_params: int = 0
    total_charge: float = 0.0
    formal_charge: int = 0
    charge_error: float = 0.0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    force_field: str = "MMFF94 (harmonic truncation) via RDKit"
    approximations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "force_field": self.force_field,
            "n_atoms": self.n_atoms,
            "n_bonds": self.n_bonds,
            "n_angles": self.n_angles,
            "n_torsions": self.n_torsions,
            "n_missing_bond_params": len(self.missing_bond_params),
            "n_missing_angle_params": len(self.missing_angle_params),
            "n_missing_torsion_params": self.missing_torsion_params,
            "total_partial_charge": round(self.total_charge, 6),
            "formal_charge": self.formal_charge,
            "charge_error": round(self.charge_error, 6),
            "failures": self.failures,
            "warnings": self.warnings,
            "approximations": self.approximations,
        }


APPROXIMATIONS = [
    "MMFF cubic bond-stretch truncated to harmonic",
    "MMFF cubic angle-bend truncated to harmonic",
    "stretch-bend coupling omitted",
    "out-of-plane bending omitted; sp2 planarity maintained by torsions only",
    "MMFF buffered 14-7 van der Waals approximated by Lennard-Jones 12-6 "
    "with sigma = R*/2^(1/6)",
    "MMFF combination rules replaced by OpenMM Lorentz-Berthelot",
    "1-4 interactions unscaled, per MMFF94",
]


@dataclass
class PolymerParameters:
    """A polymer oligomer with 3D coordinates and MMFF-derived parameters."""

    mol: Chem.Mol
    report: ParameterisationReport
    # Per-atom nonbonded parameters, in OpenMM units.
    charges: list[float]
    sigmas_nm: list[float]
    epsilons_kj: list[float]
    # Bonded terms as OpenMM-ready tuples.
    bonds: list[tuple[int, int, float, float]]
    angles: list[tuple[int, int, int, float, float]]
    torsions: list[tuple[int, int, int, int, int, float, float]]
    conformer_seed: int = 0

    @property
    def n_atoms(self) -> int:
        return self.mol.GetNumAtoms()

    def positions_nm(self) -> list[tuple[float, float, float]]:
        conf = self.mol.GetConformer()
        return [
            (p.x * ANGSTROM_TO_NM, p.y * ANGSTROM_TO_NM, p.z * ANGSTROM_TO_NM)
            for p in (conf.GetAtomPosition(i) for i in range(self.mol.GetNumAtoms()))
        ]


def _enumerate_angles(mol: Chem.Mol) -> list[tuple[int, int, int]]:
    angles = []
    for atom in mol.GetAtoms():
        nbrs = sorted(n.GetIdx() for n in atom.GetNeighbors())
        centre = atom.GetIdx()
        for a_i in range(len(nbrs)):
            for a_j in range(a_i + 1, len(nbrs)):
                angles.append((nbrs[a_i], centre, nbrs[a_j]))
    return angles


def _enumerate_torsions(mol: Chem.Mol) -> list[tuple[int, int, int, int]]:
    torsions = []
    for bond in mol.GetBonds():
        b, c = bond.GetBeginAtom(), bond.GetEndAtom()
        b_nbrs = [n.GetIdx() for n in b.GetNeighbors() if n.GetIdx() != c.GetIdx()]
        c_nbrs = [n.GetIdx() for n in c.GetNeighbors() if n.GetIdx() != b.GetIdx()]
        for a in b_nbrs:
            for d in c_nbrs:
                if a == d:
                    continue
                torsions.append((a, b.GetIdx(), c.GetIdx(), d))
    return torsions


def parameterise_polymer(
    build: OligomerBuild,
    *,
    seed: int = 20260101,
    optimise_iterations: int = 1000,
    strict: bool = True,
) -> PolymerParameters:
    """Embed, optimise and parameterise the oligomer from ``build``.

    Raises :class:`ValidationFailure` when ``strict`` and parameterisation is
    incomplete. Validating *before* simulating is the point: an unparameterised
    atom silently given zero charge and zero LJ would produce a plausible
    looking interaction energy that is meaningless.
    """
    mol = Chem.AddHs(Chem.Mol(build.mol))

    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    if AllChem.EmbedMolecule(mol, params) != 0:
        # Retry with random coordinates, which succeeds for awkward topologies.
        params.useRandomCoords = True
        if AllChem.EmbedMolecule(mol, params) != 0:
            raise ValidationFailure(
                f"could not generate 3D coordinates for oligomer {build.smiles!r}; "
                "it cannot be simulated"
            )
    AllChem.MMFFOptimizeMolecule(mol, maxIters=optimise_iterations)

    mp = AllChem.MMFFGetMoleculeProperties(mol)
    report = ParameterisationReport(
        ok=True,
        n_atoms=mol.GetNumAtoms(),
        n_bonds=mol.GetNumBonds(),
        n_angles=0,
        n_torsions=0,
        formal_charge=Chem.GetFormalCharge(mol),
        approximations=list(APPROXIMATIONS),
    )

    if mp is None:
        report.ok = False
        report.failures.append(
            "RDKit could not assign MMFF94 atom types to this molecule. It cannot "
            "be parameterised with this force field. No simulation will be run."
        )
        if strict:
            raise ValidationFailure(report.failures[-1])
        return PolymerParameters(
            mol=mol, report=report, charges=[], sigmas_nm=[], epsilons_kj=[],
            bonds=[], angles=[], torsions=[], conformer_seed=seed,
        )

    # --- nonbonded -------------------------------------------------------
    charges: list[float] = []
    sigmas: list[float] = []
    epsilons: list[float] = []
    for i in range(mol.GetNumAtoms()):
        charges.append(float(mp.GetMMFFPartialCharge(i)))
        vdw = mp.GetMMFFVdWParams(i, i)
        if vdw is None:
            report.warnings.append(
                f"atom {i} ({mol.GetAtomWithIdx(i).GetSymbol()}) has no MMFF vdW "
                "parameters; using a neutral default, which weakens any contact "
                "it makes"
            )
            sigmas.append(0.30)
            epsilons.append(0.05)
            continue
        # RDKit returns (R_ij_star, epsilon, R_ij_star_unscaled, epsilon_unscaled).
        r_star_angstrom = float(vdw[0])
        epsilon_kcal = float(vdw[1])
        sigmas.append(r_star_angstrom * ANGSTROM_TO_NM * SIGMA_FROM_RSTAR)
        epsilons.append(epsilon_kcal * KCAL_PER_MOL_TO_KJ)

    report.total_charge = sum(charges)
    report.charge_error = abs(report.total_charge - report.formal_charge)
    if report.charge_error > 0.01:
        report.warnings.append(
            f"MMFF partial charges sum to {report.total_charge:.4f} but the formal "
            f"charge is {report.formal_charge}; difference {report.charge_error:.4f}"
        )

    # --- bonds -----------------------------------------------------------
    bonds: list[tuple[int, int, float, float]] = []
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        bp = mp.GetMMFFBondStretchParams(mol, i, j)
        if bp is None:
            report.missing_bond_params.append((i, j))
            continue
        # (type_index, kb [md/A], r0 [A])
        kb, r0 = float(bp[1]), float(bp[2])
        bonds.append((i, j, r0 * ANGSTROM_TO_NM, kb * BOND_K_TO_OPENMM))

    # --- angles ----------------------------------------------------------
    angles: list[tuple[int, int, int, float, float]] = []
    for i, j, k in _enumerate_angles(mol):
        report.n_angles += 1
        ap = mp.GetMMFFAngleBendParams(mol, i, j, k)
        if ap is None:
            report.missing_angle_params.append((i, j, k))
            continue
        # (type_index, ka [md*A/rad^2], theta0 [degrees])
        ka, theta0_deg = float(ap[1]), float(ap[2])
        angles.append(
            (i, j, k, math.radians(theta0_deg), ka * ANGLE_K_TO_OPENMM)
        )

    # --- torsions --------------------------------------------------------
    torsions: list[tuple[int, int, int, int, int, float, float]] = []
    # i, j, k, l: the conventional four-atom dihedral naming (see assemble.py).
    for i, j, k, l in _enumerate_torsions(mol):  # noqa: E741
        report.n_torsions += 1
        tp = mp.GetMMFFTorsionParams(mol, i, j, k, l)
        if tp is None:
            report.missing_torsion_params += 1
            continue
        # (type_index, V1, V2, V3) in kcal/mol, for
        # E = 0.5*(V1(1+cos phi) + V2(1-cos 2phi) + V3(1+cos 3phi)).
        v1, v2, v3 = float(tp[1]), float(tp[2]), float(tp[3])
        for periodicity, amplitude, phase in (
            (1, v1, 0.0),
            (2, v2, math.pi),  # 1 - cos(2x) == 1 + cos(2x - pi)
            (3, v3, 0.0),
        ):
            if abs(amplitude) < 1e-8:
                continue
            torsions.append(
                (i, j, k, l, periodicity, phase, 0.5 * amplitude * KCAL_PER_MOL_TO_KJ)
            )

    # --- validation gate -------------------------------------------------
    if report.missing_bond_params:
        report.ok = False
        report.failures.append(
            f"{len(report.missing_bond_params)} bond(s) have no MMFF parameters: "
            f"{report.missing_bond_params[:5]}"
        )
    if report.missing_angle_params:
        report.ok = False
        report.failures.append(
            f"{len(report.missing_angle_params)} angle(s) have no MMFF parameters: "
            f"{report.missing_angle_params[:5]}"
        )
    if report.n_torsions and report.missing_torsion_params / report.n_torsions > 0.25:
        report.ok = False
        report.failures.append(
            f"{report.missing_torsion_params} of {report.n_torsions} torsions have "
            "no MMFF parameters (over the 25% tolerance); the backbone would be "
            "under-restrained"
        )
    elif report.missing_torsion_params:
        report.warnings.append(
            f"{report.missing_torsion_params} of {report.n_torsions} torsions have "
            "no MMFF parameters and contribute no barrier"
        )

    if not report.ok and strict:
        raise ValidationFailure(
            "polymer parameterisation failed, so no simulation will be run:\n  "
            + "\n  ".join(report.failures)
        )

    return PolymerParameters(
        mol=mol,
        report=report,
        charges=charges,
        sigmas_nm=sigmas,
        epsilons_kj=epsilons,
        bonds=bonds,
        angles=angles,
        torsions=torsions,
        conformer_seed=seed,
    )


def obc_parameters(mol: Chem.Mol) -> list[tuple[float, float]]:
    """Per-atom OBC radius (nm) and scale factor, by element."""
    out = []
    for atom in mol.GetAtoms():
        z = atom.GetAtomicNum()
        out.append(
            (OBC_RADII_NM.get(z, DEFAULT_OBC_RADIUS_NM), OBC_SCALES.get(z, DEFAULT_OBC_SCALE))
        )
    return out

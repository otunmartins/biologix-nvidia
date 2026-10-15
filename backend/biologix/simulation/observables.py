"""Observables computed from a simulated protein-polymer system.

Each observable carries its own interpretation note. The notes are not
decoration: ``interaction_energy`` in particular is routinely misread, so the
class that holds it states in terms what it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# Heavy-atom contact cutoff, in nm.
CONTACT_CUTOFF_NM = 0.40
# Hydrogen-bond geometric criteria: donor-acceptor distance and D-H...A angle.
HBOND_MAX_DISTANCE_NM = 0.35
HBOND_MIN_ANGLE_DEG = 120.0


@dataclass
class InteractionEnergy:
    """Single-point interaction energy, with its meaning pinned down."""

    total_kj_per_mol: float
    complex_kj_per_mol: float
    protein_kj_per_mol: float
    polymer_kj_per_mol: float
    solvent_model: str

    @property
    def interpretation(self) -> dict[str, str]:
        in_solvent = self.solvent_model.startswith("implicit")
        return {
            "definition": (
                "E(complex) - E(protein alone) - E(polymer alone), all evaluated "
                "at the same coordinates. Intramolecular terms cancel, leaving "
                "protein-polymer cross terms"
                + (
                    " plus the non-additive implicit-solvation (desolvation) "
                    "contribution, which does not cancel."
                    if in_solvent
                    else "."
                )
            ),
            "is_not_binding_free_energy": (
                "This is a potential energy difference at one configuration. It "
                "contains no entropy, no conformational sampling and no explicit "
                "solvent reorganisation. It is not a binding free energy and "
                "cannot be converted into one."
            ),
            "is_not_shelf_life": (
                "It says nothing about shelf life, storage stability, or how long "
                "a formulation will remain within specification."
            ),
            "is_not_proof_of_stabilisation": (
                "A favourable interaction energy is not evidence that the polymer "
                "stabilises the biologic. Strong binding can equally promote "
                "unfolding or aggregation. Only a laboratory assay can establish "
                "a stabilising effect."
            ),
            "comparability": (
                "Useful for ranking candidates against the same target under "
                "identical settings. Not comparable across different force "
                "fields, solvent models, cutoffs or targets."
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_kj_per_mol": round(self.total_kj_per_mol, 3),
            "complex_kj_per_mol": round(self.complex_kj_per_mol, 3),
            "protein_kj_per_mol": round(self.protein_kj_per_mol, 3),
            "polymer_kj_per_mol": round(self.polymer_kj_per_mol, 3),
            "solvent_model": self.solvent_model,
            "evidence": "computed",
            "interpretation": self.interpretation,
        }


@dataclass
class ContactObservables:
    """Geometric description of the protein-polymer interface."""

    n_heavy_contacts: int
    min_distance_nm: float
    n_contacting_residues: int
    contacting_residues: list[str]
    n_hydrogen_bonds: int
    contact_cutoff_nm: float = CONTACT_CUTOFF_NM

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_heavy_contacts": self.n_heavy_contacts,
            "min_distance_nm": round(self.min_distance_nm, 4),
            "n_contacting_residues": self.n_contacting_residues,
            "contacting_residues": self.contacting_residues,
            "n_hydrogen_bonds": self.n_hydrogen_bonds,
            "contact_cutoff_nm": self.contact_cutoff_nm,
            "hbond_criteria": {
                "max_donor_acceptor_nm": HBOND_MAX_DISTANCE_NM,
                "min_angle_deg": HBOND_MIN_ANGLE_DEG,
                "note": "purely geometric criterion, no energetic term",
            },
            "evidence": "computed",
        }


@dataclass
class StructuralObservables:
    """How much the protein and polymer moved or changed shape."""

    protein_ca_rmsd_nm: float
    protein_radius_of_gyration_nm: float
    protein_rg_change_nm: float
    polymer_radius_of_gyration_nm: float
    polymer_end_to_end_nm: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "protein_ca_rmsd_nm": round(self.protein_ca_rmsd_nm, 4),
            "protein_radius_of_gyration_nm": round(self.protein_radius_of_gyration_nm, 4),
            "protein_rg_change_nm": round(self.protein_rg_change_nm, 4),
            "polymer_radius_of_gyration_nm": round(self.polymer_radius_of_gyration_nm, 4),
            "polymer_end_to_end_nm": round(self.polymer_end_to_end_nm, 4),
            "evidence": "computed",
            "interpretation": (
                "RMSD and radius-of-gyration change describe motion over this "
                "short trajectory. On nanosecond timescales they do not measure "
                "unfolding, and a small RMSD is not evidence of conformational "
                "stabilisation."
            ),
        }


def single_point_energy(system: Any, positions: Any, platform: Any) -> float:
    """Potential energy of ``system`` at ``positions``, in kJ/mol."""
    import openmm
    import openmm.unit as unit

    integrator = openmm.VerletIntegrator(0.001 * unit.picosecond)
    context = openmm.Context(system, integrator, platform)
    try:
        context.setPositions(positions)
        state = context.getState(getEnergy=True)
        return float(state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole))
    finally:
        del context
        del integrator


def interaction_energy(
    assembled: Any, positions: Any, platform: Any
) -> InteractionEnergy:
    """Decompose the energy at ``positions`` into the interaction term."""
    import openmm.unit as unit

    xyz = np.array(positions.value_in_unit(unit.nanometer))
    n_prot = assembled.n_protein_atoms

    e_complex = single_point_energy(assembled.complex_system, positions, platform)
    e_protein = single_point_energy(
        assembled.protein_system, xyz[:n_prot] * unit.nanometer, platform
    )
    e_polymer = single_point_energy(
        assembled.polymer_system, xyz[n_prot:] * unit.nanometer, platform
    )
    return InteractionEnergy(
        total_kj_per_mol=e_complex - e_protein - e_polymer,
        complex_kj_per_mol=e_complex,
        protein_kj_per_mol=e_protein,
        polymer_kj_per_mol=e_polymer,
        solvent_model=assembled.solvent,
    )


def _heavy_atom_mask(topology: Any) -> np.ndarray:
    return np.array(
        [
            atom.element is not None and atom.element.atomic_number > 1
            for atom in topology.atoms()
        ]
    )


def contact_observables(
    assembled: Any, positions: Any
) -> ContactObservables:
    """Interface geometry between protein and polymer."""
    import openmm.unit as unit

    xyz = np.array(positions.value_in_unit(unit.nanometer))
    n_prot = assembled.n_protein_atoms
    topology = assembled.complex_topology
    atoms = list(topology.atoms())

    heavy = _heavy_atom_mask(topology)
    prot_idx = np.flatnonzero(heavy[:n_prot])
    poly_idx = np.flatnonzero(heavy[n_prot:]) + n_prot

    if prot_idx.size == 0 or poly_idx.size == 0:
        return ContactObservables(0, float("inf"), 0, [], 0)

    diff = xyz[poly_idx][:, None, :] - xyz[prot_idx][None, :, :]
    dist = np.linalg.norm(diff, axis=-1)

    close = dist <= CONTACT_CUTOFF_NM
    n_contacts = int(close.sum())
    min_distance = float(dist.min())

    residues: set[str] = set()
    for local in np.flatnonzero(close.any(axis=0)):
        atom = atoms[int(prot_idx[local])]
        residue = atom.residue
        residues.add(f"{residue.chain.id}:{residue.name}{residue.id}")

    n_hbonds = _count_hydrogen_bonds(xyz, atoms, n_prot)

    return ContactObservables(
        n_heavy_contacts=n_contacts,
        min_distance_nm=min_distance,
        n_contacting_residues=len(residues),
        contacting_residues=sorted(residues),
        n_hydrogen_bonds=n_hbonds,
    )


def _count_hydrogen_bonds(xyz: np.ndarray, atoms: list[Any], n_prot: int) -> int:
    """Count protein-polymer hydrogen bonds by a geometric criterion."""
    donors: list[tuple[int, int, bool]] = []  # (donor heavy, hydrogen, is_protein)
    acceptors: list[tuple[int, bool]] = []

    neighbours: dict[int, list[int]] = {}
    for i, atom in enumerate(atoms):
        z = atom.element.atomic_number if atom.element is not None else 0
        if z in (7, 8):
            acceptors.append((i, i < n_prot))
    # Hydrogens bonded to N/O are donors; use distance to find their heavy partner.
    heavy_no = [i for i, a in enumerate(atoms)
                if a.element is not None and a.element.atomic_number in (7, 8)]
    if not heavy_no:
        return 0
    heavy_xyz = xyz[heavy_no]
    for i, atom in enumerate(atoms):
        if atom.element is None or atom.element.atomic_number != 1:
            continue
        d = np.linalg.norm(heavy_xyz - xyz[i], axis=1)
        j = int(np.argmin(d))
        if d[j] <= 0.125:  # covalent N-H or O-H
            donors.append((heavy_no[j], i, heavy_no[j] < n_prot))
    del neighbours

    count = 0
    for donor_heavy, hydrogen, donor_is_protein in donors:
        for acceptor, acceptor_is_protein in acceptors:
            if donor_is_protein == acceptor_is_protein:
                continue  # only count cross-interface bonds
            if acceptor == donor_heavy:
                continue
            da = np.linalg.norm(xyz[acceptor] - xyz[donor_heavy])
            if da > HBOND_MAX_DISTANCE_NM:
                continue
            v1 = xyz[donor_heavy] - xyz[hydrogen]
            v2 = xyz[acceptor] - xyz[hydrogen]
            n1 = np.linalg.norm(v1)
            n2 = np.linalg.norm(v2)
            if n1 == 0 or n2 == 0:
                continue
            cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
            angle = np.degrees(np.arccos(cos))
            if angle >= HBOND_MIN_ANGLE_DEG:
                count += 1
    return count


def _radius_of_gyration(xyz: np.ndarray, masses: np.ndarray) -> float:
    total = masses.sum()
    if total <= 0:
        return 0.0
    centre = (xyz * masses[:, None]).sum(axis=0) / total
    sq = ((xyz - centre) ** 2).sum(axis=1)
    return float(np.sqrt((masses * sq).sum() / total))


def structural_observables(
    assembled: Any, start_positions: Any, end_positions: Any
) -> StructuralObservables:
    """Protein and polymer structural change between two frames."""
    import openmm.unit as unit

    start = np.array(start_positions.value_in_unit(unit.nanometer))
    end = np.array(end_positions.value_in_unit(unit.nanometer))
    n_prot = assembled.n_protein_atoms
    atoms = list(assembled.complex_topology.atoms())

    ca_idx = [
        i
        for i, atom in enumerate(atoms[:n_prot])
        if atom.name == "CA" and atom.element is not None
    ]
    if ca_idx:
        delta = end[ca_idx] - start[ca_idx]
        rmsd = float(np.sqrt((delta**2).sum(axis=1).mean()))
    else:
        rmsd = 0.0

    masses = np.array(
        [
            atom.element.mass.value_in_unit(unit.dalton) if atom.element is not None else 0.0
            for atom in atoms
        ]
    )

    rg_start = _radius_of_gyration(start[:n_prot], masses[:n_prot])
    rg_end = _radius_of_gyration(end[:n_prot], masses[:n_prot])
    poly_rg = _radius_of_gyration(end[n_prot:], masses[n_prot:])

    poly = end[n_prot:]
    if poly.shape[0] >= 2:
        centre = poly.mean(axis=0)
        radii = np.linalg.norm(poly - centre, axis=1)
        far = int(np.argmax(radii))
        end_to_end = float(np.linalg.norm(poly - poly[far], axis=1).max())
    else:
        end_to_end = 0.0

    return StructuralObservables(
        protein_ca_rmsd_nm=rmsd,
        protein_radius_of_gyration_nm=rg_end,
        protein_rg_change_nm=rg_end - rg_start,
        polymer_radius_of_gyration_nm=poly_rg,
        polymer_end_to_end_nm=end_to_end,
    )


def supported_observables() -> list[dict[str, str]]:
    """Catalogue of what this module reports, and what it does not."""
    return [
        {
            "name": "interaction_energy",
            "units": "kJ/mol",
            "reports": "single-point protein-polymer interaction potential energy",
            "does_not_report": "binding free energy, Kd, shelf life, stabilisation",
        },
        {
            "name": "n_heavy_contacts",
            "units": "count",
            "reports": f"heavy-atom pairs within {CONTACT_CUTOFF_NM} nm across the interface",
            "does_not_report": "binding site identity or affinity",
        },
        {
            "name": "n_hydrogen_bonds",
            "units": "count",
            "reports": "geometric cross-interface hydrogen bonds",
            "does_not_report": "hydrogen-bond energies or occupancy over time",
        },
        {
            "name": "protein_ca_rmsd",
            "units": "nm",
            "reports": "C-alpha displacement over the trajectory",
            "does_not_report": "unfolding, melting temperature or aggregation",
        },
        {
            "name": "radius_of_gyration",
            "units": "nm",
            "reports": "compactness of protein and polymer at the final frame",
            "does_not_report": "hydrodynamic radius or solution conformation",
        },
    ]


def unsupported_observables() -> list[dict[str, str]]:
    """Quantities a researcher may expect that this prototype does not produce."""
    return [
        {
            "name": "binding free energy",
            "why_not": "requires free-energy methods (FEP, TI, MM/PBSA with entropy) "
            "and far longer sampling than implemented",
        },
        {
            "name": "aggregation propensity",
            "why_not": "requires multi-protein simulations at formulation "
            "concentration; only one protein copy is simulated",
        },
        {
            "name": "melting temperature",
            "why_not": "requires temperature-replica sampling and an unfolding "
            "transition, neither of which is implemented",
        },
        {
            "name": "shelf life",
            "why_not": "a stability-indicating experimental measurement over months; "
            "no computation here is a proxy for it",
        },
        {
            "name": "viscosity",
            "why_not": "requires explicit-solvent simulations at formulation "
            "concentration with transport-property analysis",
        },
    ]

"""Assemble a protein + polymer OpenMM system.

The protein is typed with Amber14 (bundled with OpenMM). The polymer is typed
with MMFF94 via :mod:`biologix.simulation.parameterise`. The two parameter
sets are then combined in one :class:`openmm.System`.

**This mixes force fields.** Amber14 and MMFF94 were not derived together and
their combination rules differ. Cross terms between protein and polymer use
OpenMM's Lorentz-Berthelot combination of the two sets' Lennard-Jones
parameters. That makes the resulting interaction energies useful for ranking
candidates *against each other* under identical settings, and not useful as
absolute energetics. Recorded in every manifest as ``mixed_force_field``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from biologix.errors import ValidationFailure
from biologix.simulation.parameterise import PolymerParameters, obc_parameters

# Amber14 protein parameters ship with OpenMM. Water is only needed for the
# explicit-solvent path, which is not implemented here.
PROTEIN_FORCEFIELD = "amber14-all.xml"

SOLVENT_MODELS = ("vacuum", "implicit-obc2")


@dataclass
class AssembledSystem:
    """A complex system plus the isolated parts needed for interaction energy."""

    complex_system: Any
    complex_topology: Any
    complex_positions: Any

    protein_system: Any
    polymer_system: Any
    n_protein_atoms: int
    n_polymer_atoms: int

    solvent: str
    nonbonded_cutoff_nm: float
    constrained_hydrogens: bool
    protein_forcefield: str = PROTEIN_FORCEFIELD
    polymer_forcefield: str = "MMFF94 (harmonic truncation)"
    placement: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def protein_indices(self) -> range:
        return range(self.n_protein_atoms)

    @property
    def polymer_indices(self) -> range:
        return range(self.n_protein_atoms, self.n_protein_atoms + self.n_polymer_atoms)

    def force_field_record(self) -> dict[str, Any]:
        return {
            "protein": self.protein_forcefield,
            "polymer": self.polymer_forcefield,
            "mixed_force_field": True,
            "mixing_caveat": (
                "Amber14 (protein) and MMFF94 (polymer) were not parameterised "
                "together. Protein-polymer cross terms use Lorentz-Berthelot "
                "combination of two independent Lennard-Jones sets. Interaction "
                "energies are comparable across candidates under identical "
                "settings; they are not absolute energetics."
            ),
            "solvent": self.solvent,
            "nonbonded_cutoff_nm": self.nonbonded_cutoff_nm,
            "constrained_hydrogens": self.constrained_hydrogens,
            "placement": self.placement,
            "notes": self.notes,
        }


def prepare_protein(
    pdb_path: str,
    *,
    ph: float = 7.0,
    keep_chains: tuple[str, ...] | None = None,
) -> tuple[Any, Any, dict[str, Any]]:
    """Load a PDB, strip solvent and hetero-residues, add hydrogens.

    Returns ``(topology, positions, record)``. Everything removed is listed in
    ``record`` rather than dropped silently.
    """
    import openmm.app as app

    pdb = app.PDBFile(pdb_path)
    modeller = app.Modeller(pdb.topology, pdb.positions)

    record: dict[str, Any] = {
        "source_chains": [
            {"id": c.id, "n_residues": sum(1 for _ in c.residues())}
            for c in pdb.topology.chains()
        ],
        "n_atoms_input": pdb.topology.getNumAtoms(),
        "ph_for_protonation": ph,
    }

    n_before = modeller.topology.getNumAtoms()
    modeller.deleteWater()
    record["n_water_atoms_removed"] = n_before - modeller.topology.getNumAtoms()

    standard = set(app.PDBFile._standardResidues)
    nonstandard = [r for r in modeller.topology.residues() if r.name not in standard]
    record["nonstandard_residues_removed"] = sorted({r.name for r in nonstandard})
    if nonstandard:
        modeller.delete(nonstandard)

    if keep_chains:
        unwanted = [c for c in modeller.topology.chains() if c.id not in keep_chains]
        if unwanted:
            record["chains_removed"] = [c.id for c in unwanted]
            modeller.delete(unwanted)

    forcefield = app.ForceField(PROTEIN_FORCEFIELD)
    modeller.addHydrogens(forcefield, pH=ph)
    record["n_atoms_after_hydrogens"] = modeller.topology.getNumAtoms()
    record["chains_kept"] = [
        {"id": c.id, "n_residues": sum(1 for _ in c.residues())}
        for c in modeller.topology.chains()
    ]
    record["protonation_note"] = (
        f"Hydrogens added by OpenMM Modeller at pH {ph}. Protonation states are "
        "fixed for the whole run; there is no titration, so pH is only "
        "represented through this initial assignment."
    )
    return modeller.topology, modeller.positions, record


def _polymer_only_system(
    polymer: PolymerParameters, *, solvent: str, cutoff_nm: float, constrain_h: bool
) -> Any:
    """A System containing just the polymer, for the interaction-energy term."""
    import openmm

    system = openmm.System()
    mol = polymer.mol
    for atom in mol.GetAtoms():
        system.addParticle(atom.GetMass())

    bond_force = openmm.HarmonicBondForce()
    angle_force = openmm.HarmonicAngleForce()
    torsion_force = openmm.PeriodicTorsionForce()
    nonbonded = openmm.NonbondedForce()
    nonbonded.setNonbondedMethod(openmm.NonbondedForce.CutoffNonPeriodic)
    nonbonded.setCutoffDistance(cutoff_nm)
    nonbonded.setUseDispersionCorrection(False)

    _add_polymer_terms(
        system,
        polymer,
        offset=0,
        bond_force=bond_force,
        angle_force=angle_force,
        torsion_force=torsion_force,
        nonbonded=nonbonded,
        constrain_h=constrain_h,
    )

    for force in (bond_force, angle_force, torsion_force, nonbonded):
        system.addForce(force)

    if solvent == "implicit-obc2":
        gb = openmm.GBSAOBCForce()
        gb.setNonbondedMethod(openmm.GBSAOBCForce.CutoffNonPeriodic)
        gb.setCutoffDistance(cutoff_nm)
        for i, (radius, scale) in enumerate(obc_parameters(mol)):
            gb.addParticle(polymer.charges[i], radius, scale)
        system.addForce(gb)
    return system


def _add_polymer_terms(
    system: Any,
    polymer: PolymerParameters,
    *,
    offset: int,
    bond_force: Any,
    angle_force: Any,
    torsion_force: Any,
    nonbonded: Any,
    constrain_h: bool,
) -> dict[str, int]:
    """Add the polymer's particles, bonded terms and exclusions to ``system``.

    ``offset`` is the index of the polymer's first particle, so this works
    both for a polymer-only system (offset 0) and for appending to a protein
    system.
    """
    mol = polymer.mol
    n_constraints = 0
    constrained: set[tuple[int, int]] = set()

    if constrain_h:
        for i, j, r0_nm, _k in polymer.bonds:
            zi = mol.GetAtomWithIdx(i).GetAtomicNum()
            zj = mol.GetAtomWithIdx(j).GetAtomicNum()
            if 1 in (zi, zj):
                system.addConstraint(offset + i, offset + j, r0_nm)
                constrained.add((min(i, j), max(i, j)))
                n_constraints += 1

    n_bonds = 0
    for i, j, r0_nm, k in polymer.bonds:
        if (min(i, j), max(i, j)) in constrained:
            # A constrained bond must not also carry a harmonic term.
            continue
        bond_force.addBond(offset + i, offset + j, r0_nm, k)
        n_bonds += 1

    for i, j, k, theta0, k_angle in polymer.angles:
        angle_force.addAngle(offset + i, offset + j, offset + k, theta0, k_angle)

    # i, j, k, l is the conventional naming for the four atoms of a dihedral in
    # molecular mechanics, so the ambiguous-name rule is suppressed rather than
    # renaming to something a force-field reader would not recognise.
    for i, j, k, l, periodicity, phase, amplitude in polymer.torsions:  # noqa: E741
        torsion_force.addTorsion(
            offset + i, offset + j, offset + k, offset + l, periodicity, phase, amplitude
        )

    for idx in range(mol.GetNumAtoms()):
        nonbonded.addParticle(
            polymer.charges[idx], polymer.sigmas_nm[idx], polymer.epsilons_kj[idx]
        )

    # Exclude 1-2 and 1-3 pairs. 1-4 pairs are left unscaled, per MMFF94.
    adjacency: dict[int, set[int]] = {a.GetIdx(): set() for a in mol.GetAtoms()}
    for bond in mol.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        adjacency[a].add(b)
        adjacency[b].add(a)

    excluded: set[tuple[int, int]] = set()
    for a, neighbours in adjacency.items():
        for b in neighbours:
            excluded.add((min(a, b), max(a, b)))
            for c in adjacency[b]:
                if c != a:
                    excluded.add((min(a, c), max(a, c)))

    for a, b in sorted(excluded):
        nonbonded.addException(offset + a, offset + b, 0.0, 0.1, 0.0, replace=True)

    return {
        "n_constraints": n_constraints,
        "n_harmonic_bonds": n_bonds,
        "n_exclusions": len(excluded),
    }


def _place_polymer(
    protein_positions: np.ndarray,
    polymer_positions: np.ndarray,
    *,
    gap_nm: float,
    direction_seed: int,
    clash_tolerance_nm: float = 0.22,
    max_pushes: int = 40,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Place the polymer against the protein *surface*, not outside its bounding sphere.

    A seeded protein surface atom is chosen as the contact site and the polymer
    is set along the outward radial direction through it, then pushed outward
    until no atom pair is closer than ``clash_tolerance_nm``. Placing relative
    to the bounding sphere instead would leave the polymer metres of simulated
    time away from an elongated protein, and a short trajectory would report
    zero contacts regardless of chemistry.

    Replicates differ in which surface site they start from, which is the
    point: one contact geometry is not a sample of the interface.
    """
    protein_centre = protein_positions.mean(axis=0)
    radii = np.linalg.norm(protein_positions - protein_centre, axis=1)
    protein_radius = float(radii.max())

    rng = np.random.default_rng(direction_seed)

    # Prefer genuinely surface-exposed atoms: those in the outer third of the
    # radial distribution. Interior atoms would give an unreachable site.
    threshold = float(np.quantile(radii, 0.66))
    surface = np.flatnonzero(radii >= threshold)
    if surface.size == 0:
        surface = np.arange(protein_positions.shape[0])
    site = int(rng.choice(surface))
    site_position = protein_positions[site]

    direction = site_position - protein_centre
    norm = np.linalg.norm(direction)
    if norm < 1e-6:
        direction = rng.normal(size=3)
        norm = np.linalg.norm(direction)
    direction = direction / norm

    polymer_centre = polymer_positions.mean(axis=0)
    polymer_radius = float(
        np.linalg.norm(polymer_positions - polymer_centre, axis=1).max()
    )

    # Start with the polymer centroid one polymer-radius plus the gap beyond
    # the chosen surface atom, then relieve any residual clash.
    offset = polymer_radius + gap_nm
    shifted = polymer_positions + (site_position + direction * offset - polymer_centre)

    pushes = 0
    separation = _min_separation(shifted, protein_positions)
    while separation < clash_tolerance_nm and pushes < max_pushes:
        shifted = shifted + direction * 0.05
        separation = _min_separation(shifted, protein_positions)
        pushes += 1

    return shifted, {
        "method": (
            "polymer placed along the outward radial direction through a seeded "
            "protein surface atom, then pushed outward until the minimum "
            "interatomic separation exceeded the clash tolerance"
        ),
        "contact_site_atom_index": site,
        "approach_direction": [round(float(d), 4) for d in direction],
        "direction_seed": direction_seed,
        "protein_radius_nm": round(protein_radius, 4),
        "polymer_radius_nm": round(polymer_radius, 4),
        "requested_gap_nm": gap_nm,
        "clash_tolerance_nm": clash_tolerance_nm,
        "clash_relief_pushes": pushes,
        "initial_min_separation_nm": round(separation, 4),
        "replicate_note": (
            "Replicates start from different surface sites. Interface observables "
            "should be read across replicates, never from one."
        ),
    }


def _min_separation(a: np.ndarray, b: np.ndarray) -> float:
    """Smallest distance between any atom of ``a`` and any atom of ``b``."""
    return float(np.linalg.norm(a[:, None, :] - b[None, :, :], axis=-1).min())


def replace_polymer_positions(
    assembled: AssembledSystem, *, direction_seed: int, gap_nm: float = 0.30
) -> tuple[Any, dict[str, Any]]:
    """New starting positions with the polymer at a different surface site.

    The System, Topology and all parameters are identical across replicates;
    only the initial placement changes. Reusing them avoids re-running PDB
    parsing and ``createSystem`` for every replicate, which dominated the
    per-replicate cost.
    """
    import openmm.unit as unit

    xyz = np.array(assembled.complex_positions.value_in_unit(unit.nanometer))
    n_protein = assembled.n_protein_atoms
    protein_xyz = xyz[:n_protein]
    polymer_xyz = xyz[n_protein:]

    placed, placement = _place_polymer(
        protein_xyz, polymer_xyz, gap_nm=gap_nm, direction_seed=direction_seed
    )
    return np.vstack([protein_xyz, placed]) * unit.nanometer, placement


def assemble(
    pdb_path: str,
    polymer: PolymerParameters,
    *,
    solvent: str = "implicit-obc2",
    ph: float = 7.0,
    cutoff_nm: float = 1.0,
    # 0.30 nm puts the nearest polymer atom inside the 0.40 nm contact cutoff
    # without clashing, so a short trajectory starts from a real interface.
    gap_nm: float = 0.30,
    constrain_hydrogens: bool = True,
    direction_seed: int = 20260101,
    keep_chains: tuple[str, ...] | None = None,
) -> AssembledSystem:
    """Build the complex system and the two isolated reference systems."""
    if solvent not in SOLVENT_MODELS:
        raise ValidationFailure(
            f"solvent must be one of {SOLVENT_MODELS}, got {solvent!r}"
        )
    if not polymer.report.ok:
        raise ValidationFailure(
            "refusing to assemble a system from a polymer that failed "
            f"parameterisation: {polymer.report.failures}"
        )

    import openmm
    import openmm.app as app
    import openmm.unit as unit

    topology, positions, protein_record = prepare_protein(
        pdb_path, ph=ph, keep_chains=keep_chains
    )

    forcefield = app.ForceField(PROTEIN_FORCEFIELD)
    constraints = app.HBonds if constrain_hydrogens else None
    protein_system = forcefield.createSystem(
        topology,
        nonbondedMethod=app.CutoffNonPeriodic,
        nonbondedCutoff=cutoff_nm * unit.nanometer,
        constraints=constraints,
        rigidWater=False,
    )
    n_protein = protein_system.getNumParticles()

    # The complex system starts as a fresh protein system so that mutating it
    # cannot disturb the reference one.
    complex_system = forcefield.createSystem(
        topology,
        nonbondedMethod=app.CutoffNonPeriodic,
        nonbondedCutoff=cutoff_nm * unit.nanometer,
        constraints=constraints,
        rigidWater=False,
    )

    bond_force = angle_force = torsion_force = nonbonded = None
    for force in complex_system.getForces():
        if isinstance(force, openmm.HarmonicBondForce) and bond_force is None:
            bond_force = force
        elif isinstance(force, openmm.HarmonicAngleForce) and angle_force is None:
            angle_force = force
        elif isinstance(force, openmm.PeriodicTorsionForce) and torsion_force is None:
            torsion_force = force
        elif isinstance(force, openmm.NonbondedForce) and nonbonded is None:
            nonbonded = force

    if nonbonded is None:
        raise ValidationFailure("protein system has no NonbondedForce; cannot assemble")
    for name, force in (
        ("HarmonicBondForce", bond_force),
        ("HarmonicAngleForce", angle_force),
        ("PeriodicTorsionForce", torsion_force),
    ):
        if force is None:
            raise ValidationFailure(f"protein system has no {name}; cannot assemble")

    mol = polymer.mol
    for atom in mol.GetAtoms():
        complex_system.addParticle(atom.GetMass())

    counts = _add_polymer_terms(
        complex_system,
        polymer,
        offset=n_protein,
        bond_force=bond_force,
        angle_force=angle_force,
        torsion_force=torsion_force,
        nonbonded=nonbonded,
        constrain_h=constrain_hydrogens,
    )

    # Implicit solvent covers every particle, so it is added after the
    # polymer rather than being baked into createSystem.
    notes: list[str] = []
    if solvent == "implicit-obc2":
        gb = openmm.GBSAOBCForce()
        gb.setNonbondedMethod(openmm.GBSAOBCForce.CutoffNonPeriodic)
        gb.setCutoffDistance(cutoff_nm)
        protein_nonbonded = nonbonded
        protein_elements = _element_list(topology)
        for i in range(n_protein):
            charge, _sigma, _eps = protein_nonbonded.getParticleParameters(i)
            # Amber14 protein radii/scales by element, same table as the polymer.
            radius, scale = _obc_for_element(protein_elements[i])
            gb.addParticle(charge, radius, scale)
        for i, (radius, scale) in enumerate(obc_parameters(mol)):
            gb.addParticle(polymer.charges[i], radius, scale)
        complex_system.addForce(gb)
        notes.append(
            "Implicit solvent is OBC2 (GBSAOBCForce) with element-based radii "
            "applied uniformly to protein and polymer. Salt screening is not "
            "included: GBSAOBCForce has no ionic-strength term, so buffer "
            "ionic strength is recorded metadata only."
        )
    else:
        notes.append(
            "Vacuum: no solvent model. Electrostatic interactions are "
            "unscreened and will be far stronger than in water."
        )

    # Reference systems for the interaction-energy decomposition.
    polymer_system = _polymer_only_system(
        polymer, solvent=solvent, cutoff_nm=cutoff_nm, constrain_h=constrain_hydrogens
    )
    if solvent == "implicit-obc2":
        gb_prot = openmm.GBSAOBCForce()
        gb_prot.setNonbondedMethod(openmm.GBSAOBCForce.CutoffNonPeriodic)
        gb_prot.setCutoffDistance(cutoff_nm)
        prot_nb = next(
            f for f in protein_system.getForces() if isinstance(f, openmm.NonbondedForce)
        )
        protein_elements = _element_list(topology)
        for i in range(n_protein):
            charge, _s, _e = prot_nb.getParticleParameters(i)
            radius, scale = _obc_for_element(protein_elements[i])
            gb_prot.addParticle(charge, radius, scale)
        protein_system.addForce(gb_prot)

    # Positions: protein as loaded, polymer translated into place.
    protein_xyz = np.array(positions.value_in_unit(unit.nanometer))
    polymer_xyz = np.array(polymer.positions_nm())
    placed, placement = _place_polymer(
        protein_xyz, polymer_xyz, gap_nm=gap_nm, direction_seed=direction_seed
    )
    placement.update(protein_record)
    placement.update(counts)

    merged_topology = _merge_topology(topology, mol)
    merged_positions = np.vstack([protein_xyz, placed]) * unit.nanometer

    return AssembledSystem(
        complex_system=complex_system,
        complex_topology=merged_topology,
        complex_positions=merged_positions,
        protein_system=protein_system,
        polymer_system=polymer_system,
        n_protein_atoms=n_protein,
        n_polymer_atoms=mol.GetNumAtoms(),
        solvent=solvent,
        nonbonded_cutoff_nm=cutoff_nm,
        constrained_hydrogens=constrain_hydrogens,
        placement=placement,
        notes=notes,
    )


def _element_list(topology: Any) -> list[int]:
    """Atomic numbers of every particle in an OpenMM topology, in order."""
    return [
        atom.element.atomic_number if atom.element is not None else 0
        for atom in topology.atoms()
    ]


def _obc_for_element(z: int) -> tuple[float, float]:
    from biologix.simulation.parameterise import (
        DEFAULT_OBC_RADIUS_NM,
        DEFAULT_OBC_SCALE,
        OBC_RADII_NM,
        OBC_SCALES,
    )

    return OBC_RADII_NM.get(z, DEFAULT_OBC_RADIUS_NM), OBC_SCALES.get(z, DEFAULT_OBC_SCALE)


def _merge_topology(protein_topology: Any, polymer_mol: Any) -> Any:
    """Topology covering protein and polymer, for trajectory output."""
    import openmm.app as app

    merged = app.Topology()
    chain_map: dict[Any, Any] = {}
    atom_map: dict[Any, Any] = {}

    for chain in protein_topology.chains():
        new_chain = merged.addChain(chain.id)
        chain_map[chain] = new_chain
        for residue in chain.residues():
            new_residue = merged.addResidue(residue.name, new_chain, residue.id)
            for atom in residue.atoms():
                atom_map[atom] = merged.addAtom(atom.name, atom.element, new_residue)
    for bond in protein_topology.bonds():
        merged.addBond(atom_map[bond[0]], atom_map[bond[1]])

    poly_chain = merged.addChain("P")
    poly_residue = merged.addResidue("POL", poly_chain)
    poly_atoms = []
    for atom in polymer_mol.GetAtoms():
        element = app.Element.getByAtomicNumber(atom.GetAtomicNum())
        poly_atoms.append(
            merged.addAtom(f"{atom.GetSymbol()}{atom.GetIdx()}", element, poly_residue)
        )
    for bond in polymer_mol.GetBonds():
        merged.addBond(poly_atoms[bond.GetBeginAtomIdx()], poly_atoms[bond.GetEndAtomIdx()])
    return merged

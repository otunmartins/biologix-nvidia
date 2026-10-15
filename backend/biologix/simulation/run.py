"""Run minimisation and dynamics, and report observables with full settings.

Minimisation and dynamics are separate, reported separately, and a run can
stop after minimisation. Replicates differ in both initial polymer placement
and velocity seed, and their spread is reported: a single trajectory of a
protein-polymer system tells you very little, and reporting one number as if
it were converged would be the main way this module could mislead.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from biologix import EVIDENCE_COMPUTED
from biologix.errors import JobCancelled, ValidationFailure
from biologix.polymers.models import FormulationConditions, PolymerCandidate
from biologix.polymers.oligomer import build_oligomer
from biologix.provenance import RunManifest
from biologix.simulation.assemble import assemble, replace_polymer_positions
from biologix.simulation.observables import (
    contact_observables,
    interaction_energy,
    structural_observables,
    supported_observables,
    unsupported_observables,
)
from biologix.simulation.parameterise import parameterise_polymer
from biologix.simulation.platforms import get_platform, platform_properties


@dataclass
class SimulationSettings:
    """Every setting that affects the numbers, recorded verbatim."""

    temperature_k: float = 298.15
    friction_per_ps: float = 1.0
    timestep_fs: float = 2.0
    minimisation_max_iterations: int = 500
    minimisation_tolerance_kj_per_mol: float = 10.0
    equilibration_steps: int = 2_000
    production_steps: int = 10_000
    report_interval: int = 1_000
    solvent: str = "implicit-obc2"
    nonbonded_cutoff_nm: float = 1.0
    constrain_hydrogens: bool = True
    ph: float = 7.0
    initial_gap_nm: float = 0.30
    n_replicates: int = 3
    base_seed: int = 20260101
    oligomer_dp: int | None = None
    # Positional restraints on protein heavy atoms, in kJ/mol/nm^2. Zero means
    # the protein is free.
    protein_restraint_k: float = 0.0
    # Flat-bottom centroid tether holding the polymer near the protein, in
    # kJ/mol/nm^2, acting only beyond ``polymer_tether_distance_nm``.
    #
    # This BIASES the sampling. Without it, an unbound polymer diffuses away
    # within a few picoseconds and interface observables become dominated by
    # whether it happened to stay, which is a property of the run length, not
    # of the chemistry. With it, the polymer is kept in the interaction region
    # so that candidates are compared on interface quality under the same
    # constraint. The bias is recorded in every manifest and report; an
    # interaction energy obtained under a tether is NOT evidence of
    # spontaneous binding.
    polymer_tether_k: float = 0.0
    polymer_tether_distance_nm: float = 1.5

    @property
    def production_time_ps(self) -> float:
        return self.production_steps * self.timestep_fs / 1000.0

    @property
    def total_time_ps(self) -> float:
        return (self.equilibration_steps + self.production_steps) * self.timestep_fs / 1000.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["production_time_ps"] = self.production_time_ps
        d["total_time_ps"] = self.total_time_ps
        d["timescale_caveat"] = (
            f"Total simulated time per replicate is {self.total_time_ps:.1f} ps. "
            "Protein conformational change, aggregation and excipient exchange "
            "occur on microsecond-to-month timescales. This trajectory samples "
            "local interface relaxation only."
        )
        d["sampling_bias"] = (
            "A flat-bottom centroid tether of "
            f"{self.polymer_tether_k:g} kJ/mol/nm^2 beyond "
            f"{self.polymer_tether_distance_nm:g} nm holds the polymer in the "
            "interaction region. Interaction energies obtained this way measure "
            "interface quality under a constraint; they are NOT evidence that "
            "the polymer binds spontaneously."
            if self.polymer_tether_k > 0
            else "No tether. An unbound polymer may diffuse away from the "
            "protein, in which case interface observables approach zero and the "
            "replicate spread reflects escape rather than chemistry."
        )
        return d


@dataclass
class ReplicateResult:
    """One replicate's outcome."""

    replicate: int
    seed: int
    direction_seed: int
    actual_platform: str
    minimised_energy_kj: float
    initial_energy_kj: float
    interaction_energy: dict[str, Any] = field(default_factory=dict)
    contacts: dict[str, Any] = field(default_factory=dict)
    structure: dict[str, Any] = field(default_factory=dict)
    minimisation_seconds: float = 0.0
    dynamics_seconds: float = 0.0
    placement: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimulationResult:
    """Aggregated result over replicates."""

    candidate_id: str
    biologic: str
    settings: dict[str, Any]
    replicates: list[ReplicateResult]
    oligomer: dict[str, Any]
    parameterisation: dict[str, Any]
    force_field: dict[str, Any]
    modelled_conditions: dict[str, str]
    manifest: RunManifest
    observable_catalogue: dict[str, Any] = field(default_factory=dict)

    @property
    def successful(self) -> list[ReplicateResult]:
        return [r for r in self.replicates if r.ok]

    def summary(self) -> dict[str, Any]:
        """Mean and spread across replicates, or an explicit failure."""
        good = self.successful
        if not good:
            return {
                "status": "failed",
                "n_replicates": len(self.replicates),
                "n_successful": 0,
                "errors": [r.error for r in self.replicates],
            }

        def spread(values: list[float]) -> dict[str, float | None]:
            arr = np.array(values, dtype=float)
            return {
                "mean": round(float(arr.mean()), 3),
                "std": round(float(arr.std(ddof=1)), 3) if arr.size > 1 else None,
                "min": round(float(arr.min()), 3),
                "max": round(float(arr.max()), 3),
                "n": int(arr.size),
            }

        energies = [r.interaction_energy.get("total_kj_per_mol", float("nan")) for r in good]
        contacts = [float(r.contacts.get("n_heavy_contacts", 0)) for r in good]
        hbonds = [float(r.contacts.get("n_hydrogen_bonds", 0)) for r in good]
        rmsd = [float(r.structure.get("protein_ca_rmsd_nm", 0.0)) for r in good]
        min_dist = [float(r.contacts.get("min_distance_nm", 0.0)) for r in good]

        out = {
            "status": "ok",
            "n_replicates": len(self.replicates),
            "n_successful": len(good),
            "interaction_energy_kj_per_mol": spread(energies),
            "n_heavy_contacts": spread(contacts),
            "n_hydrogen_bonds": spread(hbonds),
            "protein_ca_rmsd_nm": spread(rmsd),
            "min_distance_nm": spread(min_dist),
            "actual_platforms": sorted({r.actual_platform for r in good}),
            "evidence": EVIDENCE_COMPUTED,
        }
        std = out["interaction_energy_kj_per_mol"]["std"]
        mean = out["interaction_energy_kj_per_mol"]["mean"]
        if std is not None and mean not in (0, None):
            out["replicate_spread_warning"] = (
                f"Replicate standard deviation is {std:.1f} kJ/mol against a mean "
                f"of {mean:.1f} kJ/mol. "
                + (
                    "The spread is comparable to or larger than the mean, so "
                    "candidate ranking on this observable is not resolved by "
                    f"{len(good)} replicates."
                    if abs(std) > 0.5 * abs(mean)
                    else "Treat differences between candidates smaller than this "
                    "spread as unresolved."
                )
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "biologic": self.biologic,
            "settings": self.settings,
            "summary": self.summary(),
            "replicates": [r.to_dict() for r in self.replicates],
            "oligomer": self.oligomer,
            "parameterisation": self.parameterisation,
            "force_field": self.force_field,
            "modelled_conditions": self.modelled_conditions,
            "observable_catalogue": self.observable_catalogue,
            "manifest": self.manifest.to_dict(),
        }


ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


def simulate_candidate(
    candidate: PolymerCandidate,
    pdb_path: str,
    conditions: FormulationConditions,
    *,
    settings: SimulationSettings | None = None,
    biologic_name: str = "unknown",
    platform_request: str | None = None,
    progress: ProgressCallback | None = None,
    cancelled: CancelCheck | None = None,
    write_trajectory: str | None = None,
    write_frames_pdb: str | None = None,
) -> SimulationResult:
    """Simulate one candidate against one target, over replicates.

    ``platform_request`` is passed straight through: an explicit ``"CUDA"``
    raises :class:`~biologix.errors.PlatformUnavailable` if CUDA is absent,
    rather than producing CPU numbers under a GPU label.

    ``write_trajectory`` writes a DCD for analysis; ``write_frames_pdb`` writes
    the same frames as a multi-model PDB, which is what a structure viewer can
    read directly. Both come from the first replicate only.
    """
    import openmm
    import openmm.app as app
    import openmm.unit as unit

    settings = settings or SimulationSettings()
    settings.temperature_k = conditions.temperature_k
    if conditions.ph is not None:
        settings.ph = conditions.ph

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise JobCancelled("simulation cancelled by request")

    platform, platform_record = get_platform(platform_request)
    properties = platform_properties(platform_record["actual"])

    manifest = RunManifest(
        operation="simulate",
        evidence=EVIDENCE_COMPUTED,
        inputs={
            "candidate_id": candidate.candidate_id,
            "candidate": candidate.display_name(),
            "repeat_units": [b.unit.smiles for b in candidate.blocks],
            "biologic": biologic_name,
            "structure_path": pdb_path,
            "conditions": conditions.model_dump(mode="json"),
        },
        parameters={
            "settings": settings.to_dict(),
            "platform_properties": properties,
        },
        seeds={"base_seed": settings.base_seed},
        requested_backend=platform_record["requested"],
        actual_backend=platform_record["actual"],
    )
    manifest.parameters["platform_record"] = platform_record

    report(0.02, "building oligomer")
    check_cancelled()
    dp = settings.oligomer_dp
    if dp is not None:
        trimmed = candidate.model_copy(
            update={
                "chain_length": candidate.chain_length.model_copy(
                    update={"degree_of_polymerisation": dp}
                )
            }
        )
        build = build_oligomer(trimmed)
        build.construction_rules["dp_override"] = (
            f"simulation used DP={dp} instead of the requested "
            f"DP={candidate.chain_length.degree_of_polymerisation} to keep the "
            "system size tractable"
        )
    else:
        build = build_oligomer(candidate)

    report(0.08, "parameterising polymer")
    check_cancelled()
    polymer = parameterise_polymer(build, seed=settings.base_seed, strict=True)
    manifest.parameters["parameterisation"] = polymer.report.to_dict()

    # The System, Topology and parameters are identical for every replicate;
    # only the initial polymer placement and the velocity seed differ. So the
    # system is assembled once and re-placed per replicate, which removes a
    # PDB parse and a createSystem call from each one.
    report(0.12, "assembling system")
    check_cancelled()
    assembled = assemble(
        pdb_path,
        polymer,
        solvent=settings.solvent,
        ph=settings.ph,
        cutoff_nm=settings.nonbonded_cutoff_nm,
        gap_nm=settings.initial_gap_nm,
        constrain_hydrogens=settings.constrain_hydrogens,
        direction_seed=settings.base_seed + 500_000,
    )
    system = assembled.complex_system
    if settings.protein_restraint_k > 0:
        _add_protein_restraints(system, assembled, settings.protein_restraint_k)
    if settings.polymer_tether_k > 0:
        _add_polymer_tether(
            system,
            assembled,
            settings.polymer_tether_k,
            settings.polymer_tether_distance_nm,
        )
        manifest.warn(
            "a flat-bottom polymer tether is active; interaction energies are "
            "conditional on that constraint and are not evidence of spontaneous "
            "binding"
        )

    replicates: list[ReplicateResult] = []
    placements: list[dict[str, Any]] = []

    for replicate_index in range(settings.n_replicates):
        check_cancelled()
        seed = settings.base_seed + replicate_index * 1000
        direction_seed = settings.base_seed + 500_000 + replicate_index
        base_fraction = 0.15 + 0.80 * (replicate_index / max(settings.n_replicates, 1))
        report(
            base_fraction,
            f"replicate {replicate_index + 1}/{settings.n_replicates}: placing polymer",
        )

        result = ReplicateResult(
            replicate=replicate_index,
            seed=seed,
            direction_seed=direction_seed,
            actual_platform=platform_record["actual"],
            minimised_energy_kj=float("nan"),
            initial_energy_kj=float("nan"),
        )

        try:
            start, placement = replace_polymer_positions(
                assembled,
                direction_seed=direction_seed,
                gap_nm=settings.initial_gap_nm,
            )
            placement["replicate"] = replicate_index
            placements.append(placement)
            result.placement = placement

            integrator = openmm.LangevinMiddleIntegrator(
                settings.temperature_k * unit.kelvin,
                settings.friction_per_ps / unit.picosecond,
                settings.timestep_fs * unit.femtosecond,
            )
            integrator.setRandomNumberSeed(seed)

            simulation = app.Simulation(
                assembled.complex_topology, system, integrator, platform, properties
            )
            simulation.context.setPositions(start)

            state0 = simulation.context.getState(getEnergy=True)
            result.initial_energy_kj = float(
                state0.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
            )

            # --- minimisation, kept separate from dynamics ----------------
            check_cancelled()
            t0 = time.perf_counter()
            simulation.minimizeEnergy(
                maxIterations=settings.minimisation_max_iterations,
                tolerance=settings.minimisation_tolerance_kj_per_mol
                * unit.kilojoule_per_mole
                / unit.nanometer,
            )
            result.minimisation_seconds = round(time.perf_counter() - t0, 3)
            minimised = simulation.context.getState(getEnergy=True, getPositions=True)
            result.minimised_energy_kj = float(
                minimised.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
            )
            start_positions = minimised.getPositions()

            # --- dynamics -------------------------------------------------
            report(base_fraction + 0.02, f"replicate {replicate_index + 1}: dynamics")
            check_cancelled()
            simulation.context.setVelocitiesToTemperature(
                settings.temperature_k * unit.kelvin, seed
            )
            if write_trajectory and replicate_index == 0:
                simulation.reporters.append(
                    app.DCDReporter(write_trajectory, settings.report_interval)
                )
            if write_frames_pdb and replicate_index == 0:
                # A multi-model PDB alongside the DCD: every frame carries its
                # own topology, so it can be read and animated by a viewer that
                # knows nothing about this project. Only the first replicate is
                # written — a single trajectory is an illustration, and the
                # numbers still come from the spread across replicates.
                simulation.reporters.append(
                    app.PDBReporter(
                        write_frames_pdb, settings.report_interval, enforcePeriodicBox=False
                    )
                )

            t0 = time.perf_counter()
            if settings.equilibration_steps:
                simulation.step(settings.equilibration_steps)
            check_cancelled()
            remaining = settings.production_steps
            chunk = max(settings.report_interval, 1)
            while remaining > 0:
                check_cancelled()
                step = min(chunk, remaining)
                simulation.step(step)
                remaining -= step
            result.dynamics_seconds = round(time.perf_counter() - t0, 3)

            final = simulation.context.getState(getPositions=True, getEnergy=True)
            final_positions = final.getPositions()

            result.actual_platform = simulation.context.getPlatform().getName()
            if (
                platform_record["explicit_request"]
                and result.actual_platform.lower() != platform_record["requested"].lower()
            ):
                raise ValidationFailure(
                    f"platform {platform_record['requested']!r} was requested but "
                    f"OpenMM ran on {result.actual_platform!r}"
                )

            result.interaction_energy = interaction_energy(
                assembled, final_positions, platform
            ).to_dict()
            result.contacts = contact_observables(assembled, final_positions).to_dict()
            result.structure = structural_observables(
                assembled, start_positions, final_positions
            ).to_dict()

            del simulation
            del integrator

        except JobCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            result.error = f"{type(exc).__name__}: {exc}"
            manifest.fail(f"replicate {replicate_index}: {result.error}")

        replicates.append(result)

    report(0.98, "aggregating")

    modelled = conditions.modelled_fields(
        solvent=settings.solvent,
        ph_method="OpenMM Modeller.addHydrogens" if settings.ph else None,
    )

    manifest.parameters["replicate_placements"] = placements
    manifest.finish(
        n_replicates=len(replicates),
        n_successful=sum(1 for r in replicates if r.ok),
        mean_interaction_energy_kj=(
            round(
                float(
                    np.mean(
                        [
                            r.interaction_energy["total_kj_per_mol"]
                            for r in replicates
                            if r.ok and r.interaction_energy
                        ]
                    )
                ),
                3,
            )
            if any(r.ok and r.interaction_energy for r in replicates)
            else None
        ),
    )
    manifest.record_model(
        "amber14-all.xml", "openmm-bundled", kind="protein-forcefield", licence="MIT (OpenMM)"
    )
    manifest.record_model(
        "MMFF94", "rdkit-" + _rdkit_version(), kind="polymer-forcefield", licence="BSD-3 (RDKit)"
    )

    return SimulationResult(
        candidate_id=candidate.candidate_id,
        biologic=biologic_name,
        settings=settings.to_dict(),
        replicates=replicates,
        oligomer=build.provenance(),
        parameterisation=polymer.report.to_dict(),
        force_field=assembled.force_field_record(),
        modelled_conditions=modelled,
        manifest=manifest,
        observable_catalogue={
            "supported": supported_observables(),
            "not_supported": unsupported_observables(),
        },
    )


def _add_protein_restraints(system: Any, assembled: Any, k: float) -> None:
    """Harmonic positional restraints on protein heavy atoms."""
    import openmm
    import openmm.unit as unit

    force = openmm.CustomExternalForce("0.5*k_restr*((x-x0)^2+(y-y0)^2+(z-z0)^2)")
    force.addGlobalParameter("k_restr", k * unit.kilojoule_per_mole / unit.nanometer**2)
    for name in ("x0", "y0", "z0"):
        force.addPerParticleParameter(name)

    positions = np.array(assembled.complex_positions.value_in_unit(unit.nanometer))
    atoms = list(assembled.complex_topology.atoms())
    for i in range(assembled.n_protein_atoms):
        atom = atoms[i]
        if atom.element is None or atom.element.atomic_number <= 1:
            continue
        force.addParticle(i, positions[i].tolist())
    system.addForce(force)


def _add_polymer_tether(
    system: Any, assembled: Any, k: float, flat_bottom_nm: float
) -> None:
    """Flat-bottom centroid restraint between protein and polymer.

    Zero force while the centroids are within ``flat_bottom_nm``, harmonic
    beyond it. This keeps an unbound polymer in the interaction region without
    pushing it onto the surface, so the interface geometry it adopts is still
    determined by the force field.
    """
    import openmm

    force = openmm.CustomCentroidBondForce(
        2, "0.5*k_tether*max(0, distance(g1,g2)-r0_tether)^2"
    )
    force.addGlobalParameter("k_tether", k)
    force.addGlobalParameter("r0_tether", flat_bottom_nm)

    atoms = list(assembled.complex_topology.atoms())
    protein_heavy = [
        i
        for i in assembled.protein_indices
        if atoms[i].element is not None and atoms[i].element.atomic_number > 1
    ]
    polymer_heavy = [
        i
        for i in assembled.polymer_indices
        if atoms[i].element is not None and atoms[i].element.atomic_number > 1
    ]
    if not protein_heavy or not polymer_heavy:
        return
    force.addGroup(protein_heavy)
    force.addGroup(polymer_heavy)
    force.addBond([0, 1], [])
    system.addForce(force)


def _rdkit_version() -> str:
    try:
        import rdkit

        return rdkit.__version__
    except ImportError:  # pragma: no cover
        return "unknown"

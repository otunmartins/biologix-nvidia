"""Per-candidate NVIDIA work, run as tracked jobs.

The two jobs here are the expensive half of the platform, and the reason the
job table exists: a DiffDock pose takes seconds to minutes and a Boltz-2
complex longer, so neither can run inside a request.

Both are deliberately thin wrappers over the adapters. What they add is the
part a user interface needs:

* progress a user can watch, and a terminal state either way;
* a refusal stored with its remedy, so an unavailable adapter produces a job
  that says what to do rather than a job that vanished;
* the caveats stored *with the result*, because a docking confidence or an
  affinity figure that arrives without them is the exact failure mode this
  repository exists to avoid.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from biologix.errors import BiologixError
from biologix.jobs.artifacts import (
    copy_artifact,
    count_models,
    job_directory,
    write_artifact,
)
from biologix.settings import Settings, get_settings
from biologix.store import fail_job, finish_job, set_job_progress, start_job
from biologix.store.workspace import log

JOB_KINDS = ("dock", "affinity", "simulate")

KIND_LABEL = {
    "dock": "DiffDock pose",
    "affinity": "Boltz-2 complex and affinity",
    "simulate": "OpenMM molecular dynamics",
}


def _candidate_from_smiles(repeat_unit_smiles: str, dp: int) -> Any:
    from biologix.polymers.generate import candidates_from_smiles

    imported = candidates_from_smiles([repeat_unit_smiles], dp=dp)
    if not imported.candidates:
        reason = next((r.rejection_detail for r in imported.records if r.rejection_detail), None)
        raise ValueError(f"{repeat_unit_smiles!r} is not a usable repeat unit: {reason}")
    return imported.candidates[0]


def run_dock(
    job_id: str,
    *,
    target_key: str,
    repeat_unit_smiles: str,
    degree_of_polymerisation: int = 10,
    num_poses: int = 5,
    settings: Settings | None = None,
) -> None:
    """DiffDock: blind docking poses as starting geometries for simulation."""
    s = settings or get_settings()
    url = s.database_url
    start_job(job_id, url=url)
    try:
        from biologix.nvidia.diffdock import dock_candidate
        from biologix.targets.prepare import prepare_target
        from biologix.targets.registry import get_target

        set_job_progress(job_id, stage="preparing the target structure", progress=0.2, url=url)
        biologic = get_target(target_key)
        candidate = _candidate_from_smiles(repeat_unit_smiles, degree_of_polymerisation)
        structure = prepare_target(biologic)

        set_job_progress(job_id, stage="docking with DiffDock", progress=0.5, url=url)
        result = dock_candidate(
            structure.path,
            candidate,
            num_poses=num_poses,
            dp_override=degree_of_polymerisation,
        )

        summary = result.summary()
        summary["target"] = biologic.name
        summary["evidence"] = "computed"
        # The coordinates themselves, so the pose can be looked at rather than
        # taken on trust from a confidence number.
        artifacts: list[dict[str, Any]] = []
        target_structure = copy_artifact(
            job_id,
            Path(structure.path),
            name="target.pdb",
            kind="target",
            structure_format="pdb",
            description=(
                f"{biologic.name} as prepared from PDB {structure.pdb_id}, the exact "
                "coordinates DiffDock was given"
            ),
            settings=s,
            extra={"pdb_id": structure.pdb_id, "sha256": structure.sha256},
        )
        if target_structure:
            artifacts.append(target_structure)
        for pose in result.poses:
            if not pose.structure:
                continue
            artifacts.append(
                write_artifact(
                    job_id,
                    name=f"pose-{pose.rank}.{pose.structure_format or 'sdf'}",
                    content=pose.structure,
                    kind="pose",
                    structure_format=pose.structure_format or "sdf",
                    description=(
                        f"pose {pose.rank} of {len(result.poses)}, DiffDock confidence "
                        f"{pose.confidence if pose.confidence is not None else 'unscored'}"
                    ),
                    settings=s,
                    extra={"rank": pose.rank, "confidence": pose.confidence},
                )
            )
        summary["artifacts"] = artifacts
        # Carried with the result, not alongside it: a confidence score that
        # travels without this sentence gets read as a binding strength.
        summary["caveat"] = (
            "A pose is a candidate starting geometry. DiffDock's confidence estimates "
            "whether the geometry is plausible, not how strongly anything binds, and it "
            "is not comparable between different ligands. Pose quality degrades with "
            "ligand flexibility, and an oligomer is far more flexible than the drug-like "
            "ligands DiffDock was trained on."
        )
        finish_job(job_id, summary, url=url)
    except BiologixError as exc:
        _record_refusal(job_id, exc, url=url)
    except (KeyError, ValueError) as exc:
        fail_job(
            job_id,
            error=str(exc.args[0] if exc.args else exc),
            error_kind=type(exc).__name__,
            remedy="check the target key and the repeat unit",
            url=url,
        )
    except Exception as exc:  # noqa: BLE001 - a worker records rather than vanishing
        fail_job(job_id, error=f"unexpected {type(exc).__name__}: {exc}", url=url)


def run_affinity(
    job_id: str,
    *,
    target_key: str,
    repeat_unit_smiles: str,
    degree_of_polymerisation: int = 10,
    settings: Settings | None = None,
) -> None:
    """Boltz-2: a predicted complex, and an affinity figure with its ceiling."""
    s = settings or get_settings()
    url = s.database_url
    start_job(job_id, url=url)
    try:
        from biologix.nvidia.boltz2 import predict_complex
        from biologix.targets.registry import get_target

        set_job_progress(job_id, stage="assembling the complex request", progress=0.2, url=url)
        biologic = get_target(target_key)
        candidate = _candidate_from_smiles(repeat_unit_smiles, degree_of_polymerisation)

        set_job_progress(job_id, stage="predicting with Boltz-2", progress=0.5, url=url)
        result = predict_complex(biologic, candidate, dp_override=degree_of_polymerisation)

        summary = result.summary()
        summary["target"] = biologic.name
        summary["evidence"] = "computed"
        summary["n_structures"] = len(result.structures)
        artifacts: list[dict[str, Any]] = []
        for index, predicted in enumerate(result.structures, start=1):
            if not predicted.content:
                continue
            raw_format = (predicted.format or "cif").lower()
            extension = "cif" if raw_format in ("cif", "mmcif") else raw_format
            artifacts.append(
                write_artifact(
                    job_id,
                    name=f"complex-{index}.{extension}",
                    content=predicted.content,
                    kind="complex",
                    structure_format=extension,
                    description=(
                        f"predicted complex {index} of {len(result.structures)} — "
                        f"{biologic.name} with the capped oligomer, as Boltz-2 returned it"
                    ),
                    settings=s,
                    extra={
                        "source_name": predicted.name,
                        "source": predicted.source,
                    },
                )
            )
        summary["artifacts"] = artifacts
        summary["caveat"] = (
            "Boltz-2's affinity head was trained on small-molecule protein–ligand "
            "binding, so a polymer oligomer is out of distribution. The value orders "
            "candidates under one fixed model: it is not a Kd, not an IC50, and not a "
            "stability prediction. Excipients act largely through colloidal and "
            "interfacial mechanisms rather than pocket binding."
        )
        finish_job(job_id, summary, url=url)
    except BiologixError as exc:
        _record_refusal(job_id, exc, url=url)
    except (KeyError, ValueError) as exc:
        fail_job(
            job_id,
            error=str(exc.args[0] if exc.args else exc),
            error_kind=type(exc).__name__,
            remedy="check the target key and the repeat unit",
            url=url,
        )
    except Exception as exc:  # noqa: BLE001
        fail_job(job_id, error=f"unexpected {type(exc).__name__}: {exc}", url=url)


def run_simulation(
    job_id: str,
    *,
    target_key: str,
    repeat_unit_smiles: str,
    degree_of_polymerisation: int = 8,
    production_steps: int = 6_000,
    n_replicates: int = 1,
    settings: Settings | None = None,
) -> None:
    """OpenMM molecular dynamics of the protein with the oligomer near it.

    The defaults here are short on purpose. A run of a few picoseconds on one
    replicate is an *illustration* of the complex, not a measurement of
    anything: aggregation happens on microsecond-to-month timescales, and a
    single trajectory of a protein-polymer system tells you very little. The
    result says so, and the spread across replicates — the only figure that
    would mean something — is reported as unavailable when only one was run.

    The polymer is held near the protein by a flat-bottom tether. Without it an
    unbound chain diffuses away within picoseconds and every interface number
    becomes a statement about run length rather than chemistry. That bias is
    recorded in the manifest and repeated in the result.
    """
    s = settings or get_settings()
    url = s.database_url
    start_job(job_id, url=url)
    try:
        from biologix.polymers.models import FormulationConditions
        from biologix.simulation.run import SimulationSettings, simulate_candidate
        from biologix.targets.prepare import prepare_target
        from biologix.targets.registry import get_target

        set_job_progress(job_id, stage="preparing the complex", progress=0.1, url=url)
        biologic = get_target(target_key)
        candidate = _candidate_from_smiles(repeat_unit_smiles, degree_of_polymerisation)
        structure = prepare_target(biologic)

        directory = job_directory(job_id, s)
        directory.mkdir(parents=True, exist_ok=True)
        frames_path = directory / "trajectory.pdb"

        simulation_settings = SimulationSettings(
            production_steps=production_steps,
            equilibration_steps=1_000,
            report_interval=max(production_steps // 40, 50),
            n_replicates=max(1, n_replicates),
            oligomer_dp=degree_of_polymerisation,
            # Keep the chain in the interaction region so the frames show a
            # complex rather than a polymer drifting into solvent.
            polymer_tether_k=500.0,
        )

        def progress(fraction: float, message: str) -> None:
            set_job_progress(job_id, stage=message, progress=0.15 + 0.8 * fraction, url=url)

        result = simulate_candidate(
            candidate,
            str(structure.path),
            FormulationConditions(),
            settings=simulation_settings,
            biologic_name=biologic.name,
            progress=progress,
            write_frames_pdb=str(frames_path),
        )

        summary = result.summary()
        summary["target"] = biologic.name
        summary["evidence"] = "computed"
        artifacts: list[dict[str, Any]] = []
        if frames_path.exists():
            content = frames_path.read_text(encoding="utf-8", errors="replace")
            artifacts.append(
                write_artifact(
                    job_id,
                    name="trajectory.pdb",
                    content=content,
                    kind="trajectory",
                    structure_format="pdb",
                    description=(
                        f"{count_models(content)} frames of {biologic.name} with the "
                        f"DP-{degree_of_polymerisation} oligomer, implicit solvent, "
                        f"{simulation_settings.production_time_ps:.1f} ps of production "
                        "dynamics from the first replicate"
                    ),
                    settings=s,
                    extra={
                        "n_frames": count_models(content),
                        "picoseconds": round(simulation_settings.production_time_ps, 2),
                    },
                )
            )
        summary["artifacts"] = artifacts
        summary["caveat"] = (
            "A few picoseconds of implicit-solvent dynamics on "
            f"{simulation_settings.n_replicates} replicate"
            f"{'s' if simulation_settings.n_replicates != 1 else ''}. This illustrates "
            "the complex and samples local contacts; it is not a measurement of "
            "binding, stability or aggregation, which occur on timescales many orders "
            "of magnitude longer. The polymer is held near the protein by a tether, so "
            "the interface numbers describe interface quality under that constraint and "
            "are not evidence of spontaneous binding."
        )
        finish_job(job_id, summary, url=url)
    except BiologixError as exc:
        _record_refusal(job_id, exc, url=url)
    except (KeyError, ValueError) as exc:
        fail_job(
            job_id,
            error=str(exc.args[0] if exc.args else exc),
            error_kind=type(exc).__name__,
            remedy="check the target key and the repeat unit",
            url=url,
        )
    except ImportError as exc:
        fail_job(
            job_id,
            error=f"OpenMM is not installed: {exc}",
            error_kind="ImportError",
            remedy='install the simulation extra: uv pip install -e ".[simulation]"',
            url=url,
        )
    except Exception as exc:  # noqa: BLE001
        fail_job(job_id, error=f"unexpected {type(exc).__name__}: {exc}", url=url)


def _record_refusal(job_id: str, exc: BiologixError, *, url: str | None) -> None:
    """Store an adapter's own reason and remedy rather than a generic failure."""
    fail_job(
        job_id,
        error=str(exc),
        error_kind=type(exc).__name__,
        remedy=getattr(exc, "remedy", None),
        url=url,
    )


def execute_analysis_job(
    job_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    project_id: str | None = None,
    actor_name: str | None = None,
    user_id: str | None = None,
) -> None:
    """Dispatch, then record the outcome in the audit trail."""
    s = settings or get_settings()
    if kind == "dock":
        run_dock(job_id, settings=s, **payload)
    elif kind == "affinity":
        run_affinity(job_id, settings=s, **payload)
    elif kind == "simulate":
        run_simulation(job_id, settings=s, **payload)
    else:  # pragma: no cover - the queue validates before enqueuing
        fail_job(job_id, error=f"unknown job kind {kind!r}", url=s.database_url)
        return

    from biologix.store import get_job

    job = get_job(job_id, url=s.database_url) or {}
    outcome = job.get("status", "unknown")
    log(
        kind=f"job.{outcome}",
        summary=(
            f"{KIND_LABEL.get(kind, kind)} {outcome}"
            + (f": {job.get('error')}" if outcome == "failed" else "")
        ),
        project_id=project_id,
        user_id=user_id,
        actor_name=actor_name,
        run_id=job.get("run_id"),
        candidate_id=job.get("candidate_id"),
        job_id=job_id,
        detail={"kind": kind},
        url=s.database_url,
    )


__all__ = [
    "JOB_KINDS",
    "KIND_LABEL",
    "execute_analysis_job",
    "run_affinity",
    "run_dock",
    "run_simulation",
]

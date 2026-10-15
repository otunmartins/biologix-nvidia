"""Generate-and-screen, run end to end and stored.

This is the same pipeline the MCP server exposes as two tools
(``generate_candidates`` then ``screen_candidates``), joined up and persisted so
a user can leave and come back to it. It adds nothing to the chemistry; what it
adds is that every stage's output, including what was thrown away, ends up in
the database:

1. **Generate** with the baseline generator, BioNeMo GenMol or BioNeMo MolMIM.
   Proposals that never became a repeat unit are stored as discards with their
   rejection codes.
2. **Screen** each candidate: RDKit descriptors on a capped oligomer, then the
   chosen constraint set. Failures are stored with the candidate, not dropped.
3. **Measure diversity** over the oligomers with nvMolKit when a GPU is present
   and RDKit otherwise. The backend that actually ran is recorded, so a stored
   run says whether it touched the GPU.

An unavailable NVIDIA generator fails the run with the adapter's own reason and
remedy. It is never swapped for the baseline: a run labelled ``genmol`` that
was really produced by the baseline would be a false record.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from rdkit import Chem

from biologix.errors import BiologixError
from biologix.polymers.generate import GenerationResult
from biologix.polymers.models import PolymerCandidate
from biologix.screening.descriptors import compute_descriptors
from biologix.screening.diversity import diversity_report
from biologix.screening.filters import (
    DEFAULT_CONSTRAINTS,
    apply_filters,
    apply_synthesis_gate,
    get_constraint_set,
)
from biologix.screening.fingerprints import get_backend
from biologix.settings import Settings, get_settings
from biologix.store import fail_run, save_run_results, set_run_progress
from biologix.targets.formulation import FormulationProfile

GENERATORS = ("baseline", "genmol", "molmim")

Progress = Callable[[str, float], None]


def _generate(
    generator: str,
    n: int,
    seed: int,
    profile: FormulationProfile | None = None,
) -> GenerationResult:
    """Run one generator, giving it the target's profile where it can use it.

    The profile is derived from the target's own sequence, so it is available
    for any biologic a user imports and not only for a curated few. A
    generator that cannot take it is called without it rather than silently
    given something it would ignore.
    """
    if generator == "baseline":
        from biologix.polymers.generate import BaselineGenerator

        return BaselineGenerator(seed=seed).generate(n)
    if generator == "genmol":
        from biologix.nvidia.genmol import GenMolGenerator

        return GenMolGenerator().generate(n, profile=profile)
    if generator == "molmim":
        from biologix.nvidia.molmim import MolMIMGenerator

        return MolMIMGenerator().generate(n)
    raise ValueError(f"unknown generator {generator!r}; use {', '.join(GENERATORS)}")


def cohort_distances(
    oligomer_smiles: dict[str, str], *, prefer_gpu: bool
) -> tuple[dict[str, float], dict[str, Any]]:
    """Each candidate's mean Tanimoto distance from the rest of its cohort.

    This is the per-candidate figure the ranking's diversity criterion reads.
    Fewer than two candidates carry no distance at all, and get none rather
    than a made-up zero.
    """
    ids = list(oligomer_smiles)
    if len(ids) < 2:
        return {}, {"actual": "none", "note": "fewer than two candidates"}
    mols = [Chem.MolFromSmiles(oligomer_smiles[i]) for i in ids]
    backend, report = get_backend(prefer_gpu)
    distance = 1.0 - backend.bulk_tanimoto(backend.morgan_fingerprints(mols))
    n = len(ids)
    means = (distance.sum(axis=1) - np.diag(distance)) / (n - 1)
    return {cid: round(float(v), 4) for cid, v in zip(ids, means, strict=True)}, report.to_dict()


def _candidate_row(candidate: PolymerCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "display_name": candidate.display_name(),
        "repeat_units": [b.unit.smiles for b in candidate.blocks],
        "degree_of_polymerisation": candidate.chain_length.degree_of_polymerisation,
        "end_group": str(candidate.end_group),
        "architecture": str(candidate.architecture),
        "families": list(candidate.families),
        "generator": candidate.generator,
        "notes": candidate.notes,
    }


def execute_run(
    run_id: str,
    *,
    generator: str,
    n: int,
    constraint_set: str | None,
    seed: int | None,
    settings: Settings | None = None,
    progress: Progress | None = None,
    target_key: str | None = None,
) -> None:
    """Run one generate-and-screen pass and store everything it produced.

    Never raises for an expected failure: the run is marked failed with a
    message a user can act on, because this executes on a worker thread where
    an exception would only reach a log.
    """
    s = settings or get_settings()
    url = s.database_url

    def report(stage: str, fraction: float) -> None:
        set_run_progress(run_id, stage=stage, progress=fraction, url=url)
        if progress is not None:
            progress(stage, fraction)

    try:
        constraints = (
            DEFAULT_CONSTRAINTS if constraint_set is None else get_constraint_set(constraint_set)
        )
        report("generating", 0.05)
        # The target's formulation profile, from its sequence. A target that
        # cannot be resolved is not fatal: the run proceeds target-blind and
        # the manifest records that it did, which is better than failing a
        # campaign over a profile that only biases the search.
        profile = None
        if target_key:
            try:
                from biologix.targets.formulation import profile_target
                from biologix.targets.registry import get_target

                profile = profile_target(get_target(target_key))
            except (KeyError, ValueError):
                profile = None
        result = _generate(generator, n, seed if seed is not None else s.seed, profile)

        discards = [
            {
                "proposed_smiles": r.proposed_smiles,
                "source": r.source,
                "stage": "duplicate" if r.duplicate_of else "polymerisation_rules",
                "codes": list(r.rejection_codes),
                "detail": r.rejection_detail,
                "duplicate_of": r.duplicate_of,
            }
            for r in result.records
            if not r.accepted
        ]

        rows: list[dict[str, Any]] = []
        oligomers: dict[str, str] = {}
        total = max(1, len(result.candidates))
        for i, candidate in enumerate(result.candidates):
            report("screening", 0.15 + 0.7 * i / total)
            descriptors = compute_descriptors(candidate)
            outcome = apply_filters(candidate, descriptors, constraints)
            route = next(
                (
                    result.routes.get(block.unit.smiles)
                    for block in candidate.blocks
                    if block.unit.smiles in result.routes
                ),
                None,
            )
            # The exploratory set exists to show what a generator produces, so
            # there the gate reports rather than rejects.
            outcome = apply_synthesis_gate(
                outcome, route, enforce=constraints.name != "exploratory"
            )
            if descriptors.error is None and descriptors.oligomer_smiles:
                oligomers[candidate.candidate_id] = descriptors.oligomer_smiles
            rows.append(
                {
                    **_candidate_row(candidate),
                    "synthesis": route,
                    "descriptors": descriptors.values,
                    "descriptor_error": descriptors.error,
                    "oligomer_smiles": descriptors.oligomer_smiles,
                    "screening_dp": descriptors.screening_dp,
                    "construction_rules": descriptors.construction_rules,
                    "passed": outcome.passed,
                    "failures": list(outcome.failures),
                    "warnings": list(outcome.warnings),
                    "monomer_flags": [f.to_dict() for f in outcome.residual_monomer_flags],
                    "regulatory_context": outcome.regulatory_context,
                }
            )

        report("diversity", 0.9)
        # nvMolKit is asked for; the report says whether it was what ran.
        distances, backend = cohort_distances(oligomers, prefer_gpu=True)
        for row in rows:
            row["cohort_distance"] = distances.get(row["candidate_id"])
        diversity = diversity_report(result.candidates, oligomers, prefer_gpu=True).to_dict()
        diversity["cohort_distance_backend"] = backend

        statistics = dict(result.stats())
        # The derivation figures live in the manifest because that is where the
        # generator wrote them; a user needs them beside the validity numbers.
        derivation = (result.manifest.outputs or {}).get("conversion")
        if isinstance(derivation, dict):
            statistics["derivation"] = derivation
        # The funnel and the target profile: a cohort shorter than requested
        # has to be explainable from the stored run alone.
        selection = (result.manifest.outputs or {}).get("selection")
        if isinstance(selection, dict):
            statistics["selection"] = selection
        if profile is not None:
            statistics["target_profile"] = profile.to_dict()

        save_run_results(
            run_id,
            statistics=statistics,
            rejections=result.rejection_histogram(),
            constraints=[c.describe() for c in constraints.constraints],
            candidates=rows,
            discards=discards,
            diversity=diversity,
            warnings=list(result.manifest.warnings),
            errors=list(result.manifest.errors),
            manifest=result.manifest.to_dict(),
            url=url,
        )
        if progress is not None:
            progress("complete", 1.0)
    except BiologixError as exc:
        remedy = getattr(exc, "remedy", None)
        fail_run(run_id, f"{exc}" + (f" Remedy: {remedy}" if remedy else ""), url=url)
    except (KeyError, ValueError) as exc:
        fail_run(run_id, str(exc), url=url)
    except Exception as exc:  # noqa: BLE001 - a worker must record, not vanish
        fail_run(run_id, f"unexpected {type(exc).__name__}: {exc}", url=url)


__all__ = ["GENERATORS", "cohort_distances", "execute_run"]

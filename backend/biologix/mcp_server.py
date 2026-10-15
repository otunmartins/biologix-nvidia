"""MCP server exposing the Biologix pipeline as agent-callable tools.

This is the half of the agent story that NVIDIA's BioNeMo Agent Toolkit does
not cover. The toolkit publishes skills that call BioNeMo NIMs directly
(``boltz2-nim``, ``diffdock-nim``, ``molmim-nim``, ``genmol-nim``,
``nvmolkit``), which is the right way for an agent to reach a *model*. What it
cannot know is the polymer-specific discipline this repository exists to
enforce:

* a polymer specification is not a molecule, so anything that needs a molecule
  must build an explicit capped oligomer and say what it built;
* a generated molecule is not a repeat unit until it has a polymerisable
  handle and passes :mod:`biologix.polymers.rules`;
* an unavailable adapter is skipped, never substituted;
* every number carries an evidence tier — ``hypothesis``, ``computed`` or
  ``measured`` — and a run manifest.

So the two compose: install the toolkit's skills for direct NIM access, and
point an agent at this server for the pipeline that keeps the above true. Each
tool here returns the evidence tier alongside its result, and refusals come
back as structured ``{"error": ..., "remedy": ...}`` payloads rather than
exceptions, because an agent needs to read a refusal and choose a different
route rather than crash.

Run it with ``biologix-mcp`` (stdio transport).
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from biologix import EVIDENCE_HYPOTHESIS, EVIDENCE_TIERS, __version__
from biologix.errors import AdapterCallFailed, AdapterUnavailable, BiologixError
from biologix.settings import get_settings

mcp = FastMCP(
    "biologix",
    instructions=(
        "Polymer excipient discovery for biologic formulations, with NVIDIA "
        "BioNeMo components. This is a research prototype: nothing it returns is "
        "a validated prediction of drug stability, shelf life or clinical "
        "performance. Every result carries an 'evidence' field — 'hypothesis' "
        "(proposed, not computed), 'computed' (software output, reproducible from "
        "the manifest, not a measurement) or 'measured' (from a laboratory assay). "
        "Never present a 'hypothesis' or 'computed' value as a measurement, and "
        "always carry the caveats attached to a result when you summarise it. "
        "Call nvidia_status first: adapters that are unavailable are skipped, and "
        "the baseline generator, RDKit screening and CPU simulation work without "
        "any NVIDIA component."
    ),
)


def _refusal(exc: BiologixError) -> dict[str, Any]:
    """Turn a refusal into a payload an agent can act on."""
    payload: dict[str, Any] = {"error": type(exc).__name__, "message": str(exc)}
    for attr in ("adapter", "reason", "remedy", "status", "field"):
        value = getattr(exc, attr, None)
        if value is not None:
            payload[attr] = value
    payload["note"] = (
        "No value was produced. Do not substitute a default, an estimate or a "
        "figure from memory; either fix the cause named above or use a route that "
        "needs no NVIDIA adapter."
    )
    return payload


@mcp.tool()
def nvidia_status() -> dict[str, Any]:
    """Report which NVIDIA/BioNeMo components can actually run here.

    Call this before anything else. Each adapter reports available true/false
    with a reason and a remedy, and adapters whose code paths have never been
    executed against live hardware in this build are listed under
    ``summary.unverified_adapters``.
    """
    from biologix.nvidia import availability_report

    report = availability_report()
    report["biologix_version"] = __version__
    report["evidence_tiers"] = list(EVIDENCE_TIERS)
    return report


@mcp.tool()
def list_targets() -> dict[str, Any]:
    """List the biologic targets available, with chain and modification counts."""
    from biologix.targets.registry import list_targets as _list

    return {"targets": _list(), "note": "Chains are never concatenated into one sequence."}


@mcp.tool()
def generate_candidates(
    n: int = 12,
    generator: str = "baseline",
    seed: int | None = None,
) -> dict[str, Any]:
    """Propose polymer excipient candidates.

    ``generator`` is ``baseline`` (deterministic, no network, no GPU),
    ``genmol`` (BioNeMo GenMol fragment generation) or ``molmim`` (BioNeMo
    MolMIM latent sampling around known excipient chemistry). The baseline is
    the comparison standard: a learned generator that cannot beat it on
    validity, uniqueness and diversity has not earned its dependency.

    Returns candidates plus the yield statistics that matter — how many
    proposals were chemically valid, how many were new, and why the rest were
    rejected.
    """
    choice = generator.strip().lower()
    try:
        if choice == "baseline":
            from biologix.polymers.generate import BaselineGenerator

            settings = get_settings()
            result = BaselineGenerator(seed=seed if seed is not None else settings.seed).generate(n)
        elif choice == "genmol":
            from biologix.nvidia.genmol import GenMolGenerator

            result = GenMolGenerator().generate(n)
        elif choice == "molmim":
            from biologix.nvidia.molmim import MolMIMGenerator

            result = MolMIMGenerator().generate(n)
        else:
            return {
                "error": "ValueError",
                "message": f"unknown generator {generator!r}; use baseline, genmol or molmim",
            }
    except BiologixError as exc:
        return _refusal(exc)

    return {
        "evidence": EVIDENCE_HYPOTHESIS,
        "generator": result.generator,
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "display_name": c.display_name(),
                "repeat_units": [b.unit.smiles for b in c.blocks],
                "degree_of_polymerisation": c.chain_length.degree_of_polymerisation,
                "end_group": str(c.end_group),
                "architecture": str(c.architecture),
                "families": list(c.families),
            }
            for c in result.candidates
        ],
        "statistics": result.stats(),
        "rejections": result.rejection_histogram(),
        "errors": result.manifest.errors,
        "warnings": result.manifest.warnings,
        "note": (
            "These are proposals, not predictions. A candidate here has passed "
            "polymerisation rules only; it has not been screened, simulated or "
            "measured."
        ),
    }


@mcp.tool()
def screen_candidates(
    repeat_unit_smiles: list[str], constraint_set: str | None = None
) -> dict[str, Any]:
    """Validate repeat units, compute descriptors, and apply constraints.

    Each entry must be a repeat-unit SMILES carrying two ``[*]`` attachment
    points (for example ``[*]CCO[*]``). Descriptors are computed from a short
    capped oligomer so that candidates with different requested chain lengths
    remain comparable; the oligomer used is reported.

    ``constraint_set`` defaults to the aqueous-parenteral starting set; pass
    ``"exploratory"`` to apply only buildability. Every threshold carries its
    own rationale in the returned ``constraints`` list, because they are
    editable heuristics rather than regulatory requirements.

    Nothing here is a safety assessment. Residual-monomer flags are scoped to
    the *monomer* and say so: monomer toxicology does not transfer to the
    polymer, and polymer safety does not follow from monomer safety either.
    """
    from biologix.polymers.generate import candidates_from_smiles
    from biologix.screening.descriptors import compute_descriptors
    from biologix.screening.filters import (
        DEFAULT_CONSTRAINTS,
        apply_filters,
        get_constraint_set,
    )

    if not repeat_unit_smiles:
        return {"error": "ValueError", "message": "no SMILES supplied"}
    try:
        constraints = (
            DEFAULT_CONSTRAINTS if constraint_set is None else get_constraint_set(constraint_set)
        )
    except (KeyError, ValueError) as exc:
        return {"error": "ValueError", "message": str(exc)}

    imported = candidates_from_smiles(repeat_unit_smiles)
    rows: list[dict[str, Any]] = []
    for candidate in imported.candidates:
        descriptors = compute_descriptors(candidate)
        outcome = apply_filters(candidate, descriptors, constraints)
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "display_name": candidate.display_name(),
                "descriptors": descriptors.values,
                "descriptor_error": descriptors.error,
                "oligomer_smiles": descriptors.oligomer_smiles,
                "screening_dp": descriptors.screening_dp,
                "passed": outcome.passed,
                "failures": outcome.failures,
                "warnings": outcome.warnings,
                "monomer_scoped_flags": [
                    f.to_dict() if hasattr(f, "to_dict") else str(f)
                    for f in outcome.residual_monomer_flags
                ],
                "regulatory_context": outcome.regulatory_context,
            }
        )

    return {
        "evidence": "computed",
        "constraint_set": constraints.name,
        "constraints": [c.describe() for c in constraints.constraints],
        "results": rows,
        "rejected_before_screening": [
            {"smiles": r.proposed_smiles, "reason": r.rejection_detail}
            for r in imported.records
            if not r.accepted and not r.duplicate_of
        ],
        "note": (
            "Descriptors describe a short capped oligomer, not a polydisperse "
            "polymer. Constraint thresholds are editable choices with stated "
            "rationales, not regulatory limits."
        ),
    }


@mcp.tool()
def embed_target(target: str) -> dict[str, Any]:
    """Embed a biologic's chains with BioNeMo ESM-2.

    Chains are embedded separately and never concatenated. Chemical
    modifications (acylation, PEGylation, glycosylation) are invisible to a
    sequence model and are returned under ``unrepresented_chemistry``.
    Embeddings are features, not stability predictions.
    """
    try:
        from biologix.nvidia.esm2 import Esm2Adapter
        from biologix.targets.registry import get_target

        biologic = get_target(target)
        adapter = Esm2Adapter()
        try:
            result = adapter.embed_biologic(biologic)
        finally:
            adapter.close()
    except KeyError as exc:
        return {"error": "KeyError", "message": str(exc)}
    except BiologixError as exc:
        return _refusal(exc)

    return {
        "evidence": "computed",
        "biologic": result.biologic_name,
        "model": result.model,
        "model_version": result.model_version,
        "chains": {
            chain_id: {
                "n_residues": emb.n_residues,
                "hidden_dim": emb.hidden_dim,
                "pooling": emb.pooling,
                "cached": emb.cached,
            }
            for chain_id, emb in result.chains.items()
        },
        "unrepresented_chemistry": result.unrepresented_chemistry,
        "note": (
            "An embedding is a feature vector, not a prediction. The vectors "
            "themselves are not returned over MCP; they are cached locally for "
            "downstream use."
        ),
    }


@mcp.tool()
def dock_candidate_to_target(
    target: str,
    repeat_unit_smiles: str,
    degree_of_polymerisation: int = 10,
    num_poses: int = 10,
) -> dict[str, Any]:
    """Dock a candidate's oligomer against a target structure with DiffDock.

    The candidate is reduced to one explicit capped oligomer first. Poses are
    *candidate starting geometries* for simulation: the confidence score
    estimates whether a pose is geometrically right, not how strongly anything
    binds, and it is not comparable across different ligands. Pose quality
    degrades with ligand flexibility, and the oligomer's rotatable-bond count
    is reported so you can see how far outside DiffDock's drug-like training
    regime the query sits.
    """
    try:
        from biologix.nvidia.diffdock import dock_candidate
        from biologix.polymers.generate import candidates_from_smiles
        from biologix.targets.prepare import prepare_target
        from biologix.targets.registry import get_target

        biologic = get_target(target)
        imported = candidates_from_smiles(
            [repeat_unit_smiles], dp=degree_of_polymerisation
        )
        if not imported.candidates:
            rejection = next(
                (r.rejection_detail for r in imported.records if r.rejection_detail), None
            )
            return {
                "error": "ValidationFailure",
                "message": f"{repeat_unit_smiles!r} is not a usable repeat unit",
                "reason": rejection,
            }
        structure = prepare_target(biologic)
        result = dock_candidate(
            structure.path,
            imported.candidates[0],
            num_poses=num_poses,
            dp_override=degree_of_polymerisation,
        )
    except KeyError as exc:
        return {"error": "KeyError", "message": str(exc)}
    except (AdapterUnavailable, AdapterCallFailed) as exc:
        return _refusal(exc)
    except BiologixError as exc:
        return _refusal(exc)

    summary = result.summary()
    summary["evidence"] = "computed"
    summary["target"] = biologic.name
    return summary


@mcp.tool()
def predict_complex_affinity(
    target: str,
    repeat_unit_smiles: str,
    degree_of_polymerisation: int = 10,
) -> dict[str, Any]:
    """Predict a biologic–oligomer complex and binding affinity with Boltz-2.

    Read the caveats before using the number. Boltz-2's affinity head was
    trained on small-molecule protein–ligand binding; a polymer oligomer is
    outside that distribution, so ``out_of_distribution`` is always true. The
    value ranks candidates under a fixed model. It is not a Kd, not an IC50
    measurement, and not a stability prediction — excipients mostly act through
    colloidal and interfacial mechanisms rather than pocket binding.
    """
    try:
        from biologix.nvidia.boltz2 import predict_complex
        from biologix.polymers.generate import candidates_from_smiles
        from biologix.targets.registry import get_target

        biologic = get_target(target)
        imported = candidates_from_smiles(
            [repeat_unit_smiles], dp=degree_of_polymerisation
        )
        if not imported.candidates:
            rejection = next(
                (r.rejection_detail for r in imported.records if r.rejection_detail), None
            )
            return {
                "error": "ValidationFailure",
                "message": f"{repeat_unit_smiles!r} is not a usable repeat unit",
                "reason": rejection,
            }
        result = predict_complex(
            biologic, imported.candidates[0], dp_override=degree_of_polymerisation
        )
    except KeyError as exc:
        return {"error": "KeyError", "message": str(exc)}
    except BiologixError as exc:
        return _refusal(exc)

    summary = result.summary()
    summary["evidence"] = "computed"
    summary["target"] = biologic.name
    summary["n_structures"] = len(result.structures)
    return summary


@mcp.resource("biologix://evidence-tiers")
def evidence_tiers() -> str:
    """How to read any number this server returns."""
    return (
        "Biologix keeps three categories of output strictly distinct.\n\n"
        "hypothesis — generated or proposed, not computed. Candidate structures "
        "from any generator are hypotheses.\n"
        "computed — the output of running software on inputs: descriptors, "
        "filters, simulation observables, model outputs. Reproducible from the "
        "recorded manifest. NOT a measurement of reality.\n"
        "measured — imported from a laboratory assay, with provenance.\n\n"
        "No tool on this server returns 'measured' data; this prototype has no "
        "assay integration. Presenting a 'computed' value as a measurement, or "
        "dropping the caveats attached to it, misrepresents the result."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Biologix MCP server (stdio transport)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the tool inventory and NVIDIA availability, then exit",
    )
    args = parser.parse_args()

    if args.check:
        import asyncio
        import json

        tools = asyncio.run(mcp.list_tools())
        inventory = {"tools": [t.name for t in tools], **nvidia_status()}
        print(json.dumps(inventory, indent=2, default=str))
        return

    mcp.run()


if __name__ == "__main__":
    main()

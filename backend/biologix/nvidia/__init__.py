"""NVIDIA model and library adapters.

Each adapter answers one question honestly before it is used: *can I run
here?* :func:`availability_report` aggregates those answers and is what the
API, the MCP server and the UI display. An adapter that cannot run reports
``available: false`` with a reason and a remedy; it never substitutes a
placeholder value.

The adapters, and what each is for:

``bionemo-esm2``
    Per-chain protein embeddings for the biologic target. Features, not
    predictions.
``bionemo-genmol``
    Fragment-based molecule generation, converted to repeat units.
``bionemo-molmim``
    Latent-space sampling around known excipient chemistry, plus molecule
    embeddings.
``bionemo-boltz2``
    Biologic–oligomer complex structure and a binding-affinity estimate.
``bionemo-diffdock``
    Blind docking poses, used as starting geometries for simulation.
``nvmolkit``
    GPU fingerprints, similarity, clustering and conformers.
"""

from __future__ import annotations

from typing import Any

from biologix.settings import Settings, get_settings

# Probe functions are imported lazily, inside the loop, so that one broken
# optional dependency cannot hide the status of the others.
_PROBES: tuple[tuple[str, str, str], ...] = (
    ("bionemo-esm2", "biologix.nvidia.esm2", "esm2_availability"),
    ("bionemo-genmol", "biologix.nvidia.genmol", "genmol_availability"),
    ("bionemo-molmim", "biologix.nvidia.molmim", "molmim_availability"),
    ("bionemo-boltz2", "biologix.nvidia.boltz2", "boltz2_availability"),
    ("bionemo-diffdock", "biologix.nvidia.diffdock", "diffdock_availability"),
    ("nvmolkit", "biologix.nvidia.nvmolkit", "nvmolkit_availability"),
)


def availability_report(settings: Settings | None = None) -> dict[str, Any]:
    """Status of every NVIDIA integration, plus the simulation backend."""
    import importlib
    import inspect

    s = settings or get_settings()
    adapters: list[dict[str, Any]] = []

    for name, module_path, func_name in _PROBES:
        try:
            module = importlib.import_module(module_path)
            probe = getattr(module, func_name)
            # nvmolkit's probe takes no settings; the NIM probes all do.
            kwargs = {} if not inspect.signature(probe).parameters else {"settings": s}
            adapters.append(probe(**kwargs))
        except Exception as exc:  # pragma: no cover - defensive
            adapters.append(
                {"adapter": name, "available": False, "reason": f"probe failed: {exc!r}"}
            )

    try:
        from biologix.simulation.platforms import platform_report

        simulation = platform_report()
    except Exception as exc:  # pragma: no cover
        simulation = {"available": False, "reason": f"probe failed: {exc!r}"}

    unverified = [
        a["adapter"]
        for a in adapters
        if a.get("code_path_executed_in_this_build") is False
    ]
    return {
        "adapters": adapters,
        "simulation": simulation,
        "summary": {
            "n_available": sum(1 for a in adapters if a.get("available")),
            "n_total": len(adapters),
            "degraded_mode": not all(a.get("available") for a in adapters),
            "unverified_adapters": unverified,
            "note": (
                "Unavailable adapters are skipped, never simulated. The baseline "
                "generator, RDKit screening and CPU simulation run without any "
                "NVIDIA component."
            ),
            "verification_note": (
                "'unverified_adapters' have never been executed against a live "
                "endpoint or GPU in this repository's development environment. "
                "Their request construction and response parsing are covered by "
                "tests against recorded fixtures only. See docs/VERIFICATION.md."
            ),
        },
    }


__all__ = ["availability_report"]

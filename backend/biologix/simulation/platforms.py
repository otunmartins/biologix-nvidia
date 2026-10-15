"""OpenMM platform selection and reporting.

Two rules:

1. An **explicitly requested** platform that is unavailable is an error
   (:class:`~biologix.errors.PlatformUnavailable`). A run asked to use CUDA
   must not quietly produce CPU numbers labelled as a GPU run.
2. ``auto`` may fall back, but the platform that *actually* ran is recorded on
   every result and manifest. "Requested" and "actual" are separate fields
   everywhere.
"""

from __future__ import annotations

from typing import Any

from biologix.errors import PlatformUnavailable

# Preference order for ``auto``.
#
# CPU is preferred over OpenCL deliberately. OpenCL on this project's systems
# is almost always a non-NVIDIA integrated GPU, and measurement on the
# development machine showed it *slower* than CPU for a 782-atom implicit-
# solvent system (94 s vs 44 s for the same work) because kernel-launch
# overhead dominates at this size. CUDA remains first because it is the GPU
# path this project targets. Reference is never auto-selected: correct, but
# far too slow to be a default.
AUTO_PREFERENCE = ("CUDA", "CPU", "OpenCL")


def openmm_available() -> tuple[bool, str | None]:
    try:
        import openmm  # noqa: F401

        return True, None
    except ImportError as exc:
        return False, (
            f"openmm is not installed ({exc}). Install the simulation extra: "
            "uv pip install 'openmm==8.6.1'"
        )


def available_platforms() -> list[str]:
    ok, _ = openmm_available()
    if not ok:
        return []
    import openmm

    return [
        openmm.Platform.getPlatform(i).getName()
        for i in range(openmm.Platform.getNumPlatforms())
    ]


def platform_report() -> dict[str, Any]:
    """Everything the UI needs to say about simulation capability."""
    ok, reason = openmm_available()
    if not ok:
        return {
            "backend": "openmm",
            "available": False,
            "reason": reason,
            "platforms": [],
            "cuda_available": False,
        }

    import openmm

    from biologix.nvidia.nvmolkit import _gpu_probe
    from biologix.settings import get_settings

    names = available_platforms()
    speeds = {}
    for i in range(openmm.Platform.getNumPlatforms()):
        p = openmm.Platform.getPlatform(i)
        speeds[p.getName()] = p.getSpeed()

    settings = get_settings()
    requested = settings.openmm_platform
    gpu = _gpu_probe()

    resolvable = None
    resolve_error = None
    try:
        resolvable = resolve_platform_name(requested)
    except PlatformUnavailable as exc:
        resolve_error = str(exc)

    return {
        "backend": "openmm",
        "available": True,
        "version": openmm.version.version,
        "platforms": names,
        "platform_speeds": speeds,
        "cuda_available": "CUDA" in names,
        "opencl_available": "OpenCL" in names,
        "configured_platform": requested,
        "would_resolve_to": resolvable,
        "resolve_error": resolve_error,
        "gpu_probe": gpu,
        "cuda_note": (
            "The OpenMM PyPI wheel exposes a CUDA platform only when a CUDA "
            "runtime is present on the host. 'CUDA' missing from the list above "
            "means no CUDA-capable OpenMM platform exists here, so an explicit "
            "CUDA request will fail rather than fall back."
        ),
    }


def resolve_platform_name(requested: str) -> str:
    """Resolve a configured platform name to one that exists.

    Raises :class:`PlatformUnavailable` for an explicit request that cannot be
    satisfied. ``auto`` picks the fastest available from
    :data:`AUTO_PREFERENCE`.
    """
    ok, reason = openmm_available()
    if not ok:
        raise PlatformUnavailable(requested, [])

    names = available_platforms()

    if requested.lower() != "auto":
        match = next((n for n in names if n.lower() == requested.lower()), None)
        if match is None:
            raise PlatformUnavailable(requested, names)
        return match

    for preferred in AUTO_PREFERENCE:
        if preferred in names:
            return preferred
    if names:
        return names[0]
    raise PlatformUnavailable("auto", names)


def get_platform(requested: str | None = None) -> tuple[Any, dict[str, Any]]:
    """Return ``(openmm.Platform, record)``.

    ``record`` carries both the requested and the actual platform, and whether
    a fallback happened, so a caller can refuse to report a GPU timing from a
    CPU run.
    """
    import openmm

    from biologix.settings import get_settings

    settings = get_settings()
    asked = requested or settings.openmm_platform
    resolved = resolve_platform_name(asked)
    platform = openmm.Platform.getPlatformByName(resolved)

    explicit = asked.lower() != "auto"
    record = {
        "requested": asked,
        "actual": resolved,
        "explicit_request": explicit,
        "fell_back": (not explicit) and resolved != AUTO_PREFERENCE[0],
        "available_platforms": available_platforms(),
        "is_gpu": resolved in ("CUDA", "OpenCL", "HIP"),
        "openmm_version": openmm.version.version,
    }
    return platform, record


def platform_properties(platform_name: str, *, threads: int | None = None) -> dict[str, str]:
    """Platform-specific properties, recorded verbatim in the manifest."""
    props: dict[str, str] = {}
    if platform_name == "CPU" and threads:
        props["Threads"] = str(threads)
    elif platform_name == "CUDA":
        # Mixed precision is the standard choice for biomolecular MD: single
        # precision forces with double-precision accumulation.
        props["Precision"] = "mixed"
        props["DeterministicForces"] = "true"
    elif platform_name == "OpenCL":
        props["Precision"] = "mixed"
    return props

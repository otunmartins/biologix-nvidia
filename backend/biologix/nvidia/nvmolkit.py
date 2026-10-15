"""nvMolKit adapter: GPU-accelerated chemistry operations.

nvMolKit (Apache-2.0, https://github.com/NVIDIA-BioNeMo/nvMolKit) mirrors a
subset of RDKit on the GPU with batch-oriented APIs. As of v0.3 the supported
operations are batch Morgan fingerprints, many-to-many Tanimoto and cosine
similarity, Butina clustering, batch ETKDG conformer generation and batch
MMFF/UFF optimisation. Everything else stays on RDKit.

Hardware requirement: an NVIDIA GPU of compute capability >= 7.0 (V100 or
newer) and a CUDA 12.6+ driver.

**Verification status: this module's GPU code paths have not been executed.**
The development machine has no NVIDIA GPU (``nvidia-smi`` is absent), so the
adapter is written against the published API and reports itself unavailable
here. :func:`nvmolkit_availability` is the single source of truth, and the
benchmark script refuses to report a speedup it did not measure. Agreement
with RDKit is asserted by
:func:`biologix.screening.fingerprints.agreement`, which must be run on GPU
hardware before any claim is made.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
from typing import Any

import numpy as np
from rdkit import Chem

from biologix.errors import AdapterUnavailable
from biologix.screening.fingerprints import DEFAULT_N_BITS, DEFAULT_RADIUS

NVMOLKIT_LICENCE = "Apache-2.0"
NVMOLKIT_REPO = "https://github.com/NVIDIA-BioNeMo/nvMolKit"
MIN_COMPUTE_CAPABILITY = 7.0

# Operations this adapter routes to the GPU. Anything absent stays on RDKit,
# which is stated rather than implied.
GPU_OPERATIONS = (
    "morgan_fingerprints",
    "bulk_tanimoto",
    "bulk_cosine",
    "butina_clustering",
    "etkdg_conformers",
    "mmff_optimize",
)
RDKIT_ONLY_OPERATIONS = (
    "sanitization and valence checking",
    "substructure and SMARTS matching",
    "descriptor calculation",
    "SMILES/SAFE parsing and canonicalisation",
    "polymer graph surgery (oligomer construction)",
    "MMFF parameter extraction for simulation",
)


def _gpu_probe() -> dict[str, Any]:
    """Ask the driver, not a library, whether a usable GPU exists."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        return {"gpu_present": False, "detail": "nvidia-smi not found on PATH"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"gpu_present": False, "detail": f"nvidia-smi failed: {exc!r}"}

    if proc.returncode != 0 or not proc.stdout.strip():
        return {"gpu_present": False, "detail": "nvidia-smi reported no devices"}

    gpus = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            cap = float(parts[1])
        except ValueError:
            cap = 0.0
        gpus.append({"name": parts[0], "compute_capability": cap, "driver": parts[2]})

    usable = [g for g in gpus if g["compute_capability"] >= MIN_COMPUTE_CAPABILITY]
    return {
        "gpu_present": bool(gpus),
        "gpus": gpus,
        "n_usable": len(usable),
        "detail": (
            None
            if usable
            else f"no GPU meets the compute capability >= {MIN_COMPUTE_CAPABILITY} requirement"
        ),
    }


def nvmolkit_availability() -> dict[str, Any]:
    """Whether the GPU chemistry path can run here, and why not if it cannot."""
    from biologix.settings import get_settings

    settings = get_settings()
    probe = _gpu_probe()
    installed = importlib.util.find_spec("nvmolkit") is not None

    version = "not-installed"
    if installed:
        try:
            version = importlib.metadata.version("nvmolkit")
        except Exception:  # pragma: no cover - metadata may be absent
            version = "unknown"

    reasons: list[str] = []
    if not settings.enable_nvmolkit:
        reasons.append("BIOLOGIX_ENABLE_NVMOLKIT is false")
    if not installed:
        reasons.append(
            "nvmolkit is not installed (it is in the optional 'gpu' extra: "
            "uv pip install --torch-backend=cu128 nvmolkit)"
        )
    if not probe["gpu_present"]:
        reasons.append(f"no NVIDIA GPU detected: {probe['detail']}")
    elif probe.get("n_usable", 0) == 0:
        reasons.append(probe["detail"] or "no usable GPU")

    return {
        "adapter": "nvmolkit",
        "available": not reasons,
        "reason": "; ".join(reasons) if reasons else None,
        "enabled_in_config": settings.enable_nvmolkit,
        "installed": installed,
        "version": version,
        "licence": NVMOLKIT_LICENCE,
        "repository": NVMOLKIT_REPO,
        "min_compute_capability": MIN_COMPUTE_CAPABILITY,
        "gpu_probe": probe,
        "gpu_operations": list(GPU_OPERATIONS),
        "rdkit_only_operations": list(RDKIT_ONLY_OPERATIONS),
        "code_path_executed_in_this_build": False,
        "verification_note": (
            "This adapter has not been executed against real GPU hardware in "
            "this repository's development environment. Treat it as unverified "
            "until the benchmark in backend/benchmarks/bench_fingerprints.py "
            "runs on a GPU machine and reports agreement with RDKit."
        ),
    }


def _resolve(result: Any) -> Any:
    """Pull a concrete array out of nvMolKit's async GPU result wrapper.

    nvMolKit returns ``AsyncGpuResult`` objects; the accessor name has varied
    across versions, so several are tried and the failure is explicit.
    """
    for attr in ("Get", "get", "result", "to_numpy", "numpy", "cpu"):
        fn = getattr(result, attr, None)
        if callable(fn):
            return fn()
    if hasattr(result, "__array__"):
        return np.asarray(result)
    return result


def _to_numpy(value: Any) -> np.ndarray:
    """Convert a resolved result (torch tensor or array) to NumPy."""
    value = _resolve(value)
    if hasattr(value, "detach"):  # torch tensor
        value = value.detach().cpu().numpy()
    return np.asarray(value)


def _unpack_bits(packed: np.ndarray, n_bits: int) -> np.ndarray:
    """Expand nvMolKit's packed int32 fingerprints into 0/1 bytes.

    nvMolKit returns ``(n_mols, n_bits/32)`` int32. RDKit's backend in this
    project returns ``(n_mols, n_bits)`` uint8, so the GPU output is unpacked
    to match. Bit order is checked against RDKit in the benchmark rather than
    assumed.
    """
    as_u32 = packed.astype(np.uint32, copy=False)
    as_bytes = as_u32.view(np.uint8).reshape(as_u32.shape[0], -1)
    bits = np.unpackbits(as_bytes, axis=1, bitorder="little")
    return bits[:, :n_bits].astype(np.uint8, copy=False)


class NvMolKitBackend:
    """GPU implementation of the :class:`ChemistryBackend` protocol."""

    name = "nvmolkit-gpu"

    def __init__(self) -> None:
        availability = nvmolkit_availability()
        if not availability["available"]:
            raise AdapterUnavailable(
                "nvmolkit",
                availability["reason"] or "unavailable",
                remedy="run on a machine with an NVIDIA GPU (compute capability "
                ">= 7.0) and set BIOLOGIX_ENABLE_NVMOLKIT=true",
            )
        self.availability = availability
        self._fingerprints = importlib.import_module("nvmolkit.fingerprints")
        self._similarity = importlib.import_module("nvmolkit.similarity")

    def morgan_fingerprints(
        self, mols: list[Chem.Mol], *, radius: int = DEFAULT_RADIUS, n_bits: int = DEFAULT_N_BITS
    ) -> np.ndarray:
        valid = [m for m in mols if m is not None]
        generator = self._fingerprints.MorganFingerprintGenerator(
            radius=radius, fpSize=n_bits
        )
        packed = _to_numpy(generator.GetFingerprints(valid))
        unpacked = _unpack_bits(packed, n_bits)

        # Reinstate rows for any None inputs so the output aligns with the
        # input list, as the RDKit backend does.
        if len(valid) == len(mols):
            return unpacked
        out = np.zeros((len(mols), n_bits), dtype=np.uint8)
        cursor = 0
        for i, mol in enumerate(mols):
            if mol is None:
                continue
            out[i] = unpacked[cursor]
            cursor += 1
        return out

    def bulk_tanimoto(self, fps_a: np.ndarray, fps_b: np.ndarray | None = None) -> np.ndarray:
        sim = self._similarity.crossTanimotoSimilarity(fps_a, fps_b)
        return _to_numpy(sim).astype(np.float32)

    def bulk_cosine(self, fps_a: np.ndarray, fps_b: np.ndarray | None = None) -> np.ndarray:
        sim = self._similarity.crossCosineSimilarity(fps_a, fps_b)
        return _to_numpy(sim).astype(np.float32)

    def butina_clustering(self, fingerprints: np.ndarray, cutoff: float) -> np.ndarray:
        clustering = importlib.import_module("nvmolkit.clustering")
        return _to_numpy(clustering.fused_butina(fingerprints, cutoff))

    def generate_conformers(
        self, mols: list[Chem.Mol], *, confs_per_molecule: int = 1, max_iterations: int = -1
    ) -> list[Chem.Mol]:
        """Batch ETKDG embedding. Conformers are added to the molecules in place.

        Hydrogens must already be explicit and the molecules sanitised, which
        is nvMolKit's documented precondition.
        """
        from rdkit.Chem import AllChem

        embed = importlib.import_module("nvmolkit.embedMolecules")
        params = AllChem.ETKDGv3()
        prepared = [Chem.AddHs(m) for m in mols if m is not None]
        embed.EmbedMolecules(
            prepared, params, confsPerMolecule=confs_per_molecule, maxIterations=max_iterations
        )
        return prepared

    def mmff_optimize(self, mols: list[Chem.Mol], *, max_iters: int = 500) -> list[list[float]]:
        mmff = importlib.import_module("nvmolkit.mmffOptimization")
        return mmff.MMFFOptimizeMoleculesConfs(mols, maxIters=max_iters)

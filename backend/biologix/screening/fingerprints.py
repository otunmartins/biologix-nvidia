"""Fingerprints and similarity, with a swappable compute backend.

The backend abstraction exists so the GPU path (nvMolKit) and the CPU path
(RDKit) can be benchmarked on identical inputs and verified to agree. The
backend that actually ran is always reported, never assumed: a run that
requested GPU and silently fell back to CPU would invalidate any timing
claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

DEFAULT_RADIUS = 2
DEFAULT_N_BITS = 2048


@dataclass
class BackendReport:
    """What a chemistry operation actually ran on."""

    requested: str
    actual: str
    operations: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    fell_back: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "actual": self.actual,
            "per_operation": self.operations,
            "fell_back": self.fell_back,
            "notes": self.notes,
        }


@runtime_checkable
class ChemistryBackend(Protocol):
    """Operations that may be GPU-accelerated."""

    name: str

    def morgan_fingerprints(
        self, mols: list[Chem.Mol], *, radius: int = DEFAULT_RADIUS, n_bits: int = DEFAULT_N_BITS
    ) -> np.ndarray:
        """Packed-bit fingerprints as a ``(n_mols, n_bits)`` uint8 array of 0/1."""
        ...

    def bulk_tanimoto(self, fps_a: np.ndarray, fps_b: np.ndarray | None = None) -> np.ndarray:
        """Pairwise Tanimoto similarity matrix."""
        ...


class RDKitBackend:
    """Reference CPU implementation. Always available; the correctness oracle."""

    name = "rdkit-cpu"

    def morgan_fingerprints(
        self, mols: list[Chem.Mol], *, radius: int = DEFAULT_RADIUS, n_bits: int = DEFAULT_N_BITS
    ) -> np.ndarray:
        gen = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
        out = np.zeros((len(mols), n_bits), dtype=np.uint8)
        for i, mol in enumerate(mols):
            if mol is None:
                continue
            fp = gen.GetFingerprint(mol)
            arr = np.zeros((n_bits,), dtype=np.uint8)
            DataStructs.ConvertToNumpyArray(fp, arr)
            out[i] = arr
        return out

    def bulk_tanimoto(self, fps_a: np.ndarray, fps_b: np.ndarray | None = None) -> np.ndarray:
        b = fps_a if fps_b is None else fps_b
        a = fps_a.astype(np.float32)
        bb = b.astype(np.float32)
        intersection = a @ bb.T
        pop_a = a.sum(axis=1, keepdims=True)
        pop_b = bb.sum(axis=1, keepdims=True).T
        union = pop_a + pop_b - intersection
        with np.errstate(divide="ignore", invalid="ignore"):
            sim = np.where(union > 0, intersection / union, 0.0)
        return sim.astype(np.float32)


def rdkit_bitvect_tanimoto(mols: list[Chem.Mol], *, radius: int = DEFAULT_RADIUS,
                           n_bits: int = DEFAULT_N_BITS) -> np.ndarray:
    """Similarity via RDKit's own ``BulkTanimotoSimilarity``.

    Kept as a second independent CPU implementation so the vectorised NumPy
    path above can be checked against RDKit's C++ one in the benchmarks.
    """
    gen = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
    fps = [gen.GetFingerprint(m) for m in mols if m is not None]
    n = len(fps)
    sim = np.eye(n, dtype=np.float32)
    for i in range(n):
        if i + 1 >= n:
            continue
        row = DataStructs.BulkTanimotoSimilarity(fps[i], fps[i + 1 :])
        for offset, value in enumerate(row):
            j = i + 1 + offset
            sim[i, j] = sim[j, i] = value
    return sim


def get_backend(prefer_gpu: bool = False) -> tuple[ChemistryBackend, BackendReport]:
    """Return a backend and an honest report of what it is.

    ``prefer_gpu=True`` attempts nvMolKit and records the reason for any
    fallback. It never raises: callers that *require* GPU should check
    ``report.fell_back``.
    """
    report = BackendReport(requested="gpu" if prefer_gpu else "cpu", actual="rdkit-cpu")

    if not prefer_gpu:
        report.operations = {
            "morgan_fingerprints": "rdkit-cpu",
            "bulk_tanimoto": "numpy-cpu",
        }
        return RDKitBackend(), report

    # Imported lazily: on a CPU-only machine nvMolKit is not installed, and
    # importing torch would be expensive even if it were.
    try:
        from biologix.nvidia.nvmolkit import NvMolKitBackend, nvmolkit_availability

        availability = nvmolkit_availability()
        if not availability["available"]:
            report.fell_back = True
            report.actual = "rdkit-cpu"
            report.notes.append(f"nvMolKit unavailable: {availability['reason']}")
            report.operations = {
                "morgan_fingerprints": "rdkit-cpu",
                "bulk_tanimoto": "numpy-cpu",
            }
            return RDKitBackend(), report

        backend = NvMolKitBackend()
        report.actual = backend.name
        report.operations = {
            "morgan_fingerprints": backend.name,
            "bulk_tanimoto": backend.name,
        }
        report.notes.append(f"nvMolKit {availability.get('version', 'unknown')}")
        return backend, report
    except ImportError as exc:
        report.fell_back = True
        report.actual = "rdkit-cpu"
        report.notes.append(f"nvMolKit import failed: {exc}")
        report.operations = {
            "morgan_fingerprints": "rdkit-cpu",
            "bulk_tanimoto": "numpy-cpu",
        }
        return RDKitBackend(), report


def agreement(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """Numerical agreement between two implementations' outputs.

    Used by the benchmarks: a GPU path that is fast but disagrees with RDKit
    is a bug, not a speedup.
    """
    if a.shape != b.shape:
        return {"shape_mismatch": 1.0, "a_shape": float(a.size), "b_shape": float(b.size)}
    diff = np.abs(a.astype(np.float64) - b.astype(np.float64))
    return {
        "max_abs_diff": float(diff.max()) if diff.size else 0.0,
        "mean_abs_diff": float(diff.mean()) if diff.size else 0.0,
        "n_exact": float((diff == 0).sum()),
        "n_total": float(diff.size),
        "fraction_within_1e-5": float((diff <= 1e-5).mean()) if diff.size else 1.0,
    }

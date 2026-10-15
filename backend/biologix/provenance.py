"""Run manifests: what was run, with what, on what, and what came out.

Every operation that produces a number records a manifest. A manifest is
enough to (a) say exactly which software and model versions produced a result,
(b) re-run it, and (c) tell whether the result is a hypothesis, a computation
or a measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from biologix import __version__

# Packages whose versions materially affect numeric results.
_TRACKED_PACKAGES = (
    "rdkit",
    "openmm",
    "numpy",
    "scipy",
    "scikit-learn",
    "pandas",
    "nvmolkit",
    "torch",
    "openff-toolkit",
    "openmmforcefields",
)


def stable_hash(obj: Any, length: int = 16) -> str:
    """Content hash of a JSON-serialisable object, stable across processes.

    Used for cache keys and candidate identity. ``sort_keys`` makes the hash
    independent of dict ordering; ``default=str`` keeps it total.
    """
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def file_digest(path: str | Path) -> str:
    """SHA-256 of a file, for recording input structure provenance."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def package_versions() -> dict[str, str]:
    """Installed versions of packages that affect results."""
    out: dict[str, str] = {}
    for name in _TRACKED_PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "not-installed"
    return out


def git_revision() -> str:
    """Revision of this checkout, or a marker when unavailable."""
    try:
        here = Path(__file__).resolve().parent
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=here,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if rev.returncode != 0:
            return "unknown"
        head = rev.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=here,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        suffix = "-dirty" if dirty.stdout.strip() else ""
        return f"{head}{suffix}"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def hardware_report() -> dict[str, Any]:
    """Host description, including whether an NVIDIA GPU is actually present.

    We shell out to ``nvidia-smi`` rather than trusting a library's claim,
    because a CUDA-capable *build* is not a CUDA-capable *machine*.
    """
    report: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
    }
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            gpus = []
            for line in proc.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    gpus.append(
                        {
                            "name": parts[0],
                            "memory_total": parts[1],
                            "driver_version": parts[2],
                            "compute_capability": parts[3],
                        }
                    )
            report["nvidia_gpus"] = gpus
            report["gpu_present"] = bool(gpus)
        else:
            report["nvidia_gpus"] = []
            report["gpu_present"] = False
            report["gpu_probe"] = "nvidia-smi returned no devices"
    except FileNotFoundError:
        report["nvidia_gpus"] = []
        report["gpu_present"] = False
        report["gpu_probe"] = "nvidia-smi not found on PATH"
    except (OSError, subprocess.SubprocessError) as exc:
        report["nvidia_gpus"] = []
        report["gpu_present"] = False
        report["gpu_probe"] = f"nvidia-smi failed: {exc!r}"
    return report


@dataclass
class RunManifest:
    """A record of one computation.

    ``evidence`` must be one of the tiers in :mod:`biologix`. ``exclusions``
    records candidates or inputs dropped during the run and why — an empty
    list is a claim that nothing was dropped.
    """

    operation: str
    evidence: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    runtime_seconds: float | None = None

    inputs: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    seeds: dict[str, int] = field(default_factory=dict)

    software: dict[str, str] = field(default_factory=package_versions)
    biologix_version: str = __version__
    git_revision: str = field(default_factory=git_revision)
    hardware: dict[str, Any] = field(default_factory=hardware_report)

    # What actually executed, as opposed to what was requested. These must
    # never be assumed equal.
    requested_backend: str | None = None
    actual_backend: str | None = None

    models: list[dict[str, Any]] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)

    _t0: float = field(default_factory=time.perf_counter, repr=False)

    def record_model(
        self,
        name: str,
        version: str,
        *,
        kind: str,
        endpoint: str | None = None,
        licence: str | None = None,
        cached: bool = False,
    ) -> None:
        self.models.append(
            {
                "name": name,
                "version": version,
                "kind": kind,
                "endpoint": endpoint,
                "licence": licence,
                "cached": cached,
            }
        )

    def exclude(self, identity: str, reason: str, *, stage: str) -> None:
        self.exclusions.append({"identity": identity, "reason": reason, "stage": stage})

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def finish(self, **outputs: Any) -> RunManifest:
        self.outputs.update(outputs)
        self.finished_at = datetime.now(UTC).isoformat()
        self.runtime_seconds = round(time.perf_counter() - self._t0, 4)
        return self

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        return d

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str, sort_keys=False)

    def save(self, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.operation}-{self.run_id}.manifest.json"
        path.write_text(self.to_json(), encoding="utf-8")
        return path

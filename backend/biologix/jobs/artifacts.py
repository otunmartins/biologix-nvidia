"""Coordinate files a job produced, kept on disk and described in its result.

A docking pose, a predicted complex and an MD trajectory are all *coordinates*,
and coordinates do not belong in a JSON summary: they are large, they are
binary-ish, and a payload that inlines them cannot be read by anything that
wants to look at the structure. So each one is written to a file under the data
directory and listed in the job's result as an artifact, with enough
description that a viewer knows what it is looking at.

Every path is derived from the job id and a fixed filename, never from
anything a caller supplied, and reads are confined to the job's own directory.
That is the whole of the path-safety story here, and it is deliberate: file
serving is the one place in this API where a wrong path is a security problem
rather than a bug.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from biologix.settings import Settings, get_settings

#: What a viewer needs to render each kind, and how to describe it honestly.
KIND_LABEL = {
    "target": "prepared target structure",
    "pose": "docked oligomer pose",
    "complex": "predicted protein–oligomer complex",
    "trajectory": "molecular dynamics trajectory",
    "frame": "simulation snapshot",
}

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def job_directory(job_id: str, settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    if not _SAFE_ID.match(job_id):
        raise ValueError(f"unsafe job id {job_id!r}")
    return s.data_dir / "structures" / "jobs" / job_id


def write_artifact(
    job_id: str,
    *,
    name: str,
    content: str,
    kind: str,
    structure_format: str,
    description: str,
    settings: Settings | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one coordinate file and return its artifact record."""
    if not _SAFE_NAME.match(name):
        raise ValueError(f"unsafe artifact name {name!r}")
    directory = job_directory(job_id, settings)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    # `extra` is merged first, so nothing a caller passes can overwrite the
    # filename, kind or format — those are what the file is served under, and a
    # payload's own "name" field quietly replacing the path was a real bug.
    return {
        **(extra or {}),
        "name": name,
        "kind": kind,
        "label": KIND_LABEL.get(kind, kind),
        "format": structure_format,
        "description": description,
        "n_bytes": len(content.encode("utf-8")),
    }


def copy_artifact(
    job_id: str,
    source: Path,
    *,
    name: str,
    kind: str,
    structure_format: str,
    description: str,
    settings: Settings | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Copy a file the simulation wrote into the job's own directory."""
    if not source.exists():
        return None
    return write_artifact(
        job_id,
        name=name,
        content=source.read_text(encoding="utf-8", errors="replace"),
        kind=kind,
        structure_format=structure_format,
        description=description,
        settings=settings,
        extra=extra,
    )


def read_artifact(job_id: str, name: str, settings: Settings | None = None) -> str | None:
    """Read one of a job's artifacts, refusing anything outside its directory."""
    if not _SAFE_NAME.match(name):
        return None
    directory = job_directory(job_id, settings).resolve()
    path = (directory / name).resolve()
    # Belt and braces: the name is already pattern-checked, and the resolved
    # path must still sit inside the job's own directory.
    if not str(path).startswith(str(directory)) or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


#: How many models a multi-model PDB holds, which a viewer needs in order to
#: offer a frame slider without parsing the file twice.
def count_models(content: str) -> int:
    return content.count("\nMODEL ") + (1 if content.startswith("MODEL ") else 0)


__all__ = [
    "KIND_LABEL",
    "copy_artifact",
    "count_models",
    "job_directory",
    "read_artifact",
    "write_artifact",
]

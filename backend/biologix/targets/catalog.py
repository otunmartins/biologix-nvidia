"""The target catalogue: built-in targets plus everything users have added.

The in-process registry in :mod:`biologix.targets.registry` is the right shape
for a library — a dictionary a script can add to. It is the wrong shape for a
multi-user application, where a target one person adds has to survive a
restart and be visible to everyone else. This module is the bridge: targets
are rows in the database, loaded into the registry at startup and registered
as they are created.

Built-in targets are never stored as rows. They come from code, cannot be
edited or deleted, and are marked as such everywhere, so there is no doubt
about which targets are the demonstration set and which a lab added.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from biologix.errors import ValidationFailure
from biologix.store.database import session_scope
from biologix.store.models import Project, TargetRecord
from biologix.targets.models import Biologic, Modification
from biologix.targets.registry import list_targets as _list_registry
from biologix.targets.registry import register

RESERVED_KEYS = {"new", "import", "preview", "builtin"}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:48] or "target"


def unique_key(name: str, *, url: str | None = None) -> str:
    """A stable, readable key that collides with nothing already registered."""
    from biologix.targets.registry import _REGISTRY  # noqa: PLC0415 - internal by design

    base = slugify(name)
    if base in RESERVED_KEYS:
        base = f"{base}-target"
    taken = set(_REGISTRY)
    with session_scope(url) as session:
        taken.update(session.scalars(select(TargetRecord.key)).all())

    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise ValidationFailure(f"could not find a free key for {name!r}")


def _to_biologic(record: TargetRecord) -> Biologic:
    return Biologic.model_validate(
        {
            "name": record.name,
            "biologic_class": record.biologic_class,
            "chains": record.chains,
            "modifications": record.modifications or [],
            "pdb_id": record.pdb_id,
            "structure_source": record.structure_source,
            "uniprot_id": record.uniprot_id,
            "notes": record.notes,
        }
    )


def load_all(url: str | None = None) -> list[str]:
    """Register every stored target. Called once at startup.

    A row that no longer validates — because the model gained a constraint
    since it was written — is skipped rather than allowed to stop the server
    from starting, and its key is left out of the returned list so the
    omission is visible to the caller.
    """
    registered: list[str] = []
    with session_scope(url) as session:
        records = session.scalars(select(TargetRecord)).all()
        for record in records:
            try:
                register(record.key, _to_biologic(record), overwrite=True)
            except Exception:  # noqa: BLE001 - one bad row must not block startup
                continue
            registered.append(record.key)
    return registered


def save_target(
    *,
    biologic: Biologic,
    source: str,
    source_detail: str,
    created_by: str | None = None,
    created_by_name: str | None = None,
    key: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Persist a target and register it for immediate use."""
    resolved_key = key or unique_key(biologic.name, url=url)
    payload = biologic.model_dump(mode="json")

    with session_scope(url) as session:
        if session.get(TargetRecord, resolved_key):
            raise ValidationFailure(
                f"a target with the key {resolved_key!r} already exists",
                remedy="Give it a different name.",
            )
        record = TargetRecord(
            key=resolved_key,
            name=biologic.name,
            biologic_class=str(biologic.biologic_class),
            chains=payload["chains"],
            modifications=payload.get("modifications") or [],
            pdb_id=biologic.pdb_id,
            structure_source=biologic.structure_source,
            uniprot_id=biologic.uniprot_id,
            notes=biologic.notes,
            source=source,
            source_detail=source_detail,
            created_by=created_by,
            created_by_name=created_by_name,
        )
        session.add(record)
        session.flush()
        stored = record.to_dict()

    register(resolved_key, biologic, overwrite=True)
    return stored


def add_modifications(
    key: str, modifications: list[dict[str, Any]], *, url: str | None = None
) -> dict[str, Any]:
    """Record chemistry a sequence model cannot see: disulfides, PEGylation.

    Stated by a person, never inferred. An invented modification would be
    carried into every downstream claim about the target.
    """
    with session_scope(url) as session:
        record = session.get(TargetRecord, key)
        if record is None:
            raise ValidationFailure(
                f"{key!r} is not a stored target",
                remedy="Built-in targets come from code and cannot be edited.",
            )
        # Validated through the model, so a bad site or kind is refused here
        # rather than at the next read.
        validated = [Modification.model_validate(m).model_dump(mode="json") for m in modifications]
        record.modifications = validated
        session.flush()
        stored = record.to_dict()

    register(key, _to_biologic_from_dict(stored), overwrite=True)
    return stored


def _to_biologic_from_dict(stored: dict[str, Any]) -> Biologic:
    return Biologic.model_validate(
        {
            "name": stored["name"],
            "biologic_class": stored["biologic_class"],
            "chains": stored["chains"],
            "modifications": stored["modifications"],
            "pdb_id": stored["pdb_id"],
            "structure_source": stored["structure_source"],
            "uniprot_id": stored["uniprot_id"],
            "notes": stored["notes"],
        }
    )


def delete_target(key: str, *, url: str | None = None) -> dict[str, Any]:
    """Remove a stored target, unless a program depends on it.

    A program's target is fixed for its lifetime, so deleting one out from
    under it would leave campaigns whose subject cannot be identified.
    """
    with session_scope(url) as session:
        record = session.get(TargetRecord, key)
        if record is None:
            raise ValidationFailure(
                f"{key!r} is not a stored target",
                remedy="Built-in targets come from code and cannot be deleted.",
            )
        programs = session.scalars(select(Project.name).where(Project.target_key == key)).all()
        if programs:
            raise ValidationFailure(
                f"{len(programs)} program{'s' if len(programs) != 1 else ''} use this "
                f"target: {', '.join(programs[:3])}" + (" …" if len(programs) > 3 else ""),
                remedy=(
                    "A program's target is fixed for its lifetime. Archive those "
                    "programs first if the target really should go."
                ),
            )
        session.delete(record)

    from biologix.targets.registry import _REGISTRY  # noqa: PLC0415

    _REGISTRY.pop(key, None)
    return {"deleted": key}


def catalogue(url: str | None = None) -> list[dict[str, Any]]:
    """Every target, with where it came from and what it can be used for."""
    stored: dict[str, TargetRecord] = {}
    with session_scope(url) as session:
        for record in session.scalars(select(TargetRecord)).all():
            stored[record.key] = record

    out: list[dict[str, Any]] = []
    for entry in _list_registry():
        record = stored.get(str(entry["key"]))
        pdb_id = entry.get("pdb_id")
        out.append(
            {
                **entry,
                "source": record.source if record else "built-in",
                "source_detail": record.source_detail if record else "defined in code",
                "added_by": record.created_by_name if record else None,
                "created_at": (
                    record.created_at.isoformat() if record and record.created_at else None
                ),
                "editable": record is not None,
                # What this target can actually support, stated per target
                # rather than discovered when a job fails.
                "supports_docking": bool(pdb_id),
                "capability_note": (
                    "Has a PDB entry, so docking, structure preparation and simulation can run."
                    if pdb_id
                    else (
                        "No PDB entry, so DiffDock and simulation have no coordinates to "
                        "work from. Sequence-based work — Boltz-2 complex prediction, "
                        "ESM-2 embedding — still runs."
                    )
                ),
            }
        )
    return out


__all__ = [
    "RESERVED_KEYS",
    "add_modifications",
    "catalogue",
    "delete_target",
    "load_all",
    "save_target",
    "slugify",
    "unique_key",
]

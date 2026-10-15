"""Adding and managing biologic targets.

Three ways in — paste a sequence, a PDB id, a UniProt accession — and a
deliberate two-step: ``/targets/preview`` parses and reports without storing
anything, then ``/targets`` stores what the user has actually looked at. The
preview exists because an import can be subtly wrong in ways only a person can
catch: chains split where they should not be, a crystal structure missing its
termini, a chain over ESM-2's ceiling. Storing first and showing afterwards
would make those discoveries into cleanup work.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from biologix.api.deps import CurrentUser
from biologix.errors import ValidationFailure
from biologix.settings import Settings
from biologix.store.workspace import log
from biologix.targets.catalog import (
    add_modifications,
    catalogue,
    delete_target,
    save_target,
)
from biologix.targets.imports import (
    ESM2_MAX_RESIDUES,
    ImportedTarget,
    build_biologic,
    from_pdb,
    from_uniprot,
    parse_fasta,
)
from biologix.targets.models import BiologicClass, Chain, ModificationKind


class ImportRequest(BaseModel):
    """Where the sequence comes from. Exactly one source is used."""

    source: str = Field(description="pasted | pdb | uniprot")
    name: str | None = Field(default=None, max_length=200)
    # pasted
    sequences: str | None = Field(default=None, max_length=200_000)
    # pdb
    pdb_id: str | None = Field(default=None, max_length=8)
    # uniprot
    uniprot_id: str | None = Field(default=None, max_length=16)


class ChainInput(BaseModel):
    chain_id: str = Field(min_length=1, max_length=4)
    sequence: str = Field(min_length=1, max_length=100_000)
    description: str | None = Field(default=None, max_length=300)


class CreateTargetRequest(BaseModel):
    """A target the user has previewed and is now committing."""

    name: str = Field(min_length=1, max_length=200)
    biologic_class: str = "protein"
    chains: list[ChainInput] = Field(min_length=1)
    source: str = "pasted"
    source_detail: str | None = Field(default=None, max_length=1000)
    pdb_id: str | None = Field(default=None, max_length=8)
    uniprot_id: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=4000)


class ModificationInput(BaseModel):
    kind: str
    sites: list[tuple[str, int]] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=300)
    attached_smiles: str | None = Field(default=None, max_length=1000)


class ModificationsRequest(BaseModel):
    modifications: list[ModificationInput]


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["targets"])
    url = settings.database_url

    @router.get("/targets", summary="Every biologic target, built-in and added")
    def targets(user: CurrentUser) -> dict[str, Any]:
        return {
            "targets": catalogue(url=url),
            "classes": [c.value for c in BiologicClass],
            "modification_kinds": [k.value for k in ModificationKind],
            "esm2_max_residues": ESM2_MAX_RESIDUES,
            "note": (
                "Chains are never concatenated into one sequence. A target without a "
                "PDB entry supports sequence-based work but not docking or simulation."
            ),
        }

    @router.post("/targets/preview", summary="Parse an import without storing it")
    def preview(body: ImportRequest, user: CurrentUser) -> dict[str, Any]:
        try:
            imported = _import(body, settings)
        except ValidationFailure as exc:
            raise HTTPException(
                status_code=400,
                detail={"message": str(exc), "remedy": getattr(exc, "remedy", None)},
            ) from None
        except Exception as exc:  # noqa: BLE001 - an import reaches the network
            raise HTTPException(status_code=400, detail={"message": str(exc)}) from None

        payload = imported.to_dict()
        payload["can_dock"] = bool(imported.pdb_id)
        payload["capability_note"] = (
            "Has a PDB entry, so DiffDock, structure preparation and simulation can run."
            if imported.pdb_id
            else (
                "No PDB entry. Boltz-2 complex prediction and ESM-2 embedding work from "
                "sequence, but DiffDock and MD simulation need coordinates — add a PDB "
                "id if you want those."
            )
        )
        payload["modifications_note"] = (
            "No modifications are inferred. Disulfides, PEGylation, glycosylation and "
            "acylation are invisible to a sequence model, so they must be stated "
            "explicitly to be carried anywhere."
        )
        return payload

    @router.post("/targets", status_code=201, summary="Add a biologic target")
    def create(body: CreateTargetRequest, user: CurrentUser) -> dict[str, Any]:
        try:
            imported = ImportedTarget(
                name=body.name,
                chains=[
                    Chain(
                        chain_id=chain.chain_id,
                        sequence=chain.sequence,
                        description=chain.description,
                    )
                    for chain in body.chains
                ],
                source=body.source,
                source_detail=body.source_detail or "entered by hand",
                pdb_id=(body.pdb_id or "").strip().upper() or None,
                uniprot_id=(body.uniprot_id or "").strip().upper() or None,
                notes=body.notes,
            )
            biologic = build_biologic(
                imported, biologic_class=body.biologic_class, name=body.name, notes=body.notes
            )
            stored = save_target(
                biologic=biologic,
                source=body.source,
                source_detail=imported.source_detail,
                created_by=user["id"],
                created_by_name=user["display_name"],
                url=url,
            )
        except ValidationFailure as exc:
            raise HTTPException(
                status_code=400,
                detail={"message": str(exc), "remedy": getattr(exc, "remedy", None)},
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"message": str(exc)}) from None

        log(
            kind="target.added",
            summary=(
                f"added the target {biologic.name} "
                f"({len(biologic.chains)} chain{'s' if len(biologic.chains) != 1 else ''}, "
                f"{biologic.total_residues} residues, from {body.source})"
            ),
            user_id=user["id"],
            actor_name=user["display_name"],
            detail={"target_key": stored["key"], "source": body.source},
            url=url,
        )
        return stored

    @router.put(
        "/targets/{key}/modifications",
        summary="State chemistry a sequence model cannot see",
    )
    def modifications(key: str, body: ModificationsRequest, user: CurrentUser) -> dict[str, Any]:
        try:
            stored = add_modifications(
                key,
                [m.model_dump() for m in body.modifications],
                url=url,
            )
        except ValidationFailure as exc:
            raise HTTPException(
                status_code=400,
                detail={"message": str(exc), "remedy": getattr(exc, "remedy", None)},
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"message": str(exc)}) from None

        log(
            kind="target.modified",
            summary=f"recorded {len(body.modifications)} modification(s) on {stored['name']}",
            user_id=user["id"],
            actor_name=user["display_name"],
            detail={"target_key": key},
            url=url,
        )
        return stored

    @router.delete("/targets/{key}", summary="Remove an added target")
    def remove(key: str, user: CurrentUser) -> dict[str, Any]:
        try:
            result = delete_target(key, url=url)
        except ValidationFailure as exc:
            # 409 rather than 400: the request is well formed, but a program
            # depends on this target.
            status = 409 if "program" in str(exc) else 400
            raise HTTPException(
                status_code=status,
                detail={"message": str(exc), "remedy": getattr(exc, "remedy", None)},
            ) from None

        log(
            kind="target.removed",
            summary=f"removed the target {key}",
            user_id=user["id"],
            actor_name=user["display_name"],
            url=url,
        )
        return result

    return router


def _import(body: ImportRequest, settings: Settings) -> ImportedTarget:
    source = body.source.strip().lower()
    if source == "pasted":
        if not body.sequences:
            raise ValidationFailure(
                "no sequence was supplied",
                remedy="Paste a FASTA record, several records, or a bare sequence.",
            )
        return parse_fasta(body.sequences, name=body.name)
    if source == "pdb":
        if not body.pdb_id:
            raise ValidationFailure("no PDB id was supplied", remedy="For example, 3I40.")
        return from_pdb(body.pdb_id, name=body.name)
    if source == "uniprot":
        if not body.uniprot_id:
            raise ValidationFailure(
                "no UniProt accession was supplied", remedy="For example, P01308."
            )
        return from_uniprot(body.uniprot_id, name=body.name, settings=settings)
    raise ValidationFailure(
        f"unknown source {body.source!r}",
        remedy="Use pasted, pdb or uniprot.",
    )


__all__ = ["build_router"]

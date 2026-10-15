"""Programs, triage decisions, shortlists and the audit trail.

Who may do what, stated once: this is a single-lab tool, so every signed-in
user can *read* every program — that is what makes the reviewer journey
possible — and anyone may launch a campaign or record a decision in a program,
because collaboration is the normal case and the audit trail names whoever
did it. Only a program's owner may archive it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from biologix.api.deps import CurrentUser
from biologix.settings import Settings
from biologix.store import get_run, list_jobs
from biologix.store.workspace import (
    VERDICTS,
    archive_project,
    clear_decision,
    create_project,
    get_project,
    list_activity,
    list_decisions,
    list_project_runs,
    list_projects,
    log,
    project_shortlist,
    record_decision,
)
from biologix.targets.registry import get_target


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_key: str
    description: str | None = Field(default=None, max_length=2000)


class DecisionRequest(BaseModel):
    verdict: str = Field(description=" or ".join(VERDICTS))
    rationale: str = Field(min_length=1, max_length=2000)
    weights: dict[str, float] | None = None
    order_score: float | None = None


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["workspace"])
    url = settings.database_url

    def _project_or_404(project_id: str) -> dict[str, Any]:
        project = get_project(project_id, url=url)
        if project is None:
            raise HTTPException(status_code=404, detail=f"no program {project_id!r}")
        return project

    @router.post("/projects", status_code=201, summary="Create a program")
    def new_project(body: ProjectRequest, user: CurrentUser) -> dict[str, Any]:
        try:
            target = get_target(body.target_key)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc.args[0])) from None
        project = create_project(
            owner_id=user["id"],
            name=body.name,
            target_key=body.target_key,
            target_name=target.name,
            description=body.description,
            url=url,
        )
        log(
            kind="program.created",
            summary=f"Program “{project['name']}” opened against {target.name}",
            project_id=project["id"],
            user_id=user["id"],
            actor_name=user["display_name"],
            url=url,
        )
        return project

    @router.get("/projects", summary="Programs, with their campaign and shortlist counts")
    def projects(
        user: CurrentUser,
        mine: bool = Query(default=False, description="only programs you own"),
        include_archived: bool = False,
    ) -> dict[str, Any]:
        return {
            "projects": list_projects(
                owner_id=user["id"] if mine else None,
                include_archived=include_archived,
                url=url,
            )
        }

    @router.get("/projects/{project_id}", summary="One program with its campaigns")
    def project_detail(project_id: str, user: CurrentUser) -> dict[str, Any]:
        project = _project_or_404(project_id)
        return {
            **project,
            "is_owner": project["owner_id"] == user["id"],
            "target": _target_summary(project["target_key"]),
            "campaigns": list_project_runs(project_id, url=url),
            "shortlist": project_shortlist(project_id, url=url),
            "jobs": list_jobs(project_id=project_id, limit=40, url=url),
            "activity": list_activity(project_id=project_id, limit=40, url=url),
        }

    @router.post("/projects/{project_id}/archive", summary="Archive or restore a program")
    def archive(project_id: str, user: CurrentUser, archived: bool = True) -> dict[str, Any]:
        project = _project_or_404(project_id)
        if project["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="only the program's owner can archive it")
        archive_project(project_id, archived=archived, url=url)
        log(
            kind="program.archived" if archived else "program.restored",
            summary=f"Program “{project['name']}” {'archived' if archived else 'restored'}",
            project_id=project_id,
            user_id=user["id"],
            actor_name=user["display_name"],
            url=url,
        )
        return {"id": project_id, "archived": archived}

    @router.get("/projects/{project_id}/shortlist", summary="The program's shortlist")
    def shortlist(project_id: str, user: CurrentUser) -> dict[str, Any]:
        _project_or_404(project_id)
        return {
            "shortlist": project_shortlist(project_id, url=url),
            "note": (
                "Shortlisted by a person, for the reason attached. Nothing here has "
                "been measured in a laboratory."
            ),
        }

    @router.put(
        "/runs/{run_id}/candidates/{candidate_id}/decision",
        summary="Shortlist or reject a candidate, with a reason",
    )
    def decide(
        run_id: str,
        candidate_id: str,
        body: DecisionRequest,
        user: CurrentUser,
    ) -> dict[str, Any]:
        run = get_run(run_id, url=url)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        if not any(c["candidate_id"] == candidate_id for c in run["candidates"]):
            raise HTTPException(
                status_code=404, detail=f"no candidate {candidate_id!r} in run {run_id!r}"
            )
        if not run["project_id"]:
            raise HTTPException(
                status_code=400,
                detail="this run does not belong to a program, so it has no shortlist",
            )
        try:
            decision = record_decision(
                project_id=run["project_id"],
                run_id=run_id,
                candidate_id=candidate_id,
                user_id=user["id"],
                verdict=body.verdict,
                rationale=body.rationale,
                weights=body.weights,
                order_score=body.order_score,
                url=url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        log(
            kind=f"candidate.{body.verdict}",
            summary=f"{candidate_id} {body.verdict}: {body.rationale[:140]}",
            project_id=run["project_id"],
            user_id=user["id"],
            actor_name=user["display_name"],
            run_id=run_id,
            candidate_id=candidate_id,
            detail={"verdict": body.verdict, "order_score": body.order_score},
            url=url,
        )
        return decision

    @router.delete(
        "/runs/{run_id}/candidates/{candidate_id}/decision",
        summary="Withdraw your decision on a candidate",
    )
    def undecide(run_id: str, candidate_id: str, user: CurrentUser) -> dict[str, Any]:
        removed = clear_decision(
            run_id=run_id, candidate_id=candidate_id, user_id=user["id"], url=url
        )
        if not removed:
            raise HTTPException(status_code=404, detail="you have no decision on that candidate")
        log(
            kind="candidate.undecided",
            summary=f"Decision withdrawn on {candidate_id}",
            user_id=user["id"],
            actor_name=user["display_name"],
            run_id=run_id,
            candidate_id=candidate_id,
            url=url,
        )
        return {"withdrawn": candidate_id}

    @router.get("/runs/{run_id}/decisions", summary="Every decision on this campaign")
    def run_decisions(run_id: str, user: CurrentUser) -> dict[str, Any]:
        return {"decisions": list_decisions(run_id=run_id, url=url)}

    @router.get("/activity", summary="The audit trail")
    def activity(
        user: CurrentUser,
        project_id: str | None = None,
        mine: bool = False,
        limit: int = Query(default=60, ge=1, le=300),
    ) -> dict[str, Any]:
        return {
            "activity": list_activity(
                project_id=project_id,
                user_id=user["id"] if mine else None,
                limit=limit,
                url=url,
            )
        }

    return router


def _target_summary(target_key: str) -> dict[str, Any] | None:
    try:
        biologic = get_target(target_key)
    except KeyError:
        return None
    return {
        "key": target_key,
        "name": biologic.name,
        "class": str(biologic.biologic_class),
        "n_chains": biologic.n_chains,
        "total_residues": biologic.total_residues,
        "chains": [
            {"chain_id": c.chain_id, "length": c.length, "description": c.description}
            for c in biologic.chains
        ],
        "modifications": biologic.modification_summary(),
        "pdb_id": biologic.pdb_id,
    }


__all__ = ["build_router"]

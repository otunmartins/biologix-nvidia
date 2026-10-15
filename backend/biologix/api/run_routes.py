"""Campaigns: starting them, reading them, ordering them, and acting on one
candidate with DiffDock or Boltz-2.

The ordering rule from the store applies at this boundary too: ranking is
computed per request from the caller's weights and never stored, so a ranked
view is this reader's view and not a property of the candidates.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from biologix.api.deps import CurrentUser
from biologix.jobs import GENERATORS, JOB_KINDS, MAX_CANDIDATES, MAX_POSES
from biologix.jobs.artifacts import read_artifact
from biologix.polymers.conformer import ConformerFailed, embed_repeat_unit_cached
from biologix.ranking import BASIS_NOTE, CRITERIA, DEFAULT_WEIGHTS, rank_candidates
from biologix.screening.filters import CONSTRAINT_SETS
from biologix.settings import Settings
from biologix.store import delete_run, get_job, get_run, get_run_candidate, list_jobs, list_runs
from biologix.store.workspace import get_project, list_decisions, log


class RunRequest(BaseModel):
    """What a caller may choose about a campaign."""

    target_key: str | None = Field(
        default=None,
        description="omit inside a program: the program's target is used, and cannot differ",
    )
    project_id: str | None = None
    generator: str = Field(default="baseline", description=", ".join(GENERATORS))
    n: int = Field(default=12, ge=1, le=MAX_CANDIDATES)
    constraint_set: str | None = None
    seed: int | None = None
    label: str | None = Field(default=None, max_length=200)


class AnalysisRequest(BaseModel):
    kind: str = Field(description=" or ".join(JOB_KINDS))
    degree_of_polymerisation: int = Field(default=10, ge=1, le=200)
    num_poses: int = Field(default=5, ge=1, le=MAX_POSES)
    #: Simulation length. Short by default: this illustrates a complex rather
    #: than measuring anything, and the result says so.
    production_steps: int = Field(default=6_000, ge=500, le=200_000)


def parse_weights(raw: str | None) -> dict[str, float] | None:
    """``flexibility:2,polarity:1`` into a dict; nothing means the defaults.

    Weights arrive in a query string so a ranked view is a shareable URL.
    """
    if not raw:
        return None
    weights: dict[str, float] = {}
    for part in raw.split(","):
        key, separator, value = part.partition(":")
        if not separator:
            raise ValueError(f"weight {part!r} is not in key:value form")
        try:
            weights[key.strip()] = float(value)
        except ValueError:
            raise ValueError(f"weight for {key.strip()!r} is not a number") from None
    return weights


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["campaigns"])
    url = settings.database_url

    def _run_or_404(run_id: str) -> dict[str, Any]:
        run = get_run(run_id, url=url)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        return run

    @router.get("/options", summary="How a campaign and a ranking can be configured")
    def options(user: CurrentUser) -> dict[str, Any]:
        return {
            "generators": list(GENERATORS),
            "analyses": list(JOB_KINDS),
            "max_candidates": MAX_CANDIDATES,
            "max_poses": MAX_POSES,
            "constraint_sets": [cs.describe() for cs in CONSTRAINT_SETS.values()],
            "ranking_criteria": [c.describe() for c in CRITERIA],
            "default_weights": dict(DEFAULT_WEIGHTS),
            "note": (
                "Constraint thresholds are editable heuristics with stated rationales, "
                "not regulatory limits. Ranking weights are the reader's: nothing here "
                "prescribes them."
            ),
        }

    @router.post("/runs", status_code=202, summary="Start a campaign")
    def start_run(body: RunRequest, request: Request, user: CurrentUser) -> dict[str, Any]:
        target_key = body.target_key
        if body.project_id:
            project = get_project(body.project_id, url=url)
            if project is None:
                raise HTTPException(status_code=404, detail=f"no program {body.project_id!r}")
            # The program fixes the target. A campaign against a different
            # protein would make the program's own history unreadable.
            if target_key and target_key != project["target_key"]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"this program is against {project['target_key']}; a campaign "
                        "cannot use a different target"
                    ),
                )
            target_key = project["target_key"]
        if not target_key:
            raise HTTPException(
                status_code=400, detail="a campaign needs either a program or a target"
            )

        try:
            run_id = request.app.state.queue.submit_run(
                target_key=target_key,
                generator=body.generator,
                n=body.n,
                constraint_set=body.constraint_set,
                seed=body.seed,
                label=body.label,
                project_id=body.project_id,
                user_id=user["id"],
                actor_name=user["display_name"],
            )
        except (KeyError, ValueError) as exc:
            detail = str(exc.args[0]) if exc.args else type(exc).__name__
            raise HTTPException(status_code=400, detail=detail) from None

        return {
            "run_id": run_id,
            "status": "queued",
            "poll": f"/runs/{run_id}",
            "note": (
                "An unavailable NVIDIA generator fails this campaign with its reason; "
                "it is never silently replaced by the baseline."
            ),
        }

    @router.get("/runs", summary="Recent campaigns, newest first")
    def runs(
        user: CurrentUser,
        limit: int = Query(default=50, ge=1, le=200),
        project_id: str | None = None,
    ) -> dict[str, Any]:
        rows = list_runs(limit=limit, url=url)
        if project_id:
            rows = [r for r in rows if r["project_id"] == project_id]
        return {"runs": rows}

    @router.get("/runs/{run_id}", summary="One campaign, including what it rejected")
    def run_detail(run_id: str, user: CurrentUser) -> dict[str, Any]:
        run = _run_or_404(run_id)
        return {
            **run,
            "evidence": "computed",
            "decisions": list_decisions(run_id=run_id, url=url),
            "jobs": list_jobs(run_id=run_id, limit=60, url=url),
            "note": (
                "Descriptors describe a short capped oligomer, not a polydisperse "
                "polymer. Discards are included deliberately: the rejection reasons "
                "say what the generator is doing wrong."
            ),
        }

    @router.delete("/runs/{run_id}", summary="Delete a campaign and everything in it")
    def remove_run(run_id: str, user: CurrentUser) -> dict[str, Any]:
        run = _run_or_404(run_id)
        if not delete_run(run_id, url=url):
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        log(
            kind="campaign.deleted",
            summary=f"Campaign {run_id} deleted with its {run['n_candidates']} candidates",
            project_id=run["project_id"],
            user_id=user["id"],
            actor_name=user["display_name"],
            url=url,
        )
        return {"deleted": run_id}

    @router.get("/runs/{run_id}/ranking", summary="A campaign's candidates in your order")
    def run_ranking(
        run_id: str,
        user: CurrentUser,
        weights: str | None = Query(
            default=None,
            description="key:value pairs, e.g. flexibility:2,polarity:1. Omit for defaults.",
        ),
        passed_only: bool = Query(default=True, description="exclude candidates that failed"),
    ) -> dict[str, Any]:
        run = _run_or_404(run_id)
        pool = [
            c
            for c in run["candidates"]
            if (c["passed"] or not passed_only) and not c["descriptor_error"]
        ]
        descriptors = {c["candidate_id"]: c["descriptors"] for c in pool}
        distances = {
            c["candidate_id"]: c["cohort_distance"]
            for c in pool
            if c["cohort_distance"] is not None
        }
        # The synthetic accessibility of the *monomer* the route needs; a
        # candidate with no route on record simply has no value here.
        synthesis = {}
        for candidate in pool:
            route = (candidate.get("synthesis") or {}).get("route") or {}
            score = route.get("sa_score")
            if isinstance(score, (int, float)):
                synthesis[candidate["candidate_id"]] = float(score)
        try:
            ranking = rank_candidates(
                descriptors,
                weights=parse_weights(weights),
                diversity=distances or None,
                synthesis=synthesis or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        # Each person's own verdict travels with the row, so a triage view
        # shows what *you* decided rather than a merged opinion.
        decisions = {
            d["candidate_id"]: d
            for d in list_decisions(run_id=run_id, url=url)
            if d["user_id"] == user["id"]
        }
        jobs_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for job in list_jobs(run_id=run_id, limit=200, url=url):
            if job["candidate_id"]:
                jobs_by_candidate.setdefault(job["candidate_id"], []).append(job)

        by_id = {c["candidate_id"]: c for c in pool}
        payload = ranking.to_dict()
        ordered = [
            {
                **by_id[row["candidate_id"]],
                **row,
                "my_decision": decisions.get(row["candidate_id"]),
                "jobs": jobs_by_candidate.get(row["candidate_id"], []),
            }
            for row in payload.pop("ranked")
        ]
        return {
            **payload,
            "run": {
                key: run[key]
                for key in (
                    "id",
                    "status",
                    "label",
                    "target_key",
                    "target_name",
                    "generator",
                    "project_id",
                )
            },
            "n_excluded": len(run["candidates"]) - len(pool),
            "candidates": ordered,
            "basis": BASIS_NOTE,
        }

    @router.get(
        "/runs/{run_id}/candidates/{candidate_id}",
        summary="One candidate, with the constraints it was judged against",
    )
    def candidate_detail(run_id: str, candidate_id: str, user: CurrentUser) -> dict[str, Any]:
        candidate = get_run_candidate(run_id, candidate_id, url=url)
        if candidate is None:
            raise HTTPException(
                status_code=404, detail=f"no candidate {candidate_id!r} in run {run_id!r}"
            )
        mine = [
            d for d in list_decisions(run_id=run_id, url=url) if d["candidate_id"] == candidate_id
        ]
        # What the 3D panel will show, so the page can label the picture and
        # size its own request. Embedding failures are reported here rather
        # than left for the viewer to discover as a blank panel.
        structure: dict[str, Any] = {"available": False, "reason": None}
        units = candidate.get("repeat_units") or []
        if units:
            try:
                conformer = embed_repeat_unit_cached(
                    units[0], int(candidate.get("degree_of_polymerisation") or 25)
                )
                structure = {
                    "available": True,
                    "reason": None,
                    "url": f"/runs/{run_id}/candidates/{candidate_id}/structure",
                    **conformer.metadata(),
                }
            except ConformerFailed as exc:
                structure = {"available": False, "reason": str(exc)}

        return {
            **candidate,
            "evidence": "computed",
            "structure": structure,
            "jobs": list_jobs(run_id=run_id, candidate_id=candidate_id, limit=40, url=url),
            "decisions": mine,
            "my_decision": next((d for d in mine if d["user_id"] == user["id"]), None),
        }

    @router.post(
        "/runs/{run_id}/candidates/{candidate_id}/analyses",
        status_code=202,
        summary="Run DiffDock or Boltz-2 on this candidate",
    )
    def start_analysis(
        run_id: str,
        candidate_id: str,
        body: AnalysisRequest,
        request: Request,
        user: CurrentUser,
    ) -> dict[str, Any]:
        candidate = get_run_candidate(run_id, candidate_id, url=url)
        if candidate is None:
            raise HTTPException(
                status_code=404, detail=f"no candidate {candidate_id!r} in run {run_id!r}"
            )
        run = _run_or_404(run_id)
        repeat_units = candidate["repeat_units"]
        if not repeat_units:
            raise HTTPException(
                status_code=400, detail="this candidate has no repeat unit to build from"
            )

        try:
            job_id = request.app.state.queue.submit_analysis(
                kind=body.kind,
                target_key=run["target_key"],
                # The first block's repeat unit: a copolymer is docked as its
                # leading block, which the job payload records explicitly.
                repeat_unit_smiles=repeat_units[0],
                degree_of_polymerisation=body.degree_of_polymerisation,
                num_poses=body.num_poses,
                production_steps=body.production_steps,
                run_id=run_id,
                candidate_id=candidate_id,
                project_id=run["project_id"],
                user_id=user["id"],
                actor_name=user["display_name"],
            )
        except (KeyError, ValueError) as exc:
            detail = str(exc.args[0]) if exc.args else type(exc).__name__
            raise HTTPException(status_code=400, detail=detail) from None

        return {
            "job_id": job_id,
            "kind": body.kind,
            "status": "queued",
            "poll": f"/jobs/{job_id}",
            "note": (
                "An unavailable adapter fails this job with its own reason and remedy, "
                "which is stored with the job rather than discarded."
            ),
        }

    @router.get(
        "/runs/{run_id}/candidates/{candidate_id}/structure",
        summary="3D coordinates for this candidate's own oligomer",
    )
    def candidate_structure(
        run_id: str,
        candidate_id: str,
        user: CurrentUser,
        fmt: str = Query("pdb", pattern="^(pdb|sdf)$"),
    ) -> Response:
        """A conformer of the candidate itself, with no target and no GPU.

        This is the structure a user can see during triage, before committing
        a docking or simulation job. It is computed on demand from the stored
        repeat unit, memoised, and labelled with the caveat that it is one
        vacuum conformer rather than a formulation prediction.
        """
        candidate = get_run_candidate(run_id, candidate_id, url=url)
        if candidate is None:
            raise HTTPException(
                status_code=404, detail=f"no candidate {candidate_id!r} in run {run_id!r}"
            )
        units = candidate.get("repeat_units") or []
        if not units:
            raise HTTPException(
                status_code=404, detail="this candidate has no repeat unit to embed"
            )
        try:
            conformer = embed_repeat_unit_cached(
                units[0], int(candidate.get("degree_of_polymerisation") or 25)
            )
        except ConformerFailed as exc:
            # A candidate that cannot be embedded is a real answer, not a
            # server error: the reason is the useful part.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        content = conformer.pdb if fmt == "pdb" else conformer.sdf
        return Response(
            content=content,
            media_type="chemical/x-pdb" if fmt == "pdb" else "chemical/x-mdl-sdfile",
            headers={
                "cache-control": "private, max-age=3600",
                "x-biologix-evidence": "computed",
            },
        )

    @router.get(
        "/jobs/{job_id}/structures/{name}",
        summary="One coordinate file a job produced",
    )
    def job_structure(job_id: str, name: str, user: CurrentUser) -> Response:
        """Raw coordinates, for a structure viewer.

        Served as text from the job's own directory. The job id and filename
        are both pattern-checked, and the resolved path must sit inside that
        directory, so this cannot be used to read anything else.
        """
        content = read_artifact(job_id, name)
        if content is None:
            raise HTTPException(status_code=404, detail=f"no structure {name!r} for {job_id!r}")
        suffix = name.rsplit(".", 1)[-1].lower()
        media = {
            "pdb": "chemical/x-pdb",
            "sdf": "chemical/x-mdl-sdfile",
            "mol2": "chemical/x-mol2",
            "cif": "chemical/x-cif",
            "mmcif": "chemical/x-cif",
        }.get(suffix, "text/plain")
        return Response(
            content=content,
            media_type=media,
            headers={"cache-control": "private, max-age=600"},
        )

    @router.get("/jobs/{job_id}", summary="One analysis job")
    def job_detail(job_id: str, user: CurrentUser) -> dict[str, Any]:
        job = get_job(job_id, url=url)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
        return job

    @router.get("/jobs", summary="Analysis jobs, newest first")
    def jobs(
        user: CurrentUser,
        run_id: str | None = None,
        candidate_id: str | None = None,
        project_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return {
            "jobs": list_jobs(
                run_id=run_id,
                candidate_id=candidate_id,
                project_id=project_id,
                limit=limit,
                url=url,
            )
        }

    return router


__all__ = ["build_router", "parse_weights"]

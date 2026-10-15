"""Reading and writing runs and jobs.

Everything here takes its own session and returns plain dictionaries rather
than live ORM objects. That keeps the job threads and the request handlers from
sharing session state, and means a caller cannot accidentally hold a detached
instance whose lazy loads fail later.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from biologix.store.database import session_scope
from biologix.store.models import Candidate, Discard, Job, Run

TERMINAL = ("succeeded", "failed", "cancelled")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now(UTC)


def _loaded_run(session: Session, run_id: str) -> Run | None:
    return session.scalars(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.candidates), selectinload(Run.discards))
    ).first()


# -- runs ------------------------------------------------------------------


def create_run(
    *,
    target_key: str,
    target_name: str | None,
    generator: str,
    n_requested: int,
    constraint_set: str,
    seed: int | None,
    label: str | None = None,
    params: dict[str, Any] | None = None,
    project_id: str | None = None,
    created_by: str | None = None,
    created_by_name: str | None = None,
    url: str | None = None,
) -> str:
    run_id = new_id()
    with session_scope(url) as session:
        session.add(
            Run(
                id=run_id,
                status="queued",
                stage="queued",
                progress=0.0,
                label=label,
                project_id=project_id,
                created_by=created_by,
                created_by_name=created_by_name,
                target_key=target_key,
                target_name=target_name,
                generator=generator,
                n_requested=n_requested,
                constraint_set=constraint_set,
                seed=seed,
                params=params or {},
            )
        )
    return run_id


def set_run_progress(run_id: str, *, stage: str, progress: float, url: str | None = None) -> None:
    with session_scope(url) as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        run.status = "running"
        run.stage = stage
        run.progress = max(0.0, min(1.0, progress))


def fail_run(run_id: str, error: str, url: str | None = None) -> None:
    with session_scope(url) as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        run.status = "failed"
        run.stage = "failed"
        run.error = error
        run.finished_at = _now()


def save_run_results(
    run_id: str,
    *,
    statistics: dict[str, Any],
    rejections: dict[str, Any],
    constraints: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    discards: list[dict[str, Any]],
    diversity: dict[str, Any] | None,
    warnings: list[str],
    errors: list[str],
    manifest: dict[str, Any] | None = None,
    url: str | None = None,
) -> None:
    """Store a finished run. Called once, inside one transaction."""
    with session_scope(url) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise KeyError(f"run {run_id!r} does not exist")
        run.statistics = statistics
        run.rejections = rejections
        run.constraints = constraints
        run.diversity = diversity
        run.warnings = warnings
        run.errors = errors
        run.manifest = manifest
        run.status = "succeeded"
        run.stage = "complete"
        run.progress = 1.0
        run.finished_at = _now()

        for row in candidates:
            session.add(Candidate(run_id=run_id, **row))
        for row in discards:
            session.add(Discard(run_id=run_id, **row))


def get_run(run_id: str, url: str | None = None) -> dict[str, Any] | None:
    with session_scope(url) as session:
        run = _loaded_run(session, run_id)
        return run.to_dict() if run else None


def get_run_candidate(
    run_id: str, candidate_id: str, url: str | None = None
) -> dict[str, Any] | None:
    with session_scope(url) as session:
        candidate = session.scalars(
            select(Candidate).where(
                Candidate.run_id == run_id, Candidate.candidate_id == candidate_id
            )
        ).first()
        if candidate is None:
            return None
        run = session.get(Run, run_id)
        return {
            **candidate.to_dict(),
            "run": run.summary() if run else None,
            "constraints": (run.constraints or []) if run else [],
        }


def list_runs(limit: int = 50, url: str | None = None) -> list[dict[str, Any]]:
    with session_scope(url) as session:
        runs = session.scalars(
            select(Run)
            .options(selectinload(Run.candidates), selectinload(Run.discards))
            .order_by(Run.created_at.desc())
            .limit(limit)
        ).all()
        return [r.summary() for r in runs]


def delete_run(run_id: str, url: str | None = None) -> bool:
    with session_scope(url) as session:
        run = session.get(Run, run_id)
        if run is None:
            return False
        session.delete(run)
        return True


# -- jobs ------------------------------------------------------------------


def create_job(
    *,
    kind: str,
    payload: dict[str, Any],
    run_id: str | None = None,
    candidate_id: str | None = None,
    project_id: str | None = None,
    created_by: str | None = None,
    created_by_name: str | None = None,
    evidence: str = "computed",
    url: str | None = None,
) -> str:
    job_id = new_id()
    with session_scope(url) as session:
        session.add(
            Job(
                id=job_id,
                kind=kind,
                status="queued",
                stage="queued",
                payload=payload,
                run_id=run_id,
                candidate_id=candidate_id,
                project_id=project_id,
                created_by=created_by,
                created_by_name=created_by_name,
                evidence=evidence,
            )
        )
    return job_id


def start_job(job_id: str, url: str | None = None) -> None:
    with session_scope(url) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "running"
        job.stage = "running"
        job.started_at = _now()


def set_job_progress(job_id: str, *, stage: str, progress: float, url: str | None = None) -> None:
    with session_scope(url) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.stage = stage
        job.progress = max(0.0, min(1.0, progress))


def finish_job(job_id: str, result: dict[str, Any], url: str | None = None) -> None:
    with session_scope(url) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "succeeded"
        job.stage = "complete"
        job.progress = 1.0
        job.result = result
        job.finished_at = _now()


def fail_job(
    job_id: str,
    *,
    error: str,
    error_kind: str | None = None,
    remedy: str | None = None,
    url: str | None = None,
) -> None:
    with session_scope(url) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "failed"
        job.stage = "failed"
        job.error = error
        job.error_kind = error_kind
        job.remedy = remedy
        job.finished_at = _now()


def get_job(job_id: str, url: str | None = None) -> dict[str, Any] | None:
    with session_scope(url) as session:
        job = session.get(Job, job_id)
        return job.to_dict() if job else None


def list_jobs(
    *,
    run_id: str | None = None,
    candidate_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
    url: str | None = None,
) -> list[dict[str, Any]]:
    with session_scope(url) as session:
        statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
        if run_id:
            statement = statement.where(Job.run_id == run_id)
        if candidate_id:
            statement = statement.where(Job.candidate_id == candidate_id)
        if project_id:
            statement = statement.where(Job.project_id == project_id)
        return [j.to_dict() for j in session.scalars(statement).all()]


def abandon_orphaned_work(url: str | None = None) -> int:
    """Mark work that the previous process was running as abandoned.

    The queue lives in this process, so anything still marked ``running`` at
    startup belongs to a process that is gone. Leaving those rows as "running"
    would show a user a spinner that can never finish, which is a worse lie
    than saying the work was lost.
    """
    touched = 0
    with session_scope(url) as session:
        for job in session.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all():
            job.status = "failed"
            job.stage = "abandoned"
            job.error = "the server restarted while this job was in flight"
            job.error_kind = "Abandoned"
            job.remedy = "submit it again"
            job.finished_at = _now()
            touched += 1
        for run in session.scalars(select(Run).where(Run.status.in_(("queued", "running")))).all():
            run.status = "failed"
            run.stage = "abandoned"
            run.error = "the server restarted while this run was in flight"
            run.finished_at = _now()
            touched += 1
    return touched


__all__ = [
    "TERMINAL",
    "abandon_orphaned_work",
    "create_job",
    "create_run",
    "delete_run",
    "fail_job",
    "fail_run",
    "finish_job",
    "get_job",
    "get_run",
    "get_run_candidate",
    "list_jobs",
    "list_runs",
    "new_id",
    "save_run_results",
    "set_job_progress",
    "set_run_progress",
    "start_job",
]

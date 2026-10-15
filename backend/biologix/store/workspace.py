"""Programs, triage decisions and the audit trail.

Same discipline as :mod:`biologix.store.repository`: each function takes its
own session and returns plain dictionaries, so nothing here hands a caller a
detached ORM instance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from biologix.store.database import session_scope
from biologix.store.models import Activity, Candidate, Decision, Project, Run

VERDICTS = ("shortlisted", "rejected")


def _now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


# -- programs --------------------------------------------------------------


def create_project(
    *,
    owner_id: str,
    name: str,
    target_key: str,
    target_name: str | None,
    description: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    project_id = new_id()
    with session_scope(url) as session:
        project = Project(
            id=project_id,
            owner_id=owner_id,
            name=name.strip(),
            description=(description or "").strip() or None,
            target_key=target_key,
            target_name=target_name,
        )
        session.add(project)
        session.flush()
        return project.to_dict()


def get_project(project_id: str, url: str | None = None) -> dict[str, Any] | None:
    with session_scope(url) as session:
        project = session.get(Project, project_id)
        return project.to_dict() if project else None


def list_projects(
    *, owner_id: str | None = None, include_archived: bool = False, url: str | None = None
) -> list[dict[str, Any]]:
    """Programs with the counts a card needs, computed in one pass each.

    The counts are what make a program card worth looking at — a program with
    no campaigns and a program with a 12-candidate shortlist should not look
    alike — so they are part of the listing rather than a second request.
    """
    with session_scope(url) as session:
        statement = select(Project).order_by(Project.created_at.desc())
        if owner_id:
            statement = statement.where(Project.owner_id == owner_id)
        if not include_archived:
            statement = statement.where(Project.archived.is_(False))
        projects = session.scalars(statement).all()

        out: list[dict[str, Any]] = []
        for project in projects:
            runs = session.scalars(select(Run).where(Run.project_id == project.id)).all()
            shortlisted = session.scalars(
                select(Decision.candidate_id).where(
                    Decision.project_id == project.id, Decision.verdict == "shortlisted"
                )
            ).all()
            out.append(
                {
                    **project.to_dict(),
                    "n_campaigns": len(runs),
                    "n_running": sum(1 for r in runs if r.status in ("queued", "running")),
                    "n_candidates": sum(len(r.candidates) for r in runs),
                    "n_passed": sum(1 for r in runs for c in r.candidates if c.passed),
                    "n_shortlisted": len(set(shortlisted)),
                    "last_activity": max(
                        (r.created_at for r in runs if r.created_at),
                        default=project.created_at,
                    ).isoformat()
                    if (runs or project.created_at)
                    else None,
                }
            )
        return out


def archive_project(project_id: str, *, archived: bool = True, url: str | None = None) -> bool:
    with session_scope(url) as session:
        project = session.get(Project, project_id)
        if project is None:
            return False
        project.archived = archived
        return True


def list_project_runs(project_id: str, url: str | None = None) -> list[dict[str, Any]]:
    with session_scope(url) as session:
        runs = session.scalars(
            select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc())
        ).all()
        return [r.summary() for r in runs]


# -- decisions -------------------------------------------------------------


def record_decision(
    *,
    project_id: str,
    run_id: str,
    candidate_id: str,
    user_id: str,
    verdict: str,
    rationale: str,
    weights: dict[str, Any] | None = None,
    order_score: float | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Shortlist or reject one candidate, with a reason.

    A second decision by the same person on the same candidate replaces the
    first rather than stacking, so a shortlist cannot contain two
    contradictory verdicts from one decider.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}; use {' or '.join(VERDICTS)}")
    reason = rationale.strip()
    if not reason:
        raise ValueError(
            "a decision needs a reason: it is the only part of a triage call that "
            "is not already recoverable from the numbers"
        )

    with session_scope(url) as session:
        existing = session.scalars(
            select(Decision).where(
                Decision.run_id == run_id,
                Decision.candidate_id == candidate_id,
                Decision.user_id == user_id,
            )
        ).first()
        if existing is not None:
            existing.verdict = verdict
            existing.rationale = reason
            existing.weights_at_decision = weights
            existing.order_score_at_decision = order_score
            existing.updated_at = _now()
            session.flush()
            return existing.to_dict()

        decision = Decision(
            project_id=project_id,
            run_id=run_id,
            candidate_id=candidate_id,
            user_id=user_id,
            verdict=verdict,
            rationale=reason,
            weights_at_decision=weights,
            order_score_at_decision=order_score,
        )
        session.add(decision)
        session.flush()
        return decision.to_dict()


def clear_decision(*, run_id: str, candidate_id: str, user_id: str, url: str | None = None) -> bool:
    with session_scope(url) as session:
        decision = session.scalars(
            select(Decision).where(
                Decision.run_id == run_id,
                Decision.candidate_id == candidate_id,
                Decision.user_id == user_id,
            )
        ).first()
        if decision is None:
            return False
        session.delete(decision)
        return True


def list_decisions(
    *,
    project_id: str | None = None,
    run_id: str | None = None,
    url: str | None = None,
) -> list[dict[str, Any]]:
    with session_scope(url) as session:
        statement = select(Decision).order_by(Decision.created_at.desc())
        if project_id:
            statement = statement.where(Decision.project_id == project_id)
        if run_id:
            statement = statement.where(Decision.run_id == run_id)
        return [d.to_dict() for d in session.scalars(statement).all()]


def _candidate_payload(session: Session, run_id: str, candidate_id: str) -> dict[str, Any] | None:
    candidate = session.scalars(
        select(Candidate).where(Candidate.run_id == run_id, Candidate.candidate_id == candidate_id)
    ).first()
    return candidate.to_dict() if candidate else None


def project_shortlist(project_id: str, url: str | None = None) -> list[dict[str, Any]]:
    """The shortlist, each entry carrying the candidate it refers to.

    A shortlist of ids would need the reader to go and look up every row; the
    point of a shortlist is to be read in one pass.
    """
    with session_scope(url) as session:
        decisions = session.scalars(
            select(Decision)
            .where(Decision.project_id == project_id, Decision.verdict == "shortlisted")
            .order_by(Decision.created_at.desc())
        ).all()
        out: list[dict[str, Any]] = []
        for decision in decisions:
            candidate = _candidate_payload(session, decision.run_id, decision.candidate_id)
            if candidate is None:
                # The run was deleted; the decision is history, not a link.
                continue
            out.append({"decision": decision.to_dict(), "candidate": candidate})
        return out


# -- audit trail -----------------------------------------------------------


def log(
    *,
    kind: str,
    summary: str,
    project_id: str | None = None,
    user_id: str | None = None,
    actor_name: str | None = None,
    run_id: str | None = None,
    candidate_id: str | None = None,
    job_id: str | None = None,
    detail: dict[str, Any] | None = None,
    url: str | None = None,
) -> None:
    """Append one line to the audit trail. Never raises into a caller.

    An action that succeeded must not be reported as failed because its
    audit write failed, and an audit failure must not be silent either — so
    it is swallowed here and surfaced by the row simply being absent, which
    the activity feed shows as a gap rather than as a lie.
    """
    try:
        with session_scope(url) as session:
            session.add(
                Activity(
                    kind=kind,
                    summary=summary,
                    project_id=project_id,
                    user_id=user_id,
                    actor_name=actor_name,
                    run_id=run_id,
                    candidate_id=candidate_id,
                    job_id=job_id,
                    detail=detail or {},
                )
            )
    except Exception:  # noqa: BLE001 - logging must not break the action it records
        pass


def list_activity(
    *,
    project_id: str | None = None,
    user_id: str | None = None,
    limit: int = 60,
    url: str | None = None,
) -> list[dict[str, Any]]:
    with session_scope(url) as session:
        statement = select(Activity).order_by(Activity.created_at.desc(), Activity.id.desc())
        if project_id:
            statement = statement.where(Activity.project_id == project_id)
        if user_id:
            statement = statement.where(Activity.user_id == user_id)
        return [a.to_dict() for a in session.scalars(statement.limit(limit)).all()]


__all__ = [
    "VERDICTS",
    "archive_project",
    "clear_decision",
    "create_project",
    "get_project",
    "list_activity",
    "list_decisions",
    "list_project_runs",
    "list_projects",
    "log",
    "project_shortlist",
    "record_decision",
]

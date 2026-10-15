"""Database tables.

A run is the unit a user comes back to. What is stored is everything needed to
*re-read* a run honestly — the inputs, the surviving candidates, the ones that
did not survive and why, and the manifest — rather than only the pleasant part.
Three choices are deliberate:

* **The rejections are stored, not just the survivors.** A screening run whose
  discards are thrown away cannot be audited, and the discard reasons are the
  most informative thing a user sees.
* **Descriptors are stored as computed, with their oligomer.** A descriptor
  without the capped oligomer it was computed from is not reproducible, so the
  two travel together.
* **Nothing stores a derived ordering.** Weights belong to the user and change
  per view, so ranking is computed on read. A stored rank would go stale the
  moment someone moved a slider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Make a stored timestamp safe to compare against :func:`_now`.

    ``DateTime(timezone=True)`` is honoured by Postgres, which gives back an
    aware datetime, and ignored by SQLite, which gives back a naive one. Every
    timestamp in this schema is written as UTC, so a naive value is UTC that
    merely lost its label. Comparing the two kinds raises TypeError, which is
    the sort of bug that passes every test on SQLite and fails in production
    on the first expiry check.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One generate-and-screen pass over a target."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # queued | running | succeeded | failed
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(48))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)

    label: Mapped[str | None] = mapped_column(String(200))
    # Nullable: a run started from the MCP server or a script has no project
    # and no browser session behind it, and must not be invented one.
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by_name: Mapped[str | None] = mapped_column(String(120))
    target_key: Mapped[str] = mapped_column(String(64), index=True)
    target_name: Mapped[str | None] = mapped_column(String(200))
    generator: Mapped[str] = mapped_column(String(32))
    n_requested: Mapped[int] = mapped_column(Integer)
    constraint_set: Mapped[str] = mapped_column(String(64))
    seed: Mapped[int | None] = mapped_column(Integer)

    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rejections: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diversity: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    constraints: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)

    candidates: Mapped[list[Candidate]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Candidate.id"
    )
    discards: Mapped[list[Discard]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Discard.id"
    )

    def summary(self) -> dict[str, Any]:
        """The shape a run list shows, without loading every candidate."""
        passed = sum(1 for c in self.candidates if c.passed)
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "error": self.error,
            "label": self.label,
            "project_id": self.project_id,
            "created_by": self.created_by,
            "created_by_name": self.created_by_name,
            "target_key": self.target_key,
            "target_name": self.target_name,
            "generator": self.generator,
            "n_requested": self.n_requested,
            "constraint_set": self.constraint_set,
            "seed": self.seed,
            "n_candidates": len(self.candidates),
            "n_passed": passed,
            "n_discarded": len(self.discards),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "params": self.params or {},
            "statistics": self.statistics or {},
            "rejections": self.rejections or {},
            "diversity": self.diversity,
            "constraints": self.constraints or [],
            "warnings": self.warnings or [],
            "errors": self.errors or [],
            "manifest": self.manifest,
            "candidates": [c.to_dict() for c in self.candidates],
            "discards": [d.to_dict() for d in self.discards],
        }


class Candidate(Base):
    """A candidate that became a real repeat unit and was screened."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)

    display_name: Mapped[str] = mapped_column(String(300))
    repeat_units: Mapped[list[str]] = mapped_column(JSON, default=list)
    degree_of_polymerisation: Mapped[int] = mapped_column(Integer)
    end_group: Mapped[str] = mapped_column(String(32))
    architecture: Mapped[str] = mapped_column(String(32))
    families: Mapped[list[str]] = mapped_column(JSON, default=list)
    generator: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)

    # Descriptors and the exact oligomer they describe, kept together.
    descriptors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    descriptor_error: Mapped[str | None] = mapped_column(Text)
    oligomer_smiles: Mapped[str | None] = mapped_column(Text)
    screening_dp: Mapped[int | None] = mapped_column(Integer)
    construction_rules: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    #: How this candidate would be made: mechanism, monomer, precedent, SA
    #: score and the objections. A candidate nobody can synthesise is not a
    #: candidate, so the route is stored with it rather than recomputed.
    synthesis: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    passed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failures: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    monomer_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    regulatory_context: Mapped[Any | None] = mapped_column(JSON)
    cohort_distance: Mapped[float | None] = mapped_column(Float)

    run: Mapped[Run] = relationship(back_populates="candidates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "display_name": self.display_name,
            "repeat_units": self.repeat_units or [],
            "degree_of_polymerisation": self.degree_of_polymerisation,
            "end_group": self.end_group,
            "architecture": self.architecture,
            "families": self.families or [],
            "generator": self.generator,
            "notes": self.notes,
            "descriptors": self.descriptors or {},
            "descriptor_error": self.descriptor_error,
            "oligomer_smiles": self.oligomer_smiles,
            "screening_dp": self.screening_dp,
            "construction_rules": self.construction_rules or {},
            "synthesis": self.synthesis,
            "passed": self.passed,
            "failures": self.failures or [],
            "warnings": self.warnings or [],
            "monomer_flags": self.monomer_flags or [],
            "regulatory_context": self.regulatory_context,
            "cohort_distance": self.cohort_distance,
        }


class Discard(Base):
    """A proposal that never became a candidate, and the reason.

    Stored because the reasons are the most useful output of a generation run:
    they say what the generator is doing wrong, which is how a user decides
    whether to change generator or change constraints.
    """

    __tablename__ = "discards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    proposed_smiles: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(120))
    stage: Mapped[str] = mapped_column(String(48))
    codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    detail: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="discards")

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_smiles": self.proposed_smiles,
            "source": self.source,
            "stage": self.stage,
            "codes": self.codes or [],
            "detail": self.detail,
            "duplicate_of": self.duplicate_of,
        }


class Job(Base):
    """A unit of slow work: docking now, simulation later.

    Kept in the database rather than only in memory so a user who reloads the
    page still sees what is running, and so a job that died with the process is
    visibly stale rather than silently gone.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(120))
    progress: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by_name: Mapped[str | None] = mapped_column(String(120))
    # The evidence tier this job's result carries, fixed when it is enqueued:
    # a docking pose is computed, never measured.
    evidence: Mapped[str] = mapped_column(String(16), default="computed")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String(64))
    remedy: Mapped[str | None] = mapped_column(Text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "project_id": self.project_id,
            "created_by": self.created_by,
            "created_by_name": self.created_by_name,
            "evidence": self.evidence,
            "payload": self.payload or {},
            "result": self.result,
            "error": self.error,
            "error_kind": self.error_kind,
            "remedy": self.remedy,
        }


class User(Base):
    """Someone who uses the platform.

    Roles are not a permission system — this is a research prototype with one
    organisation — they are a statement of what a person does, which is what
    the audit trail needs in order to be readable: "a reviewer shortlisted
    this" means something different from "the scientist who generated it did".
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    # scientist | computational | reviewer
    role: Mapped[str] = mapped_column(String(24), default="scientist")
    # Null for an account that has only ever signed in through an identity
    # provider. Such a user has no password to verify, and password sign-in
    # must refuse them rather than compare against an empty string.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict[str, Any]:
        """Never includes the password hash. There is no flag to turn that on."""
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }


class Session(Base):
    """A signed-in browser.

    Only the *hash* of the token is stored. A stolen database therefore cannot
    be used to impersonate anyone, which is the whole reason not to store the
    token itself.
    """

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class Identity(Base):
    """An external account that may sign in as a :class:`User`.

    Separate from ``users`` rather than a pair of columns on it, because one
    person legitimately arrives by more than one route — a password today, a
    Google account tomorrow — and because the provider's own identifier is the
    thing that must be unique, not the email.

    Matching on ``subject`` and not on email is the point. Google's ``sub`` is
    stable and never reused; an email address can be reassigned inside a
    workspace, and treating it as the identity would hand a new employee the
    previous holder's audit history.
    """

    __tablename__ = "identities"

    #: provider + the provider's immutable id for this person.
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: What the provider last told us, kept for display and for audit only.
    email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthFlow(Base):
    """One in-flight authorisation, remembered server-side.

    The ``state`` and PKCE verifier cannot live in a cookie here: the callback
    arrives from Google through the Next.js proxy, and a flow that depends on
    cookie round-tripping across that hop is a flow that breaks quietly. They
    are rows instead, which also makes single use enforceable — a replayed
    callback finds ``consumed_at`` already set and is refused.
    """

    __tablename__ = "oauth_flows"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    #: PKCE: proves the code is being redeemed by whoever requested it.
    code_verifier: Mapped[str] = mapped_column(String(128))
    #: Replay protection for the ID token itself.
    nonce: Mapped[str] = mapped_column(String(64))
    #: Where to send the browser afterwards, validated as a relative path.
    next_path: Mapped[str] = mapped_column(String(300), default="/")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Base):
    """A formulation program: one biologic, many campaigns against it.

    The target is fixed at creation. A program whose target changed halfway
    through would make its own history unreadable — the candidates before and
    after would have been screened for different proteins.
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    target_key: Mapped[str] = mapped_column(String(64), index=True)
    target_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "name": self.name,
            "description": self.description,
            "target_key": self.target_key,
            "target_name": self.target_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "archived": self.archived,
        }


class Decision(Base):
    """A person's judgement on one candidate, with the reason they gave.

    The rationale is not optional. A shortlist without reasons is a list of
    names nobody can defend a month later, and the reason is the only part of
    a triage decision that is not already recoverable from the numbers.

    One decision per person per candidate: a later verdict replaces the
    earlier one in place, so the shortlist cannot hold two contradictory
    answers from the same person at the same time.
    """

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # shortlisted | rejected
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    rationale: Mapped[str] = mapped_column(Text)
    # The ordering the decider was looking at, so a decision can be read back
    # in the context that produced it rather than in today's ordering.
    weights_at_decision: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    order_score_at_decision: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "user_id": self.user_id,
            "verdict": self.verdict,
            "rationale": self.rationale,
            "weights_at_decision": self.weights_at_decision,
            "order_score_at_decision": self.order_score_at_decision,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Activity(Base):
    """The audit trail: what happened, who did it, and when.

    Written for every action that changes what another person would see. It is
    append-only by convention — nothing in the codebase updates or deletes a
    row — because an audit trail that can be edited is not one.
    """

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_name: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    summary: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "actor_name": self.actor_name,
            "kind": self.kind,
            "summary": self.summary,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "job_id": self.job_id,
            "detail": self.detail or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TargetRecord(Base):
    """A biologic target a user added, as opposed to the built-in demonstration set.

    Stored rather than held in the process registry because a target one person
    adds has to survive a restart and be visible to everyone else. The chains
    are kept as written — separate, never concatenated — and the provenance of
    the sequence is kept with them so the target can be re-derived rather than
    trusted.
    """

    __tablename__ = "targets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    biologic_class: Mapped[str] = mapped_column(String(24))
    chains: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    modifications: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    pdb_id: Mapped[str | None] = mapped_column(String(8))
    structure_source: Mapped[str | None] = mapped_column(Text)
    uniprot_id: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)

    # pasted | pdb | uniprot — and the detail needed to repeat the import.
    source: Mapped[str] = mapped_column(String(16))
    source_detail: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by_name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "biologic_class": self.biologic_class,
            "chains": self.chains or [],
            "modifications": self.modifications or [],
            "pdb_id": self.pdb_id,
            "structure_source": self.structure_source,
            "uniprot_id": self.uniprot_id,
            "notes": self.notes,
            "source": self.source,
            "source_detail": self.source_detail,
            "created_by": self.created_by,
            "created_by_name": self.created_by_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


__all__ = [
    "Activity",
    "Base",
    "Candidate",
    "Decision",
    "Discard",
    "Job",
    "Project",
    "Run",
    "Session",
    "TargetRecord",
    "User",
]

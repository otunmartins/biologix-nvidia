"""The HTTP API.

Three surfaces, in the order a user meets them:

* **Accounts** (``/auth/*``) — register, sign in, sign out. Session auth in an
  HttpOnly cookie; the frontend never holds a credential.
* **Workspace** (``/projects``, decisions, ``/activity``) — programs, triage
  decisions with the reason attached, and an append-only audit trail.
* **Campaigns** (``/runs``, ``/jobs``) — generate-and-screen passes, ranking
  computed from the reader's own weights, and per-candidate DiffDock and
  Boltz-2 jobs.

Plus the status surface this file started as: which NVIDIA components can run
from this machine, and separately, which have actually answered a real call.

Rules the whole API shares with the rest of the project:

* **No credential is ever served.** Configuration goes out through
  :meth:`Settings.redacted`, which replaces each secret with ``<set>`` or
  ``<unset>``. Nothing here reads ``nvidia_api_key`` directly.
* **An unavailable component reports why.** ``/availability`` is
  :func:`biologix.nvidia.availability_report` unchanged, including its
  ``unverified_adapters`` list, so the API cannot present a component as
  working when ``docs/VERIFICATION.md`` says it never has.
* **Configured is not verified.** ``/availability`` answers "is this
  credentialled and pointed somewhere"; ``/verification`` carries the stronger
  claim, and only from ``scripts/live_check.py`` having actually run.

Everything except ``/``, ``/health`` and ``/auth/*`` requires a session.
Registration is open, which is a deliberate choice for a single-lab prototype
and the wrong one for a public deployment.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from biologix.api import (
    auth_routes,
    oauth_routes,
    run_routes,
    target_routes,
    workspace_routes,
)
from biologix.api.deps import CurrentUser, OptionalUser
from biologix.jobs import JobQueue
from biologix.nvidia import availability_report
from biologix.settings import Settings, get_settings
from biologix.store import init_db
from biologix.targets.catalog import load_all

# Re-exported so existing callers and tests keep working after the split.
parse_weights = run_routes.parse_weights


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    queue = JobQueue(s)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db(s)
        # Targets users added are rows, not code: they have to be registered
        # before anything can be run against them.
        load_all(s.database_url)
        # Anything still marked running belongs to a process that is gone.
        queue.recover()
        yield
        queue.shutdown(wait=False)

    app = FastAPI(
        title="Biologix-NVIDIA",
        description=(
            "Polymer excipient discovery for biologic formulations, with NVIDIA "
            "BioNeMo components. A research prototype: every result carries an "
            "evidence tier, and nothing it returns is a validated prediction of "
            "drug stability or clinical performance."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.queue = queue
    app.state.settings = s

    # The console calls this API from its own server, so these origins exist
    # for the browser's benefit only; credentials are allowed because the
    # session travels as a cookie.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    app.include_router(auth_routes.build_router(s))
    app.include_router(oauth_routes.build_router(s))
    app.include_router(workspace_routes.build_router(s))
    app.include_router(target_routes.build_router(s))
    app.include_router(run_routes.build_router(s))

    @app.get("/", summary="What this server offers")
    def index(user: OptionalUser) -> dict[str, Any]:
        return {
            "name": "Biologix-NVIDIA",
            "signed_in_as": user["email"] if user else None,
            "routes": {
                "/health": "liveness, no work done",
                "POST /auth/register, /auth/login, /auth/logout": "accounts",
                "/auth/me": "the signed-in user",
                "/targets, POST /targets/preview, POST /targets": "biologic targets",
                "/projects": "programs, with campaign and shortlist counts",
                "POST /runs": "start a campaign",
                "/runs/{id}/ranking": "candidates ordered by your weights",
                "PUT /runs/{id}/candidates/{cid}/decision": "shortlist or reject, with a reason",
                "POST /runs/{id}/candidates/{cid}/analyses": "DiffDock or Boltz-2 on one candidate",
                "/activity": "the audit trail",
                "/availability": "every NVIDIA component's status, with reasons",
                "/verification": "what has actually been called live, and what happened",
                "/docs": "interactive API documentation",
            },
            "note": ("Everything except /, /health and /auth/* requires a session cookie."),
        }

    @app.get("/health", summary="Liveness")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/availability", summary="Which NVIDIA components can run here")
    def availability(user: CurrentUser) -> dict[str, Any]:
        # Not cached: the answer changes when a container starts or a key is
        # added, and a stale "unavailable" is exactly the wrong thing to show.
        return availability_report(s)

    @app.get("/config", summary="Configuration, credentials redacted")
    def config(user: CurrentUser) -> dict[str, Any]:
        return s.redacted()

    @app.get("/verification", summary="What has actually been called, and what happened")
    def verification(user: CurrentUser) -> dict[str, Any]:
        return verification_report(s)

    return app


def verification_report(settings: Settings | None = None) -> dict[str, Any]:
    """The last live-check result, or an honest statement that there is none.

    ``/availability`` answers "is this configured and credentialed", which is a
    much weaker claim than "this works" — a hosted endpoint can be perfectly
    configured and still 404, or crash on the provider's GPU. Conflating the
    two is how a status page comes to claim five working components when three
    work. This endpoint carries the stronger claim, and it is sourced only from
    ``scripts/live_check.py`` having actually run.
    """
    s = settings or get_settings()
    path = s.data_dir / "live-check.json"
    if not path.exists():
        return {
            "ran": False,
            "note": (
                "No live check has been run. Nothing here has been proven to work; "
                "run `python scripts/live_check.py --json var/live-check.json`."
            ),
            "results": [],
        }
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "ran": False,
            "note": f"{path} could not be read: {exc}",
            "results": [],
        }

    checked_at = report.get("checked_at")
    age_hours: float | None = None
    if isinstance(checked_at, str):
        try:
            then = datetime.fromisoformat(checked_at)
            if then.tzinfo is None:
                then = then.replace(tzinfo=UTC)
            age_hours = round((datetime.now(UTC) - then).total_seconds() / 3600, 2)
        except ValueError:
            age_hours = None

    report["ran"] = True
    report["age_hours"] = age_hours
    # A result from last week describes last week's service, not today's.
    report["stale"] = age_hours is None or age_hours > 24
    report["source"] = str(path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Biologix-NVIDIA API")
    parser.add_argument("--host", help="override BIOLOGIX_HOST")
    parser.add_argument("--port", type=int, help="override BIOLOGIX_PORT")
    parser.add_argument(
        "--reload", action="store_true", help="restart on code changes (development)"
    )
    args = parser.parse_args()

    import uvicorn

    s = get_settings()
    host = args.host or s.host
    port = args.port or s.port
    if args.reload:
        # The reloader needs an import string rather than an app instance.
        uvicorn.run("biologix.api.app:create_app", factory=True, host=host, port=port, reload=True)
    else:
        uvicorn.run(create_app(s), host=host, port=port)


if __name__ == "__main__":
    main()

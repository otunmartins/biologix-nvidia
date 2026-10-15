"""Google sign-in endpoints.

Two routes and a status. ``/auth/google/start`` sends the browser to Google,
``/auth/google/callback`` receives it back, and ``/auth/providers`` says which
of them are usable so the sign-in page can show a button only when pressing it
would work.

Both routes answer with a redirect rather than JSON, because a browser arrives
at them by navigation, not by ``fetch``. A failure therefore lands back on the
sign-in page with a readable reason in the query string instead of rendering a
JSON body at the user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete

from biologix import oauth
from biologix.accounts import AuthError, sign_in_with_identity, start_session
from biologix.api.deps import client_label, session_cookie_kwargs
from biologix.settings import Settings
from biologix.store.database import session_scope
from biologix.store.models import OAuthFlow, as_utc
from biologix.store.workspace import log

#: Where a failure sends the browser. The reason rides along as a query
#: parameter; the sign-in page shows it.
SIGN_IN_PATH = "/login"


def _front_end_origin(settings: Settings) -> str:
    """The app's own origin, derived from the redirect URI already configured.

    There is no second setting for this: the redirect URI must point at the
    frontend proxy anyway, so its origin is the app's origin by construction.
    """
    uri = settings.google_redirect_uri
    scheme, _, rest = uri.partition("://")
    host = rest.partition("/")[0]
    return f"{scheme}://{host}" if scheme and host else ""


def _bounce(settings: Settings, path: str, **params: str) -> RedirectResponse:
    origin = _front_end_origin(settings)
    query = urlencode({k: v for k, v in params.items() if v})
    target = f"{origin}{path}" + (f"?{query}" if query else "")
    # 303: the browser must GET the destination, whatever it did to get here.
    return RedirectResponse(target, status_code=303)


def _purge_expired(session: Any, now: datetime) -> None:
    """Abandoned authorisations are rows nobody will ever look at again."""
    session.execute(delete(OAuthFlow).where(OAuthFlow.expires_at < now))


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    url = settings.database_url
    cookie = session_cookie_kwargs(settings.session_cookie_secure)

    @router.get("/providers", summary="Which sign-in methods this deployment offers")
    def providers() -> dict[str, Any]:
        configured = settings.google_oauth_configured
        return {
            "password": True,
            "google": {
                "configured": configured,
                "start_path": "/api/bx/auth/google/start" if configured else None,
                "allowed_domains": settings.google_allowed_domain_list,
                "reason": None
                if configured
                else "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set",
            },
        }

    @router.get("/google/start", summary="Begin Google sign-in")
    def start(next: str = Query(default="/")) -> Response:
        try:
            flow = oauth.start_flow(settings, next_path=oauth.safe_next_path(next))
        except AuthError as exc:
            return _bounce(settings, SIGN_IN_PATH, error=str(exc))

        now = datetime.now(UTC)
        with session_scope(url) as session:
            _purge_expired(session, now)
            session.add(
                OAuthFlow(
                    state=flow.state,
                    provider=oauth.PROVIDER,
                    code_verifier=flow.code_verifier,
                    nonce=flow.nonce,
                    next_path=oauth.safe_next_path(next),
                    expires_at=flow.expires_at,
                )
            )
        return RedirectResponse(flow.authorisation_url, status_code=303)

    @router.get("/google/callback", summary="Return from Google")
    def callback(
        request: Request,
        response: Response,
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> Response:
        # The person pressed "cancel", or Google refused. Not an incident.
        if error:
            return _bounce(settings, SIGN_IN_PATH, error=f"Google sign-in was cancelled ({error})")
        if not code or not state:
            return _bounce(settings, SIGN_IN_PATH, error="Google's reply was incomplete")

        now = datetime.now(UTC)
        # Claim the flow first, and claim it exactly once. Anything that
        # arrives twice with the same state is a replay.
        with session_scope(url) as session:
            flow = session.get(OAuthFlow, state)
            if flow is None:
                return _bounce(
                    settings, SIGN_IN_PATH, error="that sign-in attempt is not recognised"
                )
            if flow.consumed_at is not None:
                return _bounce(
                    settings, SIGN_IN_PATH, error="that sign-in link has already been used"
                )
            if as_utc(flow.expires_at) < now:
                return _bounce(
                    settings, SIGN_IN_PATH, error="that sign-in attempt expired; please try again"
                )
            flow.consumed_at = now
            code_verifier = flow.code_verifier
            nonce = flow.nonce
            next_path = flow.next_path

        try:
            tokens = oauth.exchange_code(settings, code=code, code_verifier=code_verifier)
            claims = oauth.validate_id_token(
                settings,
                tokens["id_token"],
                expected_nonce=nonce,
                from_token_endpoint=True,
                now=now,
            )
            profile = oauth.profile_from_claims(settings, claims)
        except AuthError as exc:
            return _bounce(settings, SIGN_IN_PATH, error=str(exc))

        user, created = sign_in_with_identity(
            provider=oauth.PROVIDER,
            subject=profile.subject,
            email=profile.email,
            display_name=profile.display_name,
            url=url,
        )
        if created:
            log(
                kind="account.created",
                summary=f"{user['display_name']} joined with Google as {user['role']}",
                user_id=user["id"],
                actor_name=user["display_name"],
                url=url,
            )

        token, _expires = start_session(user["id"], user_agent=client_label(request), url=url)
        landing = _bounce(
            settings, oauth.safe_next_path(next_path, default=settings.oauth_success_path)
        )
        landing.set_cookie(value=token, **cookie)
        return landing

    return router

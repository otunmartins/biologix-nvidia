"""The signed-in user, and the cookie that carries them.

One rule shapes this module: the session token lives in an HttpOnly cookie, so
no JavaScript in the browser can read it, and the frontend never holds a
credential. The browser talks to the Next.js app, the Next.js server forwards
the cookie to this API, and the token is never part of a URL or a payload.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Cookie, Depends, HTTPException, Request

from biologix.accounts import SESSION_DAYS, user_for_token
from biologix.settings import get_settings

COOKIE_NAME = "biologix_session"


def session_cookie_kwargs(secure: bool) -> dict[str, Any]:
    """How the session cookie is set, in one place so it cannot drift.

    ``lax`` rather than ``strict``: the console is a single origin and lax
    keeps a normal top-level navigation signed in, while still refusing the
    cookie on cross-site subrequests. ``secure`` is off for localhost
    development and must be on wherever the app is served over HTTPS.
    """
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
        "max_age": SESSION_DAYS * 24 * 3600,
    }


def settings_for(request: Request) -> Any:
    """The settings the app was built with, not the ambient ones.

    ``create_app(settings)`` is given an explicit configuration by tests and by
    anything embedding the API, so reading :func:`get_settings` here would send
    a session lookup to a different database than the one the sign-in wrote to.
    """
    return getattr(request.app.state, "settings", None) or get_settings()


def optional_user(
    request: Request,
    biologix_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> dict[str, Any] | None:
    """The user, or None. For endpoints that are readable either way."""
    if not biologix_session:
        return None
    return user_for_token(biologix_session, url=settings_for(request).database_url)


def current_user(
    user: Annotated[dict[str, Any] | None, Depends(optional_user)],
) -> dict[str, Any]:
    """The user, or a 401. The dependency every real endpoint carries."""
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="sign in to continue",
            headers={"x-biologix-auth": "required"},
        )
    return user


def client_label(request: Request) -> str | None:
    """A short description of the client, recorded with a session."""
    return request.headers.get("user-agent")


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]
OptionalUser = Annotated[dict[str, Any] | None, Depends(optional_user)]

__all__ = [
    "COOKIE_NAME",
    "CurrentUser",
    "OptionalUser",
    "client_label",
    "current_user",
    "optional_user",
    "session_cookie_kwargs",
    "settings_for",
]

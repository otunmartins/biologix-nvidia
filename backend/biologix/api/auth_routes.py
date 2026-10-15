"""Register, sign in, sign out, and "who am I".

Registration is open. That is a deliberate choice for a single-lab prototype
and the wrong one for anything public; the README says so rather than leaving
it to be discovered.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from biologix.accounts import (
    MIN_PASSWORD,
    ROLES,
    AuthError,
    authenticate,
    end_session,
    register,
    start_session,
)
from biologix.api.deps import COOKIE_NAME, CurrentUser, client_label, session_cookie_kwargs
from biologix.settings import Settings
from biologix.store.workspace import log


class RegisterRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=MIN_PASSWORD, max_length=200)
    display_name: str = Field(max_length=120)
    role: str = "scientist"


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    url = settings.database_url
    cookie = session_cookie_kwargs(settings.session_cookie_secure)

    def _sign_in(response: Response, user: dict[str, Any], request: Request) -> dict[str, Any]:
        token, expires = start_session(user["id"], user_agent=client_label(request), url=url)
        response.set_cookie(value=token, **cookie)
        return {"user": user, "expires_at": expires.isoformat()}

    @router.post("/register", status_code=201, summary="Create an account and sign in")
    def register_account(
        body: RegisterRequest, response: Response, request: Request
    ) -> dict[str, Any]:
        try:
            user = register(
                email=body.email,
                password=body.password,
                display_name=body.display_name,
                role=body.role,
                url=url,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        log(
            kind="account.created",
            summary=f"{user['display_name']} joined as {user['role']}",
            user_id=user["id"],
            actor_name=user["display_name"],
            url=url,
        )
        return _sign_in(response, user, request)

    @router.post("/login", summary="Sign in")
    def login(body: LoginRequest, response: Response, request: Request) -> dict[str, Any]:
        try:
            user = authenticate(email=body.email, password=body.password, url=url)
        except AuthError as exc:
            # 401, and the same message whether the address or the password was
            # wrong, so this cannot be used to enumerate accounts.
            raise HTTPException(status_code=401, detail=str(exc)) from None
        return _sign_in(response, user, request)

    @router.post("/logout", summary="Sign out")
    def logout(request: Request, response: Response) -> dict[str, str]:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            end_session(token, url=url)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"status": "signed out"}

    @router.get("/me", summary="The signed-in user")
    def me(user: CurrentUser) -> dict[str, Any]:
        return {"user": user, "roles": list(ROLES)}

    return router


__all__ = ["build_router"]

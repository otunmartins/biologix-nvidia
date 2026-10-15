"""Accounts and sessions.

Deliberately small and dependency-free: PBKDF2-HMAC-SHA256 from the standard
library rather than a password-hashing dependency, because adding one to a
research prototype is a supply-chain decision that buys nothing here.

What *is* taken seriously:

* a password is never stored, and never logged;
* only the SHA-256 of a session token is stored, so a copy of the database
  cannot be used to impersonate anyone;
* comparisons that could leak timing information use ``compare_digest``;
* a wrong email and a wrong password give the identical refusal, so the API
  cannot be used to enumerate who has an account.

This is session auth for a single-tenant internal tool. It is not an identity
provider, and it does not pretend to be one: there is no password reset, no
MFA and no account lockout, and those absences are the reason it should not be
exposed to the open internet as-is.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from biologix.store.database import session_scope
from biologix.store.models import Session, User

ITERATIONS = 240_000
SESSION_DAYS = 14
ROLES = ("scientist", "computational", "reviewer")

# Not RFC 5322 — just enough to reject what is obviously not an address, since
# nothing here sends mail and the address is an identifier, not a channel.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

MIN_PASSWORD = 10


class AuthError(Exception):
    """Authentication or registration was refused, with a readable reason."""


def _now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def hash_token(token: str) -> str:
    """Session tokens are high-entropy, so a plain SHA-256 is the right tool.

    Password hashing is slow on purpose, to make guessing a low-entropy secret
    expensive. A 256-bit random token cannot be guessed, so the same cost here
    would only slow every authenticated request down.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def register(
    *,
    email: str,
    password: str,
    display_name: str,
    role: str = "scientist",
    url: str | None = None,
) -> dict[str, Any]:
    address = normalise_email(email)
    if not EMAIL_RE.match(address):
        raise AuthError("that does not look like an email address")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"a password must be at least {MIN_PASSWORD} characters")
    name = display_name.strip()
    if not name:
        raise AuthError("a display name is required: the audit trail names people, not ids")
    if role not in ROLES:
        raise AuthError(f"unknown role {role!r}; use one of {', '.join(ROLES)}")

    with session_scope(url) as session:
        if session.scalars(select(User).where(User.email == address)).first():
            raise AuthError("an account already exists for that address")
        user = User(
            id=uuid.uuid4().hex[:16],
            email=address,
            display_name=name,
            role=role,
            password_hash=hash_password(password),
        )
        session.add(user)
        session.flush()
        return user.to_dict()


def authenticate(*, email: str, password: str, url: str | None = None) -> dict[str, Any]:
    """Return the user, or raise with a message that reveals nothing."""
    address = normalise_email(email)
    with session_scope(url) as session:
        user = session.scalars(select(User).where(User.email == address)).first()
        # The same refusal either way, so this cannot be used to find out
        # which addresses have accounts.
        if user is None or not verify_password(password, user.password_hash):
            raise AuthError("those credentials do not match an account")
        user.last_seen_at = _now()
        return user.to_dict()


def start_session(
    user_id: str, *, user_agent: str | None = None, url: str | None = None
) -> tuple[str, datetime]:
    """Create a session and return the raw token, which is never stored."""
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=SESSION_DAYS)
    with session_scope(url) as session:
        session.add(
            Session(
                token_hash=hash_token(token),
                user_id=user_id,
                expires_at=expires,
                user_agent=(user_agent or "")[:300] or None,
            )
        )
    return token, expires


def user_for_token(token: str, *, url: str | None = None) -> dict[str, Any] | None:
    """The signed-in user, or None. Expired sessions are deleted on sight."""
    if not token:
        return None
    with session_scope(url) as session:
        record = session.get(Session, hash_token(token))
        if record is None:
            return None
        expires = record.expires_at
        if expires.tzinfo is None:
            # SQLite hands back naive datetimes; they were stored as UTC.
            expires = expires.replace(tzinfo=UTC)
        if expires <= _now():
            session.delete(record)
            return None
        user = session.get(User, record.user_id)
        if user is None:
            return None
        user.last_seen_at = _now()
        return user.to_dict()


def end_session(token: str, *, url: str | None = None) -> None:
    with session_scope(url) as session:
        record = session.get(Session, hash_token(token))
        if record is not None:
            session.delete(record)


def count_users(url: str | None = None) -> int:
    with session_scope(url) as session:
        return len(session.scalars(select(User.id)).all())


__all__ = [
    "MIN_PASSWORD",
    "ROLES",
    "SESSION_DAYS",
    "AuthError",
    "authenticate",
    "count_users",
    "end_session",
    "hash_password",
    "hash_token",
    "register",
    "start_session",
    "user_for_token",
    "verify_password",
]

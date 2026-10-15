"""Accounts and sessions.

What is being guarded: a password is never recoverable from storage, a session
token is never recoverable from storage, and a refusal never reveals whether
an address has an account.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from biologix.accounts import (
    MIN_PASSWORD,
    AuthError,
    authenticate,
    count_users,
    end_session,
    hash_password,
    hash_token,
    register,
    start_session,
    user_for_token,
    verify_password,
)
from biologix.store.database import reset_engine_cache, session_scope
from biologix.store.models import Session


@pytest.fixture
def db(tmp_path: Path) -> Iterator[str]:
    reset_engine_cache()
    yield f"sqlite+pysqlite:///{(tmp_path / 'accounts.db').as_posix()}"
    reset_engine_cache()


def _register(db: str, email: str = "ada@lab.test") -> dict[str, object]:
    return register(
        email=email,
        password="correct horse battery",
        display_name="Ada Lovelace",
        role="scientist",
        url=db,
    )


def test_password_round_trip() -> None:
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("Correct horse battery", stored)
    assert not verify_password("", stored)


def test_the_password_is_not_in_what_is_stored() -> None:
    stored = hash_password("correct horse battery")
    assert "correct horse battery" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_the_same_password_hashes_differently_each_time() -> None:
    """A per-user salt, so identical passwords do not share a hash."""
    assert hash_password("correct horse battery") != hash_password("correct horse battery")


def test_a_corrupt_hash_fails_closed() -> None:
    assert not verify_password("anything", "")
    assert not verify_password("anything", "md5$whatever")
    assert not verify_password("anything", "pbkdf2_sha256$broken")


def test_registration_returns_a_user_without_its_hash(db: str) -> None:
    user = _register(db)
    assert user["email"] == "ada@lab.test"
    assert user["display_name"] == "Ada Lovelace"
    assert "password_hash" not in user
    assert count_users(db) == 1


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"email": "not-an-address"}, "email address"),
        ({"password": "short"}, f"at least {MIN_PASSWORD}"),
        ({"display_name": "   "}, "display name"),
        ({"role": "overlord"}, "unknown role"),
    ],
)
def test_registration_refusals_say_what_is_wrong(
    db: str, kwargs: dict[str, str], fragment: str
) -> None:
    payload = {
        "email": "ada@lab.test",
        "password": "correct horse battery",
        "display_name": "Ada",
        "role": "scientist",
        "url": db,
    }
    payload.update(kwargs)
    with pytest.raises(AuthError, match=fragment):
        register(**payload)  # type: ignore[arg-type]


def test_an_address_cannot_be_registered_twice(db: str) -> None:
    _register(db)
    with pytest.raises(AuthError, match="already exists"):
        _register(db, email="ADA@LAB.TEST")  # case-insensitive


def test_email_is_normalised(db: str) -> None:
    _register(db, email="  Ada@Lab.Test ")
    assert authenticate(email="ada@lab.test", password="correct horse battery", url=db)


def test_a_wrong_password_and_an_unknown_address_refuse_identically(db: str) -> None:
    """Otherwise the API becomes a way to find out who has an account."""
    _register(db)
    with pytest.raises(AuthError) as wrong_password:
        authenticate(email="ada@lab.test", password="nope", url=db)
    with pytest.raises(AuthError) as no_account:
        authenticate(email="nobody@lab.test", password="nope", url=db)
    assert str(wrong_password.value) == str(no_account.value)


def test_a_session_resolves_to_its_user(db: str) -> None:
    user = _register(db)
    token, expires = start_session(str(user["id"]), user_agent="pytest", url=db)
    assert expires > datetime.now(UTC)

    resolved = user_for_token(token, url=db)
    assert resolved is not None
    assert resolved["id"] == user["id"]
    assert resolved["last_seen_at"] is not None


def test_only_the_token_hash_is_stored(db: str) -> None:
    """A copy of the database must not be usable to impersonate anyone."""
    user = _register(db)
    token, _ = start_session(str(user["id"]), url=db)
    with session_scope(db) as session:
        rows = session.query(Session).all()
        assert len(rows) == 1
        assert rows[0].token_hash != token
        assert rows[0].token_hash == hash_token(token)


def test_a_wrong_or_empty_token_is_nobody(db: str) -> None:
    _register(db)
    assert user_for_token("", url=db) is None
    assert user_for_token("not-a-real-token", url=db) is None


def test_signing_out_invalidates_the_session(db: str) -> None:
    user = _register(db)
    token, _ = start_session(str(user["id"]), url=db)
    end_session(token, url=db)
    assert user_for_token(token, url=db) is None


def test_an_expired_session_is_rejected_and_removed(db: str) -> None:
    user = _register(db)
    token, _ = start_session(str(user["id"]), url=db)
    with session_scope(db) as session:
        record = session.get(Session, hash_token(token))
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert user_for_token(token, url=db) is None
    with session_scope(db) as session:
        assert session.get(Session, hash_token(token)) is None

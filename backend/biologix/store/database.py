"""Engine, sessions and the one-call schema setup.

SQLite is the right default here: a single-user research prototype should not
need a database server to start. Two SQLite-specific things are handled rather
than discovered later:

* ``check_same_thread=False``, because the job workers are threads and would
  otherwise be refused their own connections;
* WAL journalling and a busy timeout, so a worker writing a job's progress does
  not collide with the web request reading it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from biologix.settings import Settings, get_settings
from biologix.store.models import Base


def _make_engine(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _configure(dbapi_connection, _record) -> None:  # pragma: no cover - driver glue
            cursor = dbapi_connection.cursor()
            # Readers do not block the writer, and a writer waits rather than
            # failing instantly when two threads meet.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    resolved = url or settings.database_url
    if resolved.startswith("sqlite") and ":memory:" not in resolved:
        # The database lives under the data directory, which may not exist yet
        # on a first run.
        settings.ensure_dirs()
    engine = _make_engine(resolved)
    Base.metadata.create_all(engine)
    return engine


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """A transaction that commits on success and rolls back on failure."""
    factory = get_sessionmaker(url)
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Drop cached engines; used by tests that point at a fresh database."""
    get_engine.cache_clear()


def init_db(settings: Settings | None = None) -> None:
    s = settings or get_settings()
    get_engine(s.database_url)


__all__ = [
    "get_engine",
    "get_sessionmaker",
    "init_db",
    "reset_engine_cache",
    "session_scope",
]

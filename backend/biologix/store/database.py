"""Engine, sessions and the one-call schema setup.

SQLite is the right default here: a single-user research prototype should not
need a database server to start. Two SQLite-specific things are handled rather
than discovered later:

* ``check_same_thread=False``, because the job workers are threads and would
  otherwise be refused their own connections;
* WAL journalling and a busy timeout, so a worker writing a job's progress does
  not collide with the web request reading it.

Anything that is not SQLite is treated as a deployed database server:
connections are pre-pinged and recycled, which a serverless Postgres such as
Neon requires, and the schema belongs to Alembic rather than to the
application's startup. Swapping ``BIOLOGIX_DATABASE_URL`` to
``postgresql+psycopg://…`` is the whole change — no model or query in this
package is dialect-specific.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from biologix.settings import Settings, get_settings
from biologix.store.models import Base


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _make_engine(url: str) -> Engine:
    connect_args: dict[str, object] = {}
    kwargs: dict[str, object] = {}
    if _is_sqlite(url):
        connect_args["check_same_thread"] = False
    else:
        # Neon and every other serverless Postgres closes idle connections
        # without warning, so a pooled connection that has been sitting
        # unused is frequently already dead. ``pool_pre_ping`` spends one
        # round trip proving the connection works instead of failing the
        # request; ``pool_recycle`` retires connections before the far end
        # does it for us.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 300
    engine = create_engine(url, future=True, connect_args=connect_args, **kwargs)

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
    if _is_sqlite(resolved) and ":memory:" not in resolved:
        # The database lives under the data directory, which may not exist yet
        # on a first run.
        settings.ensure_dirs()
    engine = _make_engine(resolved)
    if _is_sqlite(resolved):
        # A file database is created on demand: a local run should not need a
        # migration step before it can start.
        Base.metadata.create_all(engine)
    else:
        _require_migrated_schema(engine)
    return engine


def _require_migrated_schema(engine: Engine) -> None:
    """Refuse to run against an un-migrated server database.

    ``create_all`` is the wrong tool against Postgres. It creates whatever the
    models currently say and silently does nothing to a table that already
    exists, so a model change lands as a schema that half matches the code and
    no record of how it got there. Alembic owns the schema beyond SQLite, and
    this says so plainly rather than failing later with a missing column.
    """
    expected = set(Base.metadata.tables)
    present = set(inspect(engine).get_table_names())
    missing = sorted(expected - present)
    if not missing:
        return
    raise RuntimeError(
        f"{engine.dialect.name} database at this URL is missing "
        f"{len(missing)} of {len(expected)} tables ({', '.join(missing[:4])}"
        f"{', …' if len(missing) > 4 else ''}). Run `alembic upgrade head` "
        "against it first; the schema is managed by migrations, not by the "
        "application at startup."
    )


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

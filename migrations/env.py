"""Alembic environment.

The connection string comes from :mod:`biologix.settings`, not from
``alembic.ini``, so that a migration run and the application can never
disagree about which database they mean, and so that a Neon credential stays
in the environment rather than in a committed file.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from biologix.settings import get_settings
from biologix.store.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate compares the live database against these tables.
target_metadata = Base.metadata


def _url() -> str:
    """The database to migrate.

    ``-x url=…`` wins, so a one-off migration against a staging copy does not
    require editing the environment. Otherwise it is whatever the application
    itself would use.
    """
    override = context.get_x_argument(as_dictionary=True).get("url")
    return override or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it.

    Useful where the production database is only reachable by a DBA: the
    review happens on the SQL, not on trust in this script.
    """
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catch a column whose type changed, not merely one that was
            # added or dropped.
            compare_type=True,
            compare_server_default=True,
            # SQLite cannot ALTER a column in place. Batch mode rebuilds the
            # table instead, so the same revision applies to the local file
            # database and to Postgres.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

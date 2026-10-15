"""The guards that only matter once this is deployed.

Each test here corresponds to a way a production deployment goes wrong
silently: a session cookie sent in clear text, a serverless connection reused
after the far end dropped it, or an application creating its own schema and
leaving no record of how the database came to look the way it does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from biologix.settings import Settings
from biologix.store.database import _make_engine, _require_migrated_schema

POSTGRES_URL = "postgresql+psycopg://user:pw@db.example.neon.tech/biologix"


class TestProductionSettings:
    def test_production_defaults_the_session_cookie_to_secure(self, tmp_path: Path) -> None:
        settings = Settings(
            BIOLOGIX_ENV="production",
            BIOLOGIX_DATABASE_URL=POSTGRES_URL,
            BIOLOGIX_DATA_DIR=tmp_path / "var",
            BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        )
        assert settings.is_production
        assert settings.session_cookie_secure is True

    def test_development_leaves_it_off_for_localhost(self, tmp_path: Path) -> None:
        settings = Settings(
            BIOLOGIX_DATA_DIR=tmp_path / "var",
            BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        )
        assert settings.is_production is False
        assert settings.session_cookie_secure is False

    def test_disabling_the_secure_cookie_in_production_is_refused(self, tmp_path: Path) -> None:
        """A leftover developer override must not quietly downgrade production."""
        with pytest.raises(ValueError, match="clear text"):
            Settings(
                BIOLOGIX_ENV="production",
                BIOLOGIX_SESSION_COOKIE_SECURE="false",
                BIOLOGIX_DATABASE_URL=POSTGRES_URL,
                BIOLOGIX_DATA_DIR=tmp_path / "var",
                BIOLOGIX_CACHE_DIR=tmp_path / "cache",
            )

    def test_production_refuses_a_sqlite_store(self, tmp_path: Path) -> None:
        """A forgotten BIOLOGIX_DATABASE_URL must not boot on a throwaway file.

        The default is SQLite, so without this a deployment that lost its
        database URL would run, take sign-ups and record decisions into a file
        inside the container, and lose all of it on the next restart.
        """
        with pytest.raises(ValueError, match="lost on restart"):
            Settings(
                _env_file=None,
                BIOLOGIX_ENV="production",
                BIOLOGIX_DATA_DIR=tmp_path / "var",
                BIOLOGIX_CACHE_DIR=tmp_path / "cache",
            )

    def test_production_accepts_postgres(self, tmp_path: Path) -> None:
        settings = Settings(
            BIOLOGIX_ENV="production",
            BIOLOGIX_DATABASE_URL=POSTGRES_URL,
            BIOLOGIX_DATA_DIR=tmp_path / "var",
            BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        )
        assert settings.is_production and settings.is_postgres
        assert settings.session_cookie_secure is True

    def test_development_still_defaults_to_sqlite(self, tmp_path: Path) -> None:
        """The local run must stay a one-command affair."""
        settings = Settings(
            _env_file=None,
            BIOLOGIX_DATA_DIR=tmp_path / "var",
            BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        )
        assert settings.database_url.startswith("sqlite")

    def test_an_unknown_environment_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="environment must be one of"):
            Settings(
                BIOLOGIX_ENV="staging-ish",
                BIOLOGIX_DATA_DIR=tmp_path / "var",
                BIOLOGIX_CACHE_DIR=tmp_path / "cache",
            )

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("sqlite+pysqlite:///./var/biologix.db", False),
            (POSTGRES_URL, True),
            ("postgres://user:pw@host/db", True),
        ],
    )
    def test_is_postgres_reads_the_url(self, tmp_path: Path, url: str, expected: bool) -> None:
        settings = Settings(
            BIOLOGIX_DATABASE_URL=url,
            BIOLOGIX_DATA_DIR=tmp_path / "var",
            BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        )
        assert settings.is_postgres is expected


class TestEngineForServerlessPostgres:
    """``create_engine`` is lazy, so these assert configuration, not a connection."""

    def test_postgres_connections_are_pre_pinged_and_recycled(self) -> None:
        engine = _make_engine(POSTGRES_URL)
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == 300

    def test_sqlite_is_left_alone(self, tmp_path: Path) -> None:
        """Pre-ping and recycling are meaningless against a local file."""
        engine = _make_engine(f"sqlite+pysqlite:///{tmp_path / 'x.db'}")
        assert engine.pool._pre_ping is False


class TestSchemaOwnership:
    def test_an_unmigrated_database_is_refused_with_instructions(self) -> None:
        """The application must not paper over a missing migration."""
        empty = create_engine("sqlite+pysqlite:///:memory:", future=True)
        with pytest.raises(RuntimeError, match="alembic upgrade head"):
            _require_migrated_schema(empty)

    def test_a_migrated_database_passes(self, tmp_path: Path) -> None:
        from biologix.store.models import Base

        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'full.db'}", future=True)
        Base.metadata.create_all(engine)
        _require_migrated_schema(engine)  # does not raise

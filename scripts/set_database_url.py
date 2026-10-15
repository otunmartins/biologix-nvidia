"""Put a Postgres connection string into ``.env`` without it leaking.

A Neon connection string contains a password that grants full access to the
database. This is the companion to ``scripts/set_api_key.py`` for that
credential, and it exists so the string never has to pass through a chat
window, a shell history file, a command line or a screen share.

What it does:

* the prompt is masked, and pressing Enter on an empty prompt changes nothing;
* the URL is checked before it is stored, and the two mistakes that produce a
  confusing failure much later — a ``postgresql://`` scheme SQLAlchemy cannot
  use, and a missing ``sslmode=require`` — are offered as fixes rather than
  reported as errors;
* it is written quoted, because a Neon password may contain ``#`` and an
  unquoted ``#`` starts a comment;
* only that one line of ``.env`` is rewritten, atomically, reusing the writer
  in ``set_api_key.py`` rather than a second copy of it;
* nothing prints the URL. The confirmation shows host and database with the
  password replaced, and failures are redacted the same way.

Usage:
    python scripts/set_database_url.py              # prompt, then offer to test
    python scripts/set_database_url.py --test-only  # test what is already saved
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The atomic, line-preserving .env writer. Imported rather than copied: it is
# the part that must not be subtly wrong.
from set_api_key import ENV_FILE, _line_pattern, write_keys  # noqa: E402

VARIABLE = "BIOLOGIX_DATABASE_URL"

#: What SQLAlchemy needs in front of a Postgres URL for the installed driver.
WANTED_SCHEME = "postgresql+psycopg"

#: Schemes people paste, and which are the same database by another name.
FIXABLE_SCHEMES = ("postgresql", "postgres", "postgresql+psycopg2")


def read_saved() -> str | None:
    if not ENV_FILE.exists():
        return None
    text = ENV_FILE.read_text(encoding="utf-8")
    matches = _line_pattern(VARIABLE).findall(text)
    if not matches:
        return None
    return matches[-1].split("=", 1)[1].strip().strip("\"'") or None


def redact(url: str) -> str:
    """Host and database, never the password. Safe to print or paste."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unreadable url>"
    host = parts.hostname or "?"
    user = parts.username or "?"
    database = (parts.path or "/?").lstrip("/") or "?"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{user}:***@{host}{port}/{database}"


def scrub(text: str, url: str | None) -> str:
    """Remove a password from arbitrary output before it is shown."""
    if not url:
        return text
    try:
        password = urlsplit(url).password
    except ValueError:
        password = None
    if password:
        text = text.replace(password, "***")
    return text.replace(url, redact(url))


def inspect(url: str) -> tuple[str, list[str], list[str]]:
    """Return (possibly corrected url, applied fixes, remaining warnings)."""
    fixes: list[str] = []
    warnings: list[str] = []

    url = url.strip().strip("\"'")
    # A paste from a dashboard sometimes arrives as psql 'postgres://…'.
    if url.startswith("psql "):
        url = url[5:].strip().strip("'\"")
        fixes.append("removed the leading 'psql '")

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ValueError(f"that is not a URL: {exc}") from None

    if parts.scheme in FIXABLE_SCHEMES:
        parts = parts._replace(scheme=WANTED_SCHEME)
        fixes.append(f"scheme -> {WANTED_SCHEME} (what the installed driver registers as)")
    elif parts.scheme != WANTED_SCHEME:
        raise ValueError(
            f"scheme is {parts.scheme!r}; expected {WANTED_SCHEME} or one of "
            f"{', '.join(FIXABLE_SCHEMES)}"
        )

    # A '#' in a password is read as the start of a URL fragment, which
    # truncates the host and database and produces a baffling error two checks
    # later. Name the real cause instead.
    if parts.fragment:
        raise ValueError(
            "that URL contains an unencoded '#'. If it is in the password, "
            "replace it with %23 — Neon's own strings are already encoded, so "
            "copy the string rather than retyping the password"
        )
    if not parts.hostname:
        raise ValueError("no host in that URL")
    if not parts.password:
        warnings.append("no password in the URL — is this the full connection string?")
    if not (parts.path or "").strip("/"):
        raise ValueError("no database name in that URL")

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    host = parts.hostname
    # Neon requires TLS, and psycopg's default sslmode would only *prefer* it.
    # "Prefer" means a downgrade is silent, which is not a property to carry
    # a database password over.
    if "sslmode" not in query:
        query["sslmode"] = "require"
        fixes.append("added sslmode=require")

    # channel_binding=require is Neon's own default in newer strings; harmless
    # elsewhere, so it is left exactly as pasted.
    parts = parts._replace(query="&".join(f"{k}={v}" for k, v in query.items()))

    if host.endswith(".neon.tech") and "-pooler." not in host:
        warnings.append(
            "this is Neon's direct (unpooled) host. For a web app prefer the "
            "'-pooler' host, which survives many short-lived connections."
        )
    return urlunsplit(parts), fixes, warnings


def test_connection(url: str) -> tuple[bool, str]:
    """One ``SELECT version()`` through SQLAlchemy, with the saved URL."""
    try:
        from sqlalchemy import create_engine, text
    except ImportError:  # pragma: no cover - the venv always has it
        return False, "SQLAlchemy is not installed in this environment"
    try:
        engine = create_engine(url, future=True, pool_pre_ping=True)
        with engine.connect() as connection:
            version = connection.execute(text("select version()")).scalar_one()
            database = connection.execute(text("select current_database()")).scalar_one()
        engine.dispose()
    except Exception as exc:  # noqa: BLE001 - any driver error is a failed test
        return False, scrub(f"{type(exc).__name__}: {exc}", url)
    return True, f"connected to {database}: {str(version).split(' on ')[0]}"


def run_migrations(url: str) -> tuple[bool, str]:
    """``alembic upgrade head`` against the saved URL."""
    env = {k: v for k, v in os.environ.items() if k != VARIABLE}
    env[VARIABLE] = url
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "alembic timed out after 300 s"
    output = scrub((proc.stdout + "\n" + proc.stderr).strip(), url)
    return proc.returncode == 0, output


def confirm(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--test-only", action="store_true", help="test the saved URL without changing it"
    )
    parser.add_argument(
        "--migrate", action="store_true", help="run alembic upgrade head without asking"
    )
    args = parser.parse_args()

    if os.environ.get(VARIABLE):
        print(
            f"! {VARIABLE} is set in this shell, which overrides .env when the app loads.\n"
            f"  Unset it, or this file's value will be silently ignored.\n"
        )

    saved = read_saved()
    print(f"{VARIABLE} in .env: {redact(saved) if saved else 'not set'}\n")

    url = saved
    if not args.test_only:
        print("Paste the connection string. It will not be echoed.")
        print("Press Enter on its own to keep what is already there.\n")
        entered = getpass.getpass("connection string: ").strip()
        if entered:
            try:
                url, fixes, warnings = inspect(entered)
            except ValueError as exc:
                print(f"\nx refused: {exc}")
                return 1
            for fix in fixes:
                print(f"  - {fix}")
            for warning in warnings:
                print(f"  ! {warning}")
            # Quoted, so a '#' in the password is not read as a comment.
            write_keys({VARIABLE: f'"{url}"'})
            print(f"\n+ saved to .env: {redact(url)}")
        elif not saved:
            print("Nothing entered and nothing saved; stopping.")
            return 1

    if not url:
        return 1

    print("\nTesting the connection…")
    ok, report = test_connection(url)
    print(("+ " if ok else "x ") + report)
    if not ok:
        return 1

    if args.migrate or confirm("\nRun 'alembic upgrade head' against it now?"):
        ok, report = run_migrations(url)
        print(report)
        print(("+ " if ok else "x ") + ("schema is up to date" if ok else "migration failed"))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    python scripts/set_database_url.py              # window with a paste field
    python scripts/set_database_url.py --cli        # hidden terminal prompt
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


def run_form() -> int:
    """A window with one paste field.

    Pasting into a masked terminal prompt is uncomfortable — nothing appears,
    so there is no way to tell a failed paste from a successful one. Here the
    field accepts Ctrl+V, the value can be revealed deliberately, and the
    clipboard can be cleared on save so the credential does not linger there.
    """
    import threading
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Biologix — Postgres connection string")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid()

    ttk.Label(
        frame,
        text="Neon connection string",
        font=("TkDefaultFont", 10, "bold"),
    ).grid(row=0, column=0, columnspan=4, sticky="w")
    ttk.Label(
        frame,
        text=(
            "Paste it exactly as Neon gives it to you. The scheme and sslmode are\n"
            "corrected for you, and the password is never displayed or logged."
        ),
        foreground="#555555",
        justify="left",
    ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 8))

    entry = ttk.Entry(frame, show="•", width=84)
    entry.grid(row=2, column=0, columnspan=4, sticky="we")
    entry.focus_set()

    current = tk.StringVar()
    ttk.Label(frame, textvariable=current, foreground="#555555").grid(
        row=3, column=0, columnspan=4, sticky="w", pady=(4, 10)
    )

    def refresh_current() -> None:
        saved = read_saved()
        current.set(
            f"In .env now: {redact(saved) if saved else 'not set'}"
            + ("" if saved else "  (the app falls back to local SQLite)")
        )

    refresh_current()

    reveal = tk.BooleanVar(value=False)
    clear_clipboard = tk.BooleanVar(value=True)

    ttk.Checkbutton(
        frame,
        text="Show what I pasted",
        variable=reveal,
        command=lambda: entry.configure(show="" if reveal.get() else "•"),
    ).grid(row=4, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(frame, text="Clear the clipboard after saving", variable=clear_clipboard).grid(
        row=5, column=0, columnspan=3, sticky="w"
    )

    status = tk.StringVar(
        value=f"Writes to {ENV_FILE} (git-ignored). The string is not sent anywhere but Neon."
    )
    ttk.Label(frame, textvariable=status, wraplength=620, justify="left").grid(
        row=6, column=0, columnspan=4, sticky="w", pady=(12, 12)
    )

    buttons = ttk.Frame(frame)
    buttons.grid(row=7, column=0, columnspan=4, sticky="w")
    busy = {"running": False}

    def set_busy(running: bool) -> None:
        busy["running"] = running
        state = "disabled" if running else "normal"
        for child in buttons.winfo_children():
            child.configure(state=state)

    def save_only() -> str | None:
        """Validate and store what is in the field. Returns the saved URL."""
        pasted = entry.get().strip()
        if not pasted:
            saved = read_saved()
            if saved and saved.startswith(("postgresql", "postgres://")):
                status.set("Field empty, so the saved string is kept.")
                return saved
            status.set("Nothing pasted, and nothing usable saved. Paste the string first.")
            return None
        try:
            url, fixes, warnings = inspect(pasted)
        except ValueError as exc:
            status.set(f"Refused: {exc}")
            return None
        try:
            write_keys({VARIABLE: f'"{url}"'})
        except OSError as exc:
            status.set(f"Could not write .env: {exc}")
            return None

        entry.delete(0, tk.END)
        if clear_clipboard.get():
            root.clipboard_clear()
        refresh_current()
        lines = [f"Saved: {redact(url)}"]
        lines += [f"  fixed: {f}" for f in fixes]
        lines += [f"  note: {w}" for w in warnings]
        if os.environ.get(VARIABLE):
            lines.append(
                f"  warning: {VARIABLE} is also set in your shell, and that copy "
                "wins over .env until you unset it."
            )
        status.set("\n".join(lines))
        return url

    def in_background(work: object, url: str) -> None:
        """Run a slow step off the Tk thread, reporting back through after()."""

        def runner() -> None:
            ok, report = work(url)  # type: ignore[operator]
            root.after(0, lambda: finish(ok, report))

        def finish(ok: bool, report: str) -> None:
            status.set(("OK — " if ok else "Failed — ") + report)
            set_busy(False)

        set_busy(True)
        threading.Thread(target=runner, daemon=True).start()

    def on_save() -> None:
        save_only()

    def on_test() -> None:
        url = save_only() or read_saved()
        if not url:
            return
        status.set("Connecting to Postgres…")
        in_background(test_connection, url)

    def on_migrate() -> None:
        url = save_only() or read_saved()
        if not url:
            return

        def work(target: str) -> tuple[bool, str]:
            ok, report = test_connection(target)
            if not ok:
                return False, report
            ok, migration = run_migrations(target)
            tail = "\n".join(migration.splitlines()[-6:])
            return ok, ("schema is up to date\n" + tail if ok else migration)

        status.set("Connecting, then running alembic upgrade head…")
        in_background(work, url)

    ttk.Button(buttons, text="Save", command=on_save).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="Save & test", command=on_test).grid(row=0, column=1, padx=(0, 8))
    ttk.Button(buttons, text="Save, test & migrate", command=on_migrate).grid(
        row=0, column=2, padx=(0, 8)
    )
    ttk.Button(buttons, text="Close", command=root.destroy).grid(row=0, column=3)

    # Enter saves and tests, which is what someone who just pasted expects.
    root.bind("<Return>", lambda _e: None if busy["running"] else on_test())
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--test-only", action="store_true", help="test the saved URL without changing it"
    )
    parser.add_argument(
        "--migrate", action="store_true", help="run alembic upgrade head without asking"
    )
    parser.add_argument(
        "--cli", action="store_true", help="hidden terminal prompt instead of the window"
    )
    args = parser.parse_args()

    if not args.cli and not args.test_only:
        try:
            return run_form()
        except Exception as exc:  # noqa: BLE001 - headless or no tkinter
            print(f"(falling back to the terminal prompt: {exc})\n")

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

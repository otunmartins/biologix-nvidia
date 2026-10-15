"""Local form for putting NVIDIA credentials into ``.env``.

Purpose: get ``NVIDIA_API_KEY`` (and optionally ``NGC_API_KEY``) into the
git-ignored ``.env`` without the key ever passing through a chat window, a
shell history file, a command line or a web page.

What it does and does not do:

* the fields are masked, and a field left empty leaves that key unchanged;
* only the named lines of ``.env`` are rewritten, everything else is kept
  byte for byte, and the write is atomic (temp file, then rename);
* ``.env`` is created from ``.env.example`` if it does not exist yet;
* nothing here prints, logs or transmits a key. The only network traffic is
  the optional test, which runs ``scripts/live_check.py --only diffdock`` (one
  minimal DiffDock call to NVIDIA, using the key that was saved).

Usage:
    python scripts/set_api_key.py          # window with masked fields
    python scripts/set_api_key.py --cli    # hidden terminal prompts instead
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

FIELDS: tuple[tuple[str, str], ...] = (
    ("NVIDIA_API_KEY", "NVIDIA API key  (build.nvidia.com, used for hosted NIM calls)"),
    ("NGC_API_KEY", "NGC API key  (optional, only to pull NIM containers from nvcr.io)"),
)

# The adapter the "test" action calls. DiffDock, because its hosted endpoint
# answers on the configured path, so a failure there points at the key.
TEST_ADAPTER = "diffdock"

# Characters that would change how the .env line is parsed.
_UNSAFE = re.compile(r"[\s\"'#]")


def _line_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"^[ \t]*(?:export[ \t]+)?{re.escape(name)}[ \t]*=[^\r\n]*", re.MULTILINE)


def read_current() -> dict[str, str]:
    """Values currently in ``.env`` for the fields this form manages."""
    if not ENV_FILE.exists():
        return {}
    text = ENV_FILE.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for name, _ in FIELDS:
        matches = _line_pattern(name).findall(text)
        if matches:
            # The last assignment wins, as it does when the file is loaded.
            found[name] = matches[-1].split("=", 1)[1].strip().strip("\"'")
    return found


def describe(value: str | None) -> str:
    """A description of a stored key that is safe to show on screen."""
    if not value:
        return "not set"
    return f"set (ends in ...{value[-4:]})" if len(value) >= 12 else "set"


def validate(name: str, value: str) -> str | None:
    """Return why ``value`` cannot be stored, or None if it can."""
    if _UNSAFE.search(value):
        return f"{name} contains a space, quote or '#'. Paste the key on its own."
    if len(value) < 20:
        return f"{name} is only {len(value)} characters; that is too short to be a real key."
    return None


def write_keys(updates: dict[str, str]) -> None:
    """Set ``updates`` in ``.env``, leaving every other line untouched."""
    if ENV_FILE.exists():
        source = ENV_FILE
    elif ENV_EXAMPLE.exists():
        source = ENV_EXAMPLE
    else:
        source = None
    # newline="" keeps the file's own line endings.
    text = source.open(encoding="utf-8", newline="").read() if source else ""
    eol = "\r\n" if "\r\n" in text else "\n"

    for name, value in updates.items():
        line = f"{name}={value}"
        pattern = _line_pattern(name)
        if pattern.search(text):
            # A callable replacement, so the key is never read as a regex template.
            text = pattern.sub(lambda _m, line=line: line, text)
        else:
            if text and not text.endswith(("\n", "\r")):
                text += eol
            text += line + eol

    # Same directory, so the rename is atomic; the name matches the .env.*
    # ignore rule, so a leftover could not be committed either.
    fd, tmp_name = tempfile.mkstemp(dir=ROOT, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_name, ENV_FILE)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def shell_overrides() -> list[str]:
    """Managed variables set in this process's environment.

    An environment variable takes precedence over ``.env`` when the settings
    are loaded, so a stale one would silently shadow whatever is saved here.
    """
    return [name for name, _ in FIELDS if os.environ.get(name)]


def run_live_check() -> tuple[bool, str]:
    """One minimal hosted NIM call with the saved key. Returns (ok, short report)."""
    # Drop any shell-level copies so this exercises what is in .env.
    env = {k: v for k, v in os.environ.items() if k not in {name for name, _ in FIELDS}}
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "live_check.py"), "--only", TEST_ADAPTER],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"Timed out after 180 s waiting for the {TEST_ADAPTER} endpoint."
    output = (proc.stdout + "\n" + proc.stderr).strip()
    # live_check does not print the key; this is a second guard, not the first.
    for value in read_current().values():
        if value:
            output = output.replace(value, "<redacted>")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    # live_check ends with the full JSON report; the summary above it is enough here.
    if "{" in lines:
        lines = lines[: lines.index("{")]
    return proc.returncode == 0, "\n".join(lines[-6:])


def run_form() -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Biologix-NVIDIA credentials")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid()

    entries: dict[str, ttk.Entry] = {}
    current_labels: dict[str, tk.StringVar] = {}
    row = 0
    for name, label in FIELDS:
        ttk.Label(frame, text=label).grid(row=row, column=0, columnspan=3, sticky="w")
        entry = ttk.Entry(frame, show="*", width=72)
        entry.grid(row=row + 1, column=0, columnspan=3, sticky="we", pady=(2, 0))
        current = tk.StringVar()
        ttk.Label(frame, textvariable=current, foreground="#555555").grid(
            row=row + 2, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        entries[name] = entry
        current_labels[name] = current
        row += 3

    def refresh_current() -> None:
        stored = read_current()
        for name, var in current_labels.items():
            var.set(f"In .env now: {describe(stored.get(name))}. Leave empty to keep it.")

    reveal = tk.BooleanVar(value=False)
    clear_clipboard = tk.BooleanVar(value=True)

    def toggle_reveal() -> None:
        for entry in entries.values():
            entry.configure(show="" if reveal.get() else "*")

    ttk.Checkbutton(frame, text="Show what I typed", variable=reveal, command=toggle_reveal).grid(
        row=row, column=0, columnspan=3, sticky="w"
    )
    ttk.Checkbutton(frame, text="Clear the clipboard after saving", variable=clear_clipboard).grid(
        row=row + 1, column=0, columnspan=3, sticky="w"
    )
    row += 2

    status = tk.StringVar(value=f"Writes to {ENV_FILE} (git-ignored). Nothing is sent anywhere.")
    ttk.Label(frame, textvariable=status, wraplength=520, justify="left").grid(
        row=row, column=0, columnspan=3, sticky="w", pady=(12, 12)
    )
    row += 1

    def save() -> None:
        updates = {name: e.get().strip() for name, e in entries.items() if e.get().strip()}
        if not updates:
            status.set("Nothing entered, so .env was left unchanged.")
            return
        for name, value in updates.items():
            problem = validate(name, value)
            if problem:
                status.set(problem)
                return
        nvidia = updates.get("NVIDIA_API_KEY")
        if nvidia and not nvidia.startswith("nvapi-"):
            if not messagebox.askyesno(
                "Unusual key format",
                "NVIDIA API keys normally start with 'nvapi-'. Save this one anyway?",
            ):
                return
        try:
            write_keys(updates)
        except OSError as exc:
            status.set(f"Could not write .env: {exc}")
            return
        for entry in entries.values():
            entry.delete(0, tk.END)
        if clear_clipboard.get():
            root.clipboard_clear()
        refresh_current()
        message = f"Saved {', '.join(updates)} to .env."
        shadowed = [name for name in shell_overrides() if name in updates]
        if shadowed:
            message += (
                f" Note: {', '.join(shadowed)} is also set as an environment variable in"
                " this shell, and that copy takes precedence over .env until you unset it."
            )
        status.set(message)

    def test() -> None:
        if not read_current().get("NVIDIA_API_KEY"):
            status.set("There is no NVIDIA_API_KEY in .env to test yet.")
            return
        for button in buttons:
            button.configure(state="disabled")
        status.set(f"Testing: one minimal {TEST_ADAPTER} call to NVIDIA with the saved key ...")
        outcome: list[tuple[bool, str]] = []
        threading.Thread(target=lambda: outcome.append(run_live_check()), daemon=True).start()

        def poll() -> None:
            if not outcome:
                root.after(200, poll)
                return
            ok, report = outcome[0]
            status.set(("Key works.\n" if ok else "Test did not pass.\n") + report)
            for button in buttons:
                button.configure(state="normal")

        root.after(200, poll)

    buttons = [
        ttk.Button(frame, text="Save to .env", command=save),
        ttk.Button(frame, text="Test saved key", command=test),
        ttk.Button(frame, text="Close", command=root.destroy),
    ]
    for column, button in enumerate(buttons):
        button.grid(row=row, column=column, sticky="we", padx=(0 if column == 0 else 8, 0))

    refresh_current()
    entries["NVIDIA_API_KEY"].focus_set()
    root.mainloop()
    return 0


def run_cli() -> int:
    stored = read_current()
    updates: dict[str, str] = {}
    for name, label in FIELDS:
        print(f"{label}\n  in .env now: {describe(stored.get(name))}")
        value = getpass.getpass("  new value (hidden, Enter to keep): ").strip()
        if not value:
            continue
        problem = validate(name, value)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        if name == "NVIDIA_API_KEY" and not value.startswith("nvapi-"):
            print("  note: NVIDIA API keys normally start with 'nvapi-'; saving it as given.")
        updates[name] = value
    if not updates:
        print("Nothing entered, so .env was left unchanged.")
        return 0
    write_keys(updates)
    print(f"Saved {', '.join(updates)} to {ENV_FILE}.")
    for name in shell_overrides():
        if name in updates:
            print(f"Note: {name} is also set in this shell and takes precedence over .env.")
    print(f"Check it with:  python scripts/live_check.py --only {TEST_ADAPTER}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Store NVIDIA credentials in .env")
    parser.add_argument("--cli", action="store_true", help="hidden terminal prompts, no window")
    args = parser.parse_args()

    if args.cli:
        return run_cli()
    try:
        import tkinter  # noqa: F401 - probing for a display toolkit
    except ImportError:
        print("tkinter is not available here; falling back to terminal prompts.\n")
        return run_cli()
    return run_form()


if __name__ == "__main__":
    raise SystemExit(main())

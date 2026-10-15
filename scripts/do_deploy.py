"""Create or update the DigitalOcean app, with secrets merged in from ``.env``.

Why this exists instead of ``doctl apps update --spec .do/app.yaml``:
``update`` *replaces* the app's spec. The committed spec deliberately holds no
secrets, so applying it directly would silently delete the database URL and
every key from the running app, and the next deploy would fail at the
migration with nothing obviously wrong. This script always sends the committed
spec plus the four secrets, so create and update are the same safe operation.

What it does with the secrets:

* reads them from the git-ignored ``.env`` (put there by the masked forms in
  ``set_api_key.py`` and ``set_database_url.py``);
* adds them as app-level ``SECRET`` variables, so they reach the ``api``
  service *and* the ``migrate`` job; DigitalOcean encrypts them on receipt;
* writes the merged spec to a temporary file only for the length of the doctl
  call, then deletes it;
* never prints them. doctl's own output is scrubbed of every secret value
  before it is shown, because ``spec validate`` echoes the spec back.

Usage:
    python scripts/do_deploy.py --validate   # check only, deploy nothing
    python scripts/do_deploy.py              # create the app, or update it
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / ".do" / "app.yaml"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from set_api_key import ENV_FILE, _line_pattern  # noqa: E402

#: Required everywhere the app runs. Missing any of these is refused here,
#: before DigitalOcean is asked to build anything.
SECRETS = (
    "BIOLOGIX_DATABASE_URL",
    "NVIDIA_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
)


def env_value(name: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    matches = _line_pattern(name).findall(ENV_FILE.read_text(encoding="utf-8"))
    if not matches:
        return None
    return matches[-1].split("=", 1)[1].strip().strip("\"'") or None


def find_doctl() -> str:
    found = shutil.which("doctl")
    if found:
        return found
    # winget installs outside PATH until the shell restarts.
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for candidate in packages.glob("DigitalOcean.Doctl*/doctl.exe"):
        return str(candidate)
    raise SystemExit("doctl not found. Install it with: winget install DigitalOcean.Doctl")


def scrub(text: str, secrets: dict[str, str]) -> str:
    for value in secrets.values():
        if value:
            text = text.replace(value, "<secret>")
    return text


def run(doctl: str, args: list[str], secrets: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run([doctl, *args], capture_output=True, text=True, timeout=600, check=False)
    return proc.returncode, scrub((proc.stdout + proc.stderr).strip(), secrets)


def existing_app_id(doctl: str, name: str, secrets: dict[str, str]) -> str | None:
    code, out = run(doctl, ["apps", "list", "--format", "ID,Spec.Name", "--no-header"], secrets)
    if code != 0:
        raise SystemExit(f"could not list apps (is doctl authenticated?)\n{out}")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return parts[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--validate", action="store_true", help="check the spec, deploy nothing")
    args = parser.parse_args()

    secrets = {name: env_value(name) for name in SECRETS}
    missing = [name for name, value in secrets.items() if not value]
    if missing:
        print(f"x missing from .env: {', '.join(missing)}")
        print("  Fill them in with scripts/set_api_key.py and scripts/set_database_url.py.")
        return 1
    if not secrets["BIOLOGIX_DATABASE_URL"].startswith("postgresql"):
        print("x BIOLOGIX_DATABASE_URL in .env is not Postgres; production refuses SQLite.")
        return 1

    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    # App-level, so the migrate job receives them as well as the api service.
    spec["envs"] = [
        {"key": name, "value": value, "type": "SECRET", "scope": "RUN_TIME"}
        for name, value in secrets.items()
    ]

    doctl = find_doctl()
    code, out = run(doctl, ["account", "get", "--format", "Email,Status", "--no-header"], secrets)
    if code != 0:
        print("x doctl is not signed in. Run:  doctl auth init   and paste your API token.")
        return 1
    print(f"+ signed in to DigitalOcean as {out.split()[0] if out else '?'}")

    fd, tmp = tempfile.mkstemp(prefix="biologix-spec-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(spec, fh, sort_keys=False)

        code, out = run(
            doctl, ["apps", "spec", "validate", "--spec", tmp, "--schema-only"], secrets
        )
        if code != 0:
            # --schema-only is not in every doctl release; retry without it.
            code, out = run(doctl, ["apps", "spec", "validate", "--spec", tmp], secrets)
        if code != 0:
            print("x spec rejected by DigitalOcean:\n" + out)
            return 1
        print("+ spec is valid")
        if args.validate:
            return 0

        app_id = existing_app_id(doctl, spec["name"], secrets)
        if app_id:
            print(f"updating app {app_id} …")
            code, out = run(
                doctl,
                ["apps", "update", app_id, "--spec", tmp, "--format", "ID", "--no-header"],
                secrets,
            )
        else:
            print("creating app …")
            code, out = run(
                doctl,
                ["apps", "create", "--spec", tmp, "--format", "ID,DefaultIngress", "--no-header"],
                secrets,
            )
        print(out)
        print("+ submitted — the build is running" if code == 0 else "x doctl failed")
        if code == 0:
            print("  follow it with: doctl apps list   then   doctl apps logs <id> --type build")
        return code
    finally:
        Path(tmp).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

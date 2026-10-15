# Deploying to app.algonix.co.uk

Written for whoever runs this deployment, assuming no prior knowledge of how
this app is put together.

## The short version

Everything runs as one DigitalOcean App Platform app, built from this
repository. GoDaddy keeps the domain and serves DNS. Neon stays as the
database.

| Piece | Where | Public? |
|---|---|---|
| `app.algonix.co.uk` — Next.js console | App Platform `web` component | yes |
| The Python API | App Platform `api` component | **no** |
| Schema migration | App Platform `migrate` job (`PRE_DEPLOY`) | n/a |
| Database | Neon Postgres | — |
| DNS | GoDaddy | — |

**There is no registry step.** App Platform builds `docker/Dockerfile` directly
from GitHub on each push to `main`. You only need Docker Hub or DOCR if you
want to build somewhere other than App Platform.

## Why the API has no public address

This is the part worth understanding, because it is the main reason to keep
both halves on one platform.

The browser already never talks to the Python API: every call goes through the
Next.js server, which proxies it. So a public hostname for the API would be
attack surface serving no purpose. App Platform lets a component declare
`internal_ports` with no `http_port`, which gives it no public hostname at
all — it is reachable at `http://api:8080` from inside the app and from
nowhere else.

The result: there is no `api.algonix.co.uk` for anyone to find, no CORS entry
to get wrong, and the only door into the system is the console itself.

## Why not GoDaddy hosting

Not a preference. GoDaddy's standard cPanel plans serve PHP, static files and
MySQL. Three things rule that out:

1. **FastAPI is ASGI.** cPanel's Python support runs through Passenger, which
   speaks WSGI.
2. **RDKit, SciPy, scikit-learn and pandas are native extensions** — several
   hundred megabytes of compiled wheels, with no compiler available if one is
   missing.
3. **The job queue is an in-process thread pool** that must keep running
   between requests. Shared hosting reaps long-running processes by design.

There is no Postgres on those plans either. A GoDaddy VPS with root could run
this via Docker, if you would rather keep one vendor — it means you own TLS
renewal, the firewall, OS patching and uptime.

## 1. One-time setup

1. **Let App Platform see the repository.** In the DigitalOcean dashboard,
   connect GitHub and grant the DigitalOcean app access to
   `otunmartins/biologix-nvidia`. `deploy_on_push` needs this even though the
   repository is public.
2. **Install and sign in to doctl:**
   ```bash
   winget install DigitalOcean.Doctl
   doctl auth init        # paste an API token at the prompt
   ```
3. **Make sure `.env` holds the four secrets** — `BIOLOGIX_DATABASE_URL`,
   `NVIDIA_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — via the masked
   forms `scripts/set_database_url.py` and `scripts/set_api_key.py`.

## 2. Deploy — always through the script

```bash
python scripts/do_deploy.py --validate   # checks the spec, deploys nothing
python scripts/do_deploy.py              # creates the app, or updates it
```

**Never run `doctl apps update --spec .do/app.yaml` directly.** `update`
replaces the whole spec, and the committed spec deliberately contains no
secrets — so it would silently delete the database URL and every key from the
running app, and the next release would fail at the migration with nothing
obviously wrong.

The script sends the committed spec plus the four secrets as app-level
`SECRET` variables, which DigitalOcean encrypts on receipt and which reach both
the `api` service and the `migrate` job. The merged spec exists only in a
temporary file for the length of the doctl call, and doctl's output is
scrubbed of every secret value before it is shown, because `spec validate`
echoes the spec back.

To rotate a secret: update `.env` with the form, then run the script again.

Two spec fields drift as DigitalOcean renames things — `region` and
`instance_size_slug` — and `--validate` is what catches them.

Non-secret settings are already in the spec: `BIOLOGIX_ENV=production`,
`BIOLOGIX_API_URL=http://api:8080`, the Google redirect URI and the allowed
domain. `BIOLOGIX_ENV=production` forces the session cookie to `Secure` and
refuses a SQLite database outright, so a missing database URL fails the
release instead of booting on a throwaway file.

## 3. The migration runs itself

The `migrate` job is `kind: PRE_DEPLOY`, so it runs before each release and
**gates it** — a failed migration means the new version does not go live. It
uses the same image as the API, so it applies exactly the revisions that
shipped with that build.

This matters because the app refuses to start against a database whose tables
are missing, rather than creating them itself. A forgotten migration fails
loudly at boot instead of quietly at the first query.

## 4. DNS at GoDaddy

**My Products → Domains → algonix.co.uk → DNS → Manage Zones.** One record:

| Type | Name | Points to |
|---|---|---|
| `CNAME` | `app` | the hostname App Platform shows for the app |

Take the exact target from the app's Settings → Domains page. Adding the
domain inside App Platform is what triggers the TLS certificate; DNS alone
does not tell it to answer for that name.

Leave the root `algonix.co.uk` records alone — nothing here touches your main
site or email. Propagation is usually minutes; allow an hour before
concluding something is wrong.

## 5. Google sign-in

In the Google Cloud console, **APIs & Services → Credentials → your OAuth
client**:

- Authorised JavaScript origin: `https://app.algonix.co.uk`
- Authorised redirect URI:
  `https://app.algonix.co.uk/api/bx/auth/google/callback`

Character for character — a trailing slash is a different URI, and Google
returns `redirect_uri_mismatch`.

**The callback points at the console, not the API**, which has no public
address to point at anyway. Google redirects the browser to
`app.algonix.co.uk`, the Next.js proxy forwards it inward, and the API answers
with `Set-Cookie` and a redirect that the proxy relays back. The cookie has to
be set on the origin the browser is using.

Keep the local `http://localhost:3000/...` redirect URI registered too — one
client can hold several, so development keeps working.

## 6. Check it

```bash
curl -s https://app.algonix.co.uk/api/bx/health
# {"status":"ok"}   <- the console reached the API over the internal network

curl -s https://app.algonix.co.uk/api/bx/auth/providers
# google.configured: true
```

There is deliberately no public API URL to curl. If `/api/bx/health` returns
`503`, the `web` component cannot reach `api` — check that `BIOLOGIX_API_URL`
is `http://api:8080` and that the service is named `api`.

Then open the site, sign in, and run one campaign.

## What is not production-ready yet

Deploying does not change these, and none is hypothetical:

- **One instance, not autoscaled.** The queue is in-process, so in-flight work
  dies on restart and a second instance would hold jobs the first cannot see.
  Making the queue durable in Postgres is the largest remaining gap, and it is
  the thing that would let this scale at all.
- **No login rate limiting or account lockout.** `accounts.py` says so itself.
  Google sign-in sidesteps it for anyone who uses it; the password path does
  not.
- **No password reset and no MFA.**
- **No GPU.** nvMolKit and OpenMM CUDA stay unavailable on any CPU host, and
  `availability_report()` says so per adapter rather than silently falling
  back.
- **Backups** are whatever Neon's retention gives you. Know what that is
  before you rely on it.
- **The image has not been built end to end.** A local build was attempted and
  failed for an environmental reason — the Docker VM could not reach
  `pypi.org` — not because of anything in the Dockerfile. The substantive
  risk it would have caught was a dependency with no cp313 manylinux wheel,
  since `python:3.13-slim` carries no compiler; that was checked against PyPI
  instead, and numpy, scipy, scikit-learn, pandas, RDKit, psycopg-binary and
  pydantic-core all publish one at the pinned versions. App Platform's first
  build remains the first full test.

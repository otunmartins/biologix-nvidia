# Deploying to app.algonix.co.uk

Written for whoever runs this deployment, assuming no prior knowledge of how
this app is put together.

## The short version

**GoDaddy holds the domain. It does not host the app.** Point DNS at two
platforms that can run it, and keep `algonix.co.uk` where it is.

| Piece | Where | Why there |
|---|---|---|
| `app.algonix.co.uk` — Next.js console | Vercel | Next.js runs natively; nothing to configure |
| `api.algonix.co.uk` — Python API | Render, Railway or Fly.io | Needs one long-running container |
| Database | Neon Postgres | Already what the store expects |
| DNS for both | GoDaddy | Where the domain already is |

## Why not GoDaddy hosting

This is not a preference. GoDaddy's standard cPanel shared plans serve PHP,
static files and MySQL. Three things in this app rule that out:

1. **FastAPI is ASGI.** cPanel's Python support runs through Passenger, which
   speaks WSGI. FastAPI needs an ASGI server, so shared cPanel needs a
   workaround at best.
2. **RDKit, SciPy, scikit-learn and pandas are native extensions**, several
   hundred megabytes of compiled wheels. Shared hosting disk and memory
   quotas are not built for this, and there is no compiler available if a
   wheel is missing.
3. **The job queue is an in-process thread pool** that must keep running
   between requests. Shared hosting reaps long-running processes; that is the
   product working as intended.

There is no Postgres on those plans either — MySQL only — and the store is
Postgres or SQLite.

GoDaddy also sells VPS plans with root access, and those *can* run this via
Docker. That is a real option if you would rather keep one vendor; it means
you own TLS renewal, the firewall, OS patching and uptime. The managed route
below is less work and is what the rest of this guide assumes.

## Before you start

Three accounts: Vercel, a container host (Render is the least fiddly), and
Neon. All three have free tiers sufficient to see this working end to end.

> **Create every secret on a machine you trust.** A connection string, a
> Google client secret and an NVIDIA API key are all bearer credentials: the
> machine you paste them on can read them. If that machine has a TLS
> intercepting proxy installed, pasting a secret there is publishing it.

## 1. Database

Create a Neon project and copy the connection string. Change the scheme to
`postgresql+psycopg` and keep `sslmode=require`:

```
postgresql+psycopg://user:password@ep-xxx-pooler.region.aws.neon.tech/dbname?sslmode=require
```

Prefer the **pooled** host (`-pooler`) for a web application.

The schema is created by migration, never by the app:

```bash
export BIOLOGIX_DATABASE_URL="postgresql+psycopg://…"
alembic upgrade head
```

Run this once now, and as a release step on every deploy that changes the
schema. The app refuses to start against a database whose tables are missing
rather than creating them itself, so a forgotten migration fails loudly at
boot instead of quietly at the first query.

## 2. API

Deploy `docker/Dockerfile` from the repository root. On Render: New → Web
Service → point at the repo → Docker → Dockerfile path `docker/Dockerfile`.

**Settings that are not optional:**

- **Instance count: 1.** Not autoscaled. The job queue lives in process
  memory, so a second instance holds jobs the first cannot see, and a
  campaign started on one is invisible to the other. `abandon_orphaned_work`
  marks interrupted jobs at startup so a restart shows an honest failure
  rather than a spinner that never resolves — but it cannot recover the work.
- **Not serverless.** A function that sleeps between requests kills
  in-flight campaigns. The container must stay up.
- **Health check path: `/health`.**
- **Release command: `alembic upgrade head`.**

Environment variables:

```bash
BIOLOGIX_ENV=production
BIOLOGIX_DATABASE_URL=postgresql+psycopg://…?sslmode=require
NVIDIA_API_KEY=nvapi-…
# Google sign-in — omit both and the button simply does not appear
GOOGLE_CLIENT_ID=…apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=…
GOOGLE_REDIRECT_URI=https://app.algonix.co.uk/api/bx/auth/google/callback
GOOGLE_ALLOWED_DOMAINS=algonix.co.uk
```

`BIOLOGIX_ENV=production` forces the session cookie to `Secure` and refuses an
explicit attempt to turn that off. You do not set
`BIOLOGIX_SESSION_COOKIE_SECURE` yourself.

Note the redirect URI points at **app**, not **api**. That is correct and
explained in step 5.

## 3. Frontend

Deploy `frontend/` to Vercel. Root directory `frontend`; the framework is
detected automatically.

One environment variable:

```bash
BIOLOGIX_API_URL=https://api.algonix.co.uk
```

It is read at runtime, not inlined at build time, so pointing the app at a
different backend is a redeploy of configuration rather than of code. It is
deliberately **not** `NEXT_PUBLIC_`: the browser never learns the API's
address, because every call goes through the Next.js server.

## 4. DNS at GoDaddy

In GoDaddy: **My Products → Domains → algonix.co.uk → DNS → Manage Zones**.

Add what each platform asks for — the exact values come from their dashboards,
and inventing them here would be guessing:

| Type | Name | Points to |
|---|---|---|
| `CNAME` | `app` | the hostname Vercel gives you (typically `cname.vercel-dns.com`) |
| `CNAME` | `api` | the hostname your container host gives you |

Then add the custom domain **inside each platform too** — DNS alone does not
tell Vercel that it should answer for `app.algonix.co.uk`, and the TLS
certificate is issued from that side.

Leave the root `algonix.co.uk` records alone. Nothing here touches your main
site or email. Propagation is usually minutes; allow an hour before
concluding something is wrong.

## 5. Google sign-in

In the Google Cloud console: **APIs & Services → Credentials → your OAuth
client**.

- Authorised JavaScript origin: `https://app.algonix.co.uk`
- Authorised redirect URI:
  `https://app.algonix.co.uk/api/bx/auth/google/callback`

Character for character — a trailing slash is a different URI, and the error
Google returns is `redirect_uri_mismatch`.

**Why the callback goes to `app` and not `api`:** the session cookie has to be
set on the origin the browser is actually using. Google sends the browser to
`app.algonix.co.uk`, the Next.js proxy forwards the request to the API, and the
API answers with `Set-Cookie` and a redirect that the proxy passes back. A
callback pointed at `api.algonix.co.uk` would set a cookie for the wrong host
and sign nobody in.

## 6. Check it

```bash
curl -s https://api.algonix.co.uk/health
# {"status":"ok"}

curl -s https://app.algonix.co.uk/api/bx/health
# {"status":"ok"}   <- proves the proxy reaches the API

curl -s https://app.algonix.co.uk/api/bx/auth/providers
# google.configured: true, once the credentials are set
```

Then open `https://app.algonix.co.uk`, register, and walk one campaign.

If the second command fails while the first succeeds, `BIOLOGIX_API_URL` is
wrong or the API is not reachable from Vercel. The proxy answers `503` with
the address it tried, which names the problem.

## What is not production-ready yet

Deploying does not change these, and none is hypothetical:

- **One instance only.** In-flight work dies on restart. Making the queue
  durable in Postgres is the fix, and it is the largest remaining gap.
- **No login rate limiting or account lockout.** `accounts.py` says so
  itself. Google sign-in sidesteps it for anyone who uses it; the password
  path does not.
- **No password reset and no MFA.**
- **No GPU.** nvMolKit and OpenMM CUDA stay unavailable on any CPU host, and
  `availability_report()` reports that per adapter rather than silently
  falling back.
- **Backups.** Neon has its own; know its retention before you rely on it.

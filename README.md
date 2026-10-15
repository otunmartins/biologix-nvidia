# Biologix-NVIDIA

Research prototype for discovering **polymer excipients** that may improve
biologic drug stability and delivery, with NVIDIA-accelerated components.

> **This is a research prototype.** Nothing it produces is a validated
> prediction of drug stability, shelf life or clinical performance. Three
> categories of output are kept strictly distinct everywhere in the code and in
> every report:
>
> | Tier | Meaning |
> |---|---|
> | `hypothesis` | Generated or proposed, not computed. Candidate structures live here. |
> | `computed` | Software output: descriptors, filters, simulation observables, model outputs. Reproducible from the recorded manifest. **Not** a measurement of reality. |
> | `measured` | Imported from a laboratory assay, with provenance. |
>
> This prototype has no assay integration, so it never returns `measured`
> data.

## The problem

Biologic formulations need excipients that keep the protein from aggregating,
unfolding or adsorbing to surfaces. Polymers are promising candidates, but a
polymer is awkward to reason about computationally: it is not a molecule. It is
a *specification* — repeat units, a composition, an architecture, a chain-length
distribution — from which many molecules could be instantiated.

Most of the care in this codebase goes into not losing that distinction. Any
code that needs an ordinary molecule (fingerprints, conformers, docking,
simulation) must build an explicit capped oligomer and record how it was built,
so the resulting numbers can be interpreted as what they are: properties of a
DP-*n* oligomer, not of a polydisperse polymer.

## What works today

| Area | State |
|---|---|
| Core models, provenance, settings | Built, tested |
| Polymer representation, polymerisation rules, oligomer construction | Built, tested |
| Biologic targets, structure preparation | Built, tested |
| Screening: descriptors, constraints, fingerprints, diversity | Built, tested |
| NVIDIA/BioNeMo adapters (6) | Built, tested; **4 of 5 NIMs answer live** — see [VERIFICATION.md](docs/VERIFICATION.md) |
| Simulation: platforms, parameterisation, assembly, MD runs | Built |
| MCP server | Built, tested |
| Ranking (transparent, weighted, cohort-relative) | Built, tested |
| Persistence: runs, candidates, discards, jobs | Built, tested |
| Job queue, generate-and-screen pipeline | Built, tested |
| Accounts, sessions, roles | Built, tested |
| Programs, triage decisions, audit trail | Built, tested |
| Per-candidate analyses (DiffDock, Boltz-2) as tracked jobs | Built, tested |
| REST API: accounts, programs, campaigns, ranking, jobs, status | Built, tested |
| Web application (Next.js · TypeScript · Tailwind) | Built |
| Surrogate model, reports | **Not started** |

The adapter caveat still matters, in two halves. **Hosted NIMs: four of five
verified live** as of 2026-10-14 — GenMol, MolMIM, DiffDock and Boltz-2 each
answered a real call. ESM-2 cannot: NVIDIA retired that hosted endpoint on
2026-08-25 and it now returns HTTP 410, so it needs the self-hosted container.
**GPU code paths: none executed** — the development machine has no NVIDIA GPU,
so nvMolKit, OpenMM CUDA and every speedup claim remain unmeasured, and
diversity falls back to the RDKit CPU backend and records that it did.
`availability_report()` says all of this at runtime, per adapter, and
`/verification` carries the live-call evidence separately from it.

## Quick start

```bash
uv venv && uv pip install -e ".[dev,simulation,mcp]"
cp .env.example .env            # optional; everything has a working default

pytest                          # 259 tests, no network, no GPU
python -m biologix.mcp_server --check
biologix-api --reload           # http://127.0.0.1:8080/docs

cd frontend && npm install && npm run dev    # http://localhost:3000
```

Open <http://localhost:3000>, create an account, and the six journeys below are
all walkable. The browser never talks to the Python API directly: every call
goes through the Next.js server, so the backend's address stays out of client
code, it needs no CORS entry for the app, and the session cookie stays
HttpOnly in both directions. Point the app at another backend with
`BIOLOGIX_API_URL`, which is read at runtime rather than baked into the build.

## The application

Three roles — formulation scientist, computational chemist, reviewer — and six
journeys:

| Journey | What the app does |
|---|---|
| **Open a program** | A program fixes one biologic target. It cannot be changed afterwards: candidates screened against two different proteins cannot share a history. |
| **Run a campaign** | Choose a generator (baseline, GenMol, MolMIM), a constraint set and a count. The live pipeline shows the real stages the server reports. An unavailable NVIDIA adapter fails the campaign with its own reason rather than being swapped for the baseline. |
| **Triage** | Order candidates by your own weights — each criterion carries its rationale — and audit any row down to each criterion's contribution to its score. |
| **Decide** | Shortlist or reject, *with a reason*. The reason is required because it is the only part of a triage call the numbers cannot give back. The weights you were looking at are stored with the verdict. |
| **Analyse** | Per candidate, run DiffDock for poses or Boltz-2 for a complex and affinity figure. Both are tracked jobs; each result carries the caveat that stops it being read as a measurement. |
| **Audit** | An append-only trail of every program, campaign, decision and analysis, naming who did it and when. |

Two people may hold opposite verdicts on one candidate. The app keeps both
rather than resolving them, shows each reader their own, and names whose is
whose on the shortlist.

Accounts: registration is open, sessions are HttpOnly cookies, passwords are
PBKDF2-HMAC-SHA256 with a per-user salt, and only the SHA-256 of a session
token is stored. That is session auth for a single-lab internal tool — there
is no password reset, no MFA and no account lockout, and it should not be
exposed to the open internet as-is.

A run, end to end, with no GPU and no credentials:

```bash
curl -X POST localhost:8080/runs -H 'content-type: application/json' \
     -d '{"target_key": "human-insulin", "n": 12}'
curl localhost:8080/runs/<run_id>                       # progress, then the full record
curl 'localhost:8080/runs/<run_id>/ranking?weights=flexibility:2,polarity:1'
```

No credentials and no GPU is a supported configuration. The baseline generator,
RDKit screening and CPU simulation all run; NVIDIA adapters report
`unavailable` with a reason and are skipped rather than faked.

## NVIDIA components

| Adapter | Model | Contributes |
|---|---|---|
| `bionemo-esm2` | ESM-2 650M | Per-chain embeddings of the biologic target |
| `bionemo-genmol` | GenMol 2.0.0 | Fragment-based molecule generation → repeat units |
| `bionemo-molmim` | MolMIM 1.0.0 | Latent sampling around known excipient chemistry |
| `bionemo-boltz2` | Boltz-2 | Complex structure + binding-affinity estimate |
| `bionemo-diffdock` | DiffDock 2.1.0 | Blind docking poses → starting geometries for MD |
| `nvmolkit` | nvMolKit | GPU fingerprints, similarity, clustering, conformers |

Plus OpenMM's CUDA platform for molecular dynamics.

Setting `NVIDIA_API_KEY` makes all five NIM adapters reachable as hosted
endpoints with no GPU and no containers. See
[docs/NVIDIA_SETUP.md](docs/NVIDIA_SETUP.md) for self-hosting, nvMolKit, and
the BioNeMo Agent Toolkit skills.

### Three caveats the code enforces

These are attached to the result payloads, not left to documentation:

1. **Boltz-2 affinity is not a Kd.** Its affinity head was trained on
   small-molecule protein–ligand binding; a polymer oligomer is out of
   distribution, and excipients act mostly through colloidal and interfacial
   mechanisms rather than pocket binding. `out_of_distribution` is always true.
2. **DiffDock confidence is not binding strength.** It estimates pose
   correctness and is not comparable across ligands. The oligomer's
   rotatable-bond count ships with every result so the flexibility gap is
   visible.
3. **MolMIM's objectives are the wrong objectives.** QED and plogP are
   oral-drug-likeness scores, which select *against* what excipients need. The
   adapter defaults to similarity-constrained sampling and records a caveat
   whenever CMA-ES is used anyway.

## Design commitments

Each of these is enforced by a test, not just a convention:

- **An unavailable adapter is skipped, never substituted.**
  `AdapterUnavailable` and `AdapterCallFailed` are distinct: "could not run"
  and "ran and failed" are different claims.
- **Nothing is truncated to fit a model.** A chain longer than ESM-2's 1022
  residues raises `SequenceTooLong`, because a truncated protein is a different
  protein. Multi-chain biologics are never concatenated.
- **Chemistry a model cannot see is reported, not dropped.** PEGylation,
  acylation and glycosylation are invisible to a sequence model, so they come
  back under `unrepresented_chemistry`.
- **Every computation carries a manifest** recording software versions, git
  revision, hardware, seeds, the backend actually used (not the one requested),
  and what was excluded and why. An empty exclusion list is a claim that
  nothing was dropped.
- **Cache hits are recorded,** because "cached" and "freshly computed" are
  different claims about where a number came from.
- **Refusals are honest about composition.** Capping a PEG repeat unit with a
  hydroxyl makes a peroxide at the terminus, so `build_oligomer` refuses —
  valid parts do not always compose into a valid whole.

## Layout

```
backend/biologix/
  polymers/     representation, rules, oligomer construction, generation
  targets/      biologic models, registry, structure preparation
  screening/    descriptors, constraints, fingerprints, diversity
  ranking/      weighted, cohort-relative ordering with per-criterion audit
  nvidia/       BioNeMo + nvMolKit adapters, availability, caching
  simulation/   OpenMM platforms, parameterisation, assembly, runs
  store/        SQLite persistence: runs, candidates, discards, jobs
  jobs/         generate-and-screen pipeline, in-process worker pool
  api/          FastAPI: status, targets, runs, ranking
  mcp_server.py agent-callable pipeline tools
frontend/src/
  app/          / component status · /runs · /runs/[id] · route handlers
  components/   console, run form, run list, ranked table, weight controls
  lib/          server-side reads of the Python API, shared types
backend/tests/  no network, no GPU
docs/           NVIDIA_SETUP.md, VERIFICATION.md
scripts/        BioNeMo Agent Toolkit skill installation
```

## Licence

Apache-2.0. Model weights and NIM containers carry their own terms — ESM-2
(MIT, Meta), Boltz-2 and DiffDock (MIT), GenMol and MolMIM (NVIDIA AI
Foundation Models Community License), nvMolKit (Apache-2.0) — plus the NVIDIA
AI product agreement for NIM packaging. Each adapter records its licence in
the run manifest.

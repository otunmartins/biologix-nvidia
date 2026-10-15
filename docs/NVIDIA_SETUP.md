# NVIDIA setup

How to make the NVIDIA components in this project actually run, and what each
one contributes. Current verification status is in
[VERIFICATION.md](VERIFICATION.md) — read that before quoting any result.

## The short version

With no credentials and no GPU, the project still runs end to end: the baseline
generator proposes candidates, RDKit screens them, and OpenMM simulates on CPU.
Adapters that cannot run report `unavailable` with a reason and are skipped.
Nothing is ever substituted with a placeholder value.

To check where you stand:

```bash
python -m biologix.mcp_server --check
```

One environment variable changes the most:

```bash
# .env
NVIDIA_API_KEY=nvapi-...      # from https://build.nvidia.com
```

To enter it through a masked local form instead of editing `.env` by hand
(the key never reaches a shell history, a command line or a web page):

```bash
python scripts/set_api_key.py          # add --cli for hidden terminal prompts
```

That makes all five NIM adapters — ESM-2, GenMol, MolMIM, Boltz-2 and
DiffDock — reachable as **hosted**
endpoints — no GPU, no containers, no `docker pull`.

## What each component is for

| Adapter | Model | Contributes | Needs |
|---|---|---|---|
| `bionemo-esm2` | ESM-2 650M | Per-chain embeddings of the biologic. Features for the surrogate model. | API key *or* local container |
| `bionemo-genmol` | GenMol 2.0.0 | Fragment-based molecule generation → repeat units | API key *or* GPU container |
| `bionemo-molmim` | MolMIM 1.0.0 | Latent sampling around known excipient chemistry; molecule embeddings | API key *or* local container |
| `bionemo-boltz2` | Boltz-2 | Biologic–oligomer complex structure **and binding affinity** | API key *or* local container |
| `bionemo-diffdock` | DiffDock 2.1.0 | Blind docking poses → starting geometries for MD | API key *or* local container |
| `nvmolkit` | nvMolKit ≥ 0.3 | GPU fingerprints, bulk Tanimoto/cosine, Butina clustering, ETKDG, MMFF | GPU, CC ≥ 7.0, CUDA 12.6+ |
| OpenMM CUDA | — | GPU molecular dynamics | CUDA runtime on the host |

### Where each one sits in the pipeline

```
target (Biologic)                    candidates (PolymerCandidate)
      |                                        |
   ESM-2 ──> embeddings                 baseline / GenMol / MolMIM
      |                                        |
      |                                  repeat-unit validation
      |                                        |
      |                              RDKit / nvMolKit screening
      |                                        |
      └────────────> DiffDock ──> pose ──> OpenMM simulation
                        |
                     Boltz-2 ──> complex + affinity estimate
```

DiffDock before OpenMM is the point of having it: it replaces a random initial
placement with a contact geometry, so the MD refines a pose instead of
searching for one.

## Hosted endpoints

Defaults in `.env.example` point at `https://health.api.nvidia.com/v1/biology`.
Verify each path against your own model page on build.nvidia.com — the ESM-2
path in particular could not be confirmed from a publicly fetchable reference
at build time, and the adapter says so in its availability report. Override
with `BIOLOGIX_<MODEL>_PATH` if yours differs.

## Self-hosted containers

`NGC_API_KEY` is needed only to pull images:

```bash
docker login nvcr.io -u '$oauthtoken' -p "$NGC_API_KEY"
```

Then, one container per model (ports chosen to match `.env.example`):

```bash
docker run --rm --gpus all -p 8000:8000 nvcr.io/nim/nvidia/genmol:2.0.0
docker run --rm --gpus all -p 8001:8000 nvcr.io/nim/mit/diffdock:2.1.0
docker run --rm --gpus all -p 8002:8000 nvcr.io/nim/nvidia/molmim:1.0.0
docker run --rm --gpus all -p 8003:8000 nvcr.io/nim/mit/boltz2:latest
```

and point the adapters at them:

```bash
BIOLOGIX_GENMOL_BASE_URL=http://127.0.0.1:8000
BIOLOGIX_DIFFDOCK_BASE_URL=http://127.0.0.1:8001
BIOLOGIX_DIFFDOCK_PATH=/molecular-docking/diffdock/generate
BIOLOGIX_MOLMIM_BASE_URL=http://127.0.0.1:8002
BIOLOGIX_MOLMIM_PATH=/generate
BIOLOGIX_BOLTZ2_BASE_URL=http://127.0.0.1:8003
BIOLOGIX_BOLTZ2_PATH=/predict
```

A local base URL waives the API-key requirement automatically. "Local" means a
loopback address, `localhost`, or `host.docker.internal` — matched as a whole
hostname, so `localhost.example.com` is correctly treated as remote and still
requires credentials.

## nvMolKit

Deliberately **not** in the default dependency set: installing it on a
CPU-only machine pulls a multi-gigabyte CUDA PyTorch build that cannot be used.

```bash
uv pip install --torch-backend=cu128 nvmolkit
# then
BIOLOGIX_ENABLE_NVMOLKIT=true
```

Before quoting any speedup, confirm the GPU results match the CPU ones:

```bash
pytest -m gpu
python -m biologix.screening.fingerprints   # agreement check
```

`backend/benchmarks/` refuses to report a speedup it did not measure.

## BioNeMo Agent Toolkit

NVIDIA's [Agent Toolkit](https://github.com/NVIDIA-BioNeMo/bionemo-agent-toolkit)
publishes agent skills that call BioNeMo NIMs directly. Install the ones
relevant here:

```bash
scripts/install_bionemo_skills.sh            # or .ps1 on Windows
scripts/install_bionemo_skills.sh --list     # see the full catalogue
```

This installs `boltz2-nim`, `diffdock-nim`, `molmim-nim`, `genmol-nim` and
`nvmolkit`. Node.js is required (the skills CLI ships via npm); it is a
convenience, not a dependency — Biologix reaches the NIMs over plain HTTP
through its own adapters either way.

### Why both the toolkit and `biologix-mcp`

They cover different things, and using only one leaves a gap:

- **Toolkit skills** give an agent direct access to a *model*. Right for "fold
  this sequence", "dock this ligand".
- **`biologix-mcp`** gives an agent the *pipeline*, which is where the
  polymer-specific discipline lives and which no general NIM skill can know
  about:
  - a polymer specification is not a molecule, so anything needing a molecule
    builds an explicit capped oligomer and reports what it built;
  - a generated molecule is not a repeat unit until it has a polymerisable
    handle and passes the polymerisation rules;
  - every number carries an evidence tier (`hypothesis` / `computed` /
    `measured`) and a run manifest;
  - an affinity for a polymer oligomer is flagged out-of-distribution, because
    Boltz-2's affinity head was trained on small-molecule binding.

Register it:

```bash
claude mcp add biologix -- python -m biologix.mcp_server
```

Tools exposed: `nvidia_status`, `list_targets`, `generate_candidates`,
`screen_candidates`, `embed_target`, `dock_candidate_to_target`,
`predict_complex_affinity`.

## Reading the results

Three caveats are load-bearing, and the code attaches them to the payloads
rather than relying on anyone reading this file:

1. **Boltz-2 affinity is not a Kd.** Its affinity head was trained on
   small-molecule protein–ligand data. A polymer oligomer is out of
   distribution, and excipients mostly act through colloidal and interfacial
   mechanisms rather than pocket binding. The value ranks candidates under a
   fixed model; that is all.
2. **DiffDock confidence is not binding strength.** It estimates whether a
   pose is geometrically right, and it is not comparable across different
   ligands. Pose quality also degrades with ligand flexibility, and a polymer
   oligomer has far more rotatable bonds than the drug-like ligands DiffDock
   was trained on — the count is reported with every result.
3. **MolMIM's objectives are the wrong objectives.** QED and plogP are
   oral-drug-likeness scores; optimising an excipient for QED selects against
   the high molecular weight and polar repeat units excipients need. The
   adapter therefore defaults to similarity-constrained sampling
   (`algorithm="none"`), and records a caveat in the manifest whenever CMA-ES
   is used anyway.

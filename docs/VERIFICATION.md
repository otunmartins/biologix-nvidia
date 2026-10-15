# Verification status

This file records what has actually been executed and what has not. It exists
because "the adapter is written" and "the adapter works" are different claims,
and the gap between them is where a research prototype becomes misleading.

Last updated: 2026-10-14.

## Development environment

Everything below was run on a single machine:

| | |
|---|---|
| OS | Windows 10 Pro 10.0.19045 |
| Python | 3.13.7 (CPython, 64-bit) |
| NVIDIA GPU | **none** — `nvidia-smi` is not on PATH |
| CUDA runtime | **none** |
| `NVIDIA_API_KEY` | set since 2026-10-13 (hosted NIM calls now possible) |
| NIM containers | **none running** |

One consequence still shapes every row below: no GPU code path has been
executed here. Since the key was set on 2026-10-13, hosted NIMs *have* been
called over the wire. As of the 2026-10-14 run, **four of the five answer
successfully** — GenMol, MolMIM, DiffDock and now Boltz-2, which had failed
upstream the day before. The fifth, ESM-2, cannot answer at all: NVIDIA
retired that hosted endpoint. Each row says which, and `var/live-check.json`
holds the machine-readable report that `/verification` and the console read.

## What has been verified

| Component | Status | How |
|---|---|---|
| Core models (`Biologic`, `PolymerCandidate`, `RepeatUnit`) | **Executed** | Unit tests |
| Polymerisation rules and oligomer construction | **Executed** | Unit tests; builds real molecules via RDKit |
| Descriptors, filters, fingerprints, diversity | **Executed** | Unit tests, RDKit CPU backend |
| Provenance manifests | **Executed** | Unit tests |
| Baseline generator | **Executed** | Unit tests; deterministic from a seed |
| OpenMM availability and platform resolution | **Executed** | `Reference`, `CPU`, `OpenCL` present; resolves to `CPU` |
| MCP server tool surface and refusal payloads | **Executed** | Unit tests |
| Adapter request construction (all 5 NIMs) | **Executed** | Unit tests; parameter ranges enforced client-side, asserted to send nothing when out of range |
| Adapter response parsing (all 5 NIMs) | **Executed against fixtures** | `respx`-mocked responses built from the documented schemas |
| Credential and health-probe logic | **Executed** | Unit tests, including the local-vs-remote hostname boundary |

## What has *not* been verified

| Component | Status | What is missing |
|---|---|---|
| ESM-2 live call | **Impossible on the hosted endpoint — retired upstream** | Diagnosed 2026-10-14 by probing the route space directly. The configured path had the wrong vendor prefix (`/nvidia/esm2-650m` → HTTP 404); the correct one is `/meta/esm2-650m`, which answers **HTTP 410 Gone**: *"This endpoint has reached its end of life on 2026-08-25T09:00:00Z and is no longer available."* No successor is named in the response, and `integrate.api.nvidia.com/v1/embeddings` does not serve it either. So the response schema remains unconfirmed and the parser untested against real output. The only remaining route is the self-hosted `esm2-650m` NIM container, which needs a GPU. |
| GenMol live call | **Executed 2026-10-13 and 2026-10-14 (hosted)** | `scripts/live_check.py --only genmol` against `https://health.api.nvidia.com/v1/biology/nvidia/genmol/generate`: HTTP 200, response keys `status` and `molecules`, as the adapter expects. One minimal call only; the self-hosted GPU container is still never executed. |
| MolMIM live call | **`/generate` executed 2026-10-13 and 2026-10-14 (hosted)** | `scripts/live_check.py --only molmim`, `algorithm="none"`: HTTP 200. The hosted body is **not** the documented `{"generated": [...]}`; it is `{"molecules": "<JSON string of [{sample, score}]>", "score_type": "tanimoto_similarity"}`. The parser now accepts both. CMA-ES mode, `/embedding`, `/sampling`, `/hidden` and `/decode` are still never executed. |
| Boltz-2 live call | **Executed 2026-10-14 (hosted)** | `scripts/live_check.py --only boltz2`: HTTP 200 in 2.09 s against `https://health.api.nvidia.com/v1/biology/mit/boltz2/predict`, structure prediction parsed. This is the same request that failed the previous day with a server-side `AcceleratorError: CUDA error: an illegal memory access was encountered` (HTTP 422) across seven payload variants — so that fault was upstream and transient, exactly as recorded, and nothing in this repository changed to fix it. The response carried `affinities`, `confidence_scores`, `pae`, `pde`, `plddt` and the per-chain PTM/iPTM score families. Still unverified: **affinity prediction**, which `--affinity` turns on and which NVIDIA documents as adding 2–4x runtime, and the polymer/ligand size ceilings. |
| DiffDock live call | **Executed 2026-10-13 and 2026-10-14 (hosted)** | `scripts/live_check.py --only diffdock`: HTTP 200 in 1.6–4.6 s, one pose parsed, `schema_mismatch` empty. The body carried four keys the parser did not expect — `details`, `ligand`, `protein`, `trajectory` — reproduced identically on 2026-10-14, so they are stable ground truth to fold into `diffdock.py` rather than a one-off. Still unverified: multi-pose requests, `num_poses` > 1, and the `trajectory` field's meaning. |
| nvMolKit GPU paths | **Never executed** | Needs an NVIDIA GPU with compute capability ≥ 7.0 and CUDA 12.6+. |
| OpenMM CUDA platform | **Never executed** | The PyPI wheel exposes a CUDA platform only when a CUDA runtime is present. |
| Any claimed GPU speedup | **Never measured** | `backend/benchmarks/` must run on GPU hardware. No speedup figure appears anywhere in this repository. |

`availability_report()` reports this at runtime too: each adapter carries
`code_path_executed_in_this_build: false`, and
`summary.unverified_adapters` lists them. That flag is asserted by
`test_nvidia_availability.py::test_no_adapter_claims_to_be_verified_here`, so
it cannot quietly flip to true without a test failing.

## Closing the gaps

In rough order of how much each buys:

1. **Set `NVIDIA_API_KEY`.** This alone makes all five NIMs — ESM-2, GenMol,
   MolMIM, Boltz-2 and DiffDock — reachable as hosted endpoints with no GPU
   and no containers, turning
   five "never executed" rows into real ones. Then run the live checks:
   ```bash
   python -m biologix.mcp_server --check     # should show the adapters available
   pytest -m network                          # live-endpoint tests, skipped by default
   ```
   If the ESM-2 response shape differs from what the adapter expects, it will
   say so rather than return a wrong vector — the parser records which shape it
   saw.

2. **Run on a GPU machine.** Unlocks nvMolKit, the OpenMM CUDA platform, and
   self-hosted GenMol. Before any speedup is quoted,
   `biologix.screening.fingerprints.agreement` must confirm the GPU
   fingerprints match RDKit's.

3. **Confirm the DiffDock response schema** against a running container's
   `/docs` and replace the tolerant parser's guesses with the real field names.

Until a row above moves, no document, report or UI in this project should
describe the corresponding component as working.

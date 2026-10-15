"""One-shot live check of the hosted BioNeMo NIM endpoints.

Purpose: turn the "never executed" rows in docs/VERIFICATION.md into real
ones. For each adapter it makes **one** minimal real call, then reports four
things the fixture-based tests cannot establish:

1. whether the configured path exists at all (a 404 means the path is wrong,
   not that the model is broken);
2. what keys the response actually carried, against what the adapter expects;
3. whether this repository's own parser handled that response;
4. how long it took.

Deliberately frugal with credits: the smallest legal payload for each model,
one call each. Boltz-2 affinity is off unless ``--affinity`` is passed, since
NVIDIA documents it as adding 2-4x to the runtime.

Nothing here prints the API key, and the written report records only whether a
key was present.

Usage:
    python scripts/live_check.py                      # all adapters, no affinity
    python scripts/live_check.py --only esm2 molmim
    python scripts/live_check.py --affinity           # include Boltz-2 affinity
    python scripts/live_check.py --json var/live-check.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow running from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from biologix.errors import BiologixError  # noqa: E402
from biologix.settings import Settings, get_settings  # noqa: E402

# A short real peptide and a small real ligand, so nothing here depends on a
# fixture file.
GLP1 = "HAEGTFTSDVSSYLEGQAAKEFIAWLVKGRG"
SMALL_LIGAND = "C=CN1CCCC1=O"  # N-vinylpyrrolidone, the povidone monomer


def _result(name: str, model: str, endpoint: str, ok: bool, **extra: Any) -> dict[str, Any]:
    return {"adapter": name, "model": model, "endpoint": endpoint, "ok": ok, **extra}


def check_esm2(s: Settings, args: argparse.Namespace) -> dict[str, Any]:
    from biologix.nvidia.esm2 import Esm2Adapter, esm2_availability
    from biologix.targets.registry import get_target

    report = esm2_availability(s)
    # A real registry target rather than a bare string, because the adapter
    # takes a Biologic by design: chains stay separate and modifications stay
    # visible.
    biologic = get_target("glp1-7-37")
    adapter = Esm2Adapter(s)
    t0 = time.perf_counter()
    try:
        result = adapter.embed_biologic(biologic, use_cache=False)
    except BiologixError as exc:
        return _result(
            "bionemo-esm2",
            s.esm2_model,
            report["endpoint"],
            False,
            error=type(exc).__name__,
            message=str(exc)[:400],
            hint=(
                "A 404 here means BIOLOGIX_ESM2_PATH is wrong for your account; check "
                "the ESM-2 model page on build.nvidia.com. This path was never "
                "confirmable from public docs."
            ),
        )
    finally:
        adapter.close()
    return _result(
        "bionemo-esm2",
        s.esm2_model,
        report["endpoint"],
        True,
        seconds=round(time.perf_counter() - t0, 2),
        biologic=result.biologic_name,
        chains={k: v.hidden_dim for k, v in result.chains.items()},
        n_residues={k: v.n_residues for k, v in result.chains.items()},
        unrepresented_chemistry=result.unrepresented_chemistry,
        note="an embedding is a feature vector, not a prediction",
    )


def check_molmim(s: Settings, args: argparse.Namespace) -> dict[str, Any]:
    from biologix.nvidia.molmim import MolMIMAdapter

    adapter = MolMIMAdapter(s)
    t0 = time.perf_counter()
    try:
        response = adapter.generate_molecules(
            SMALL_LIGAND, algorithm="none", num_molecules=2, particles=2, use_cache=False
        )
    except BiologixError as exc:
        return _result(
            "bionemo-molmim",
            s.molmim_model,
            adapter.endpoint,
            False,
            error=type(exc).__name__,
            message=str(exc)[:400],
        )
    finally:
        adapter.close()
    return _result(
        "bionemo-molmim",
        s.molmim_model,
        adapter.endpoint,
        True,
        seconds=round(time.perf_counter() - t0, 2),
        response_keys=response.raw_keys,
        n_generated=len(response.smiles),
        sample=response.smiles[:2],
    )


def check_genmol(s: Settings, args: argparse.Namespace) -> dict[str, Any]:
    from biologix.nvidia.genmol import GenMolAdapter

    adapter = GenMolAdapter(s)
    t0 = time.perf_counter()
    try:
        response = adapter.generate_molecules(
            "C=CC(=O)N.[*{10-20}]", num_molecules=2, use_cache=False
        )
    except BiologixError as exc:
        return _result(
            "bionemo-genmol",
            s.genmol_model,
            adapter.endpoint,
            False,
            error=type(exc).__name__,
            message=str(exc)[:400],
            hint=(
                "GenMol is reachable hosted: set BIOLOGIX_GENMOL_BASE_URL to "
                "https://health.api.nvidia.com/v1/biology, BIOLOGIX_GENMOL_HOSTED=true, "
                "and verify BIOLOGIX_GENMOL_PATH against its model page."
            ),
        )
    finally:
        adapter.close()
    return _result(
        "bionemo-genmol",
        s.genmol_model,
        adapter.endpoint,
        True,
        seconds=round(time.perf_counter() - t0, 2),
        response_keys=response.raw_keys,
        status=response.status,
        n_generated=len(response.smiles),
        sample=response.smiles[:2],
    )


def check_diffdock(s: Settings, args: argparse.Namespace) -> dict[str, Any]:
    from biologix.nvidia.diffdock import _EXPECTED_KEYS, DiffDockAdapter, _parse_poses

    protein = Path("var/structures/3I40.pdb")
    if not protein.exists():
        from biologix.targets.prepare import fetch_structure

        protein = fetch_structure("3I40").path

    adapter = DiffDockAdapter(s)
    t0 = time.perf_counter()
    try:
        body = adapter.generate(
            protein.read_text(encoding="utf-8"),
            SMALL_LIGAND,
            ligand_file_type="txt",
            num_poses=1,
            steps=1,
            time_divisions=3,
            use_cache=False,
        )
    except BiologixError as exc:
        return _result(
            "bionemo-diffdock",
            s.diffdock_model,
            adapter.endpoint,
            False,
            error=type(exc).__name__,
            message=str(exc)[:400],
        )
    finally:
        adapter.close()

    poses, mismatch = _parse_poses(body)
    observed = sorted(k for k in body if not k.startswith("_"))
    return _result(
        "bionemo-diffdock",
        s.diffdock_model,
        adapter.endpoint,
        True,
        seconds=round(time.perf_counter() - t0, 2),
        response_keys=observed,
        expected_keys=list(_EXPECTED_KEYS),
        unexpected_keys=[k for k in observed if k not in _EXPECTED_KEYS],
        n_poses_parsed=len(poses),
        schema_mismatch=mismatch,
        note=(
            "this response schema was undocumented; the keys above are the ground "
            "truth to fold back into diffdock.py and VERIFICATION.md"
        ),
    )


def check_boltz2(s: Settings, args: argparse.Namespace) -> dict[str, Any]:
    from biologix.nvidia.boltz2 import Boltz2Adapter, _parse_affinity, _parse_structures

    adapter = Boltz2Adapter(s)
    ligand: dict[str, Any] = {"id": "LIG", "smiles": SMALL_LIGAND}
    if args.affinity:
        ligand["predict_affinity"] = True

    t0 = time.perf_counter()
    try:
        body = adapter.predict(
            [{"id": "A", "molecule_type": "protein", "sequence": GLP1}],
            [ligand],
            recycling_steps=1,
            sampling_steps=10,
            diffusion_samples=1,
            use_cache=False,
        )
    except BiologixError as exc:
        return _result(
            "bionemo-boltz2",
            s.boltz2_model,
            adapter.endpoint,
            False,
            error=type(exc).__name__,
            message=str(exc)[:400],
        )
    finally:
        adapter.close()

    structures = _parse_structures(body.get("structures"))
    out = _result(
        "bionemo-boltz2",
        s.boltz2_model,
        adapter.endpoint,
        True,
        seconds=round(time.perf_counter() - t0, 2),
        response_keys=sorted(k for k in body if not k.startswith("_")),
        n_structures=len(structures),
        structure_format=structures[0].format if structures else None,
        confidence_scores=body.get("confidence_scores"),
        affinity_requested=args.affinity,
    )
    if args.affinity:
        affinity = _parse_affinity(body.get("affinities"), "LIG", mw_correction=False)
        out["affinity_parsed"] = affinity.to_dict() if affinity else None
        out["affinity_note"] = (
            "out_of_distribution is always true for a polymer oligomer; this is not a Kd"
        )
    return out


CHECKS: dict[str, Callable[[Settings, argparse.Namespace], dict[str, Any]]] = {
    "esm2": check_esm2,
    "molmim": check_molmim,
    "genmol": check_genmol,
    "diffdock": check_diffdock,
    "boltz2": check_boltz2,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Live check of hosted BioNeMo NIMs")
    parser.add_argument("--only", nargs="+", choices=sorted(CHECKS), help="subset to check")
    parser.add_argument(
        "--affinity", action="store_true", help="include Boltz-2 affinity (2-4x slower)"
    )
    parser.add_argument("--json", type=Path, help="also write the full report here")
    args = parser.parse_args()

    s = get_settings()
    if not s.nvidia_api_key:
        print(
            "NVIDIA_API_KEY is not set, so there is nothing to check live.\n"
            "Get a free key at https://build.nvidia.com/settings/api-keys and put it\n"
            "in .env as:  NVIDIA_API_KEY=nvapi-...",
            file=sys.stderr,
        )
        return 2

    selected = args.only or list(CHECKS)
    print(f"Live check of {len(selected)} adapter(s). One minimal call each.\n")

    results: list[dict[str, Any]] = []
    for name in selected:
        print(f"  {name} ... ", end="", flush=True)
        try:
            result = CHECKS[name](s, args)
        except Exception as exc:  # noqa: BLE001 - a diagnostic must not abort
            result = _result(
                name, name, "?", False, error=type(exc).__name__, message=str(exc)[:400]
            )
        results.append(result)
        if result["ok"]:
            print(f"OK  ({result.get('seconds', '?')}s)")
        else:
            print(f"FAILED  {result.get('error')}: {str(result.get('message', ''))[:120]}")

    n_ok = sum(1 for r in results if r["ok"])
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "api_key_present": True,
        "n_ok": n_ok,
        "n_total": len(results),
        "results": results,
    }

    print(f"\n{n_ok}/{len(results)} reachable.")
    for r in results:
        if r["ok"] and r.get("unexpected_keys"):
            print(f"  ! {r['adapter']}: unexpected response keys {r['unexpected_keys']}")
        if r["ok"] and r.get("schema_mismatch"):
            print(f"  ! {r['adapter']}: schema mismatch {r['schema_mismatch']}")
        if not r["ok"] and r.get("hint"):
            print(f"  > {r['adapter']}: {r['hint']}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nfull report -> {args.json}")
    else:
        print("\n" + json.dumps(report, indent=2, default=str))

    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

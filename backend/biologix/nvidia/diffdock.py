"""BioNeMo DiffDock adapter: blind protein–ligand docking.

API contract, from NVIDIA's DiffDock NIM documentation:

* ``POST {base}/molecular-docking/diffdock/generate`` when self-hosted;
  ``POST {base}/mit/diffdock`` on the hosted endpoint. Both are configurable.
* request: ``protein`` (PDB text), ``ligand`` (text in ``ligand_file_type``),
  ``ligand_file_type`` (``sdf`` | ``mol2`` | ``txt``), ``num_poses`` (1-100,
  default 10), ``time_divisions`` (3-20, default 20), ``steps`` (1-18,
  default 18), ``save_trajectory``, ``skip_gen_conformer``, ``is_staged``.
* health: ``GET {base}/v1/health/ready``; models: ``GET {base}/v1/models``.

**Response schema is only partly documented.** NVIDIA's public reference
describes the request fully but documents the 200 response only as "predicted
molecular docking poses with confidence scores", directing readers to the
running service's own ``/docs``. This adapter therefore parses the field names
used by the DiffDock NIM examples (``ligand_positions``,
``position_confidence``, ``status``, ``trajectory``, ``protein``) and records
which keys it actually received in
:attr:`DockingResult.observed_response_keys`, so a schema change surfaces as a
recorded mismatch rather than as silently empty results.

What a pose is and is not
-------------------------
DiffDock returns *poses ranked by a confidence score*. The score is a
calibrated estimate of whether the predicted pose is within 2 Å RMSD of the
true pose — it is a geometric self-assessment, **not** a binding affinity and
not a score that can be compared across different ligands as if it were one.
Two further gaps apply to this project specifically:

1. **Ligand flexibility.** DiffDock was trained on drug-like ligands, which
   have on the order of ten rotatable bonds. A capped polymer oligomer has
   many more, and pose quality degrades with flexibility. The rotatable-bond
   count of the submitted oligomer is recorded on every result so the caller
   can see how far outside that regime the query sits.
2. **Mechanism.** Excipient polymers typically act at surfaces and interfaces
   rather than by occupying a pocket. A blind-docking pose is useful here as a
   *starting structure for simulation* — it gives the MD in
   :mod:`biologix.simulation.run` a contact geometry instead of a random
   placement — not as evidence that a binding site exists.

Accordingly the poses carry ``interpretation="candidate starting geometry"``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from biologix import EVIDENCE_COMPUTED
from biologix.errors import AdapterCallFailed, AdapterUnavailable
from biologix.nvidia.cache import JsonCache
from biologix.nvidia.endpoints import (
    auth_headers,
    credential_state,
    join,
    missing_credential_reason,
    probe_health,
)
from biologix.polymers.models import PolymerCandidate
from biologix.polymers.oligomer import OligomerBuild, build_oligomer
from biologix.provenance import RunManifest, file_digest, stable_hash
from biologix.settings import Settings, get_settings

DIFFDOCK_LICENCE = (
    "DiffDock weights are released by MIT under the MIT licence; the NVIDIA NIM "
    "packaging is governed additionally by the NVIDIA AI product agreement."
)

LIGAND_FORMATS = ("sdf", "mol2", "txt")

# Documented parameter ranges, enforced client-side.
_RANGES: dict[str, tuple[int, int]] = {
    "num_poses": (1, 100),
    "time_divisions": (3, 20),
    "steps": (1, 18),
}

# Response keys the NIM examples use. Recorded so a change is visible.
_EXPECTED_KEYS = ("ligand_positions", "position_confidence", "status")


@dataclass
class DockedPose:
    """One predicted pose and its confidence.

    ``confidence`` is DiffDock's own score: roughly a logit of the probability
    that this pose is within 2 Å RMSD of the true pose. It is comparable
    between poses of the *same* ligand against the *same* protein, and should
    not be used to rank different ligands against each other.
    """

    rank: int
    structure: str
    structure_format: str
    confidence: float | None
    interpretation: str = "candidate starting geometry"
    evidence: str = EVIDENCE_COMPUTED

    def save(self, directory: str | Path, stem: str) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}-pose{self.rank}.{self.structure_format}"
        path.write_text(self.structure, encoding="utf-8")
        return path

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "confidence": self.confidence,
            "format": self.structure_format,
            "n_bytes": len(self.structure.encode("utf-8")),
            "interpretation": self.interpretation,
            "evidence": self.evidence,
        }


@dataclass
class DockingResult:
    """Poses from one DiffDock call, with the caveats that qualify them."""

    poses: list[DockedPose]
    status: str
    request_summary: dict[str, Any]
    manifest: RunManifest
    cached: bool = False
    trajectory: Any | None = None
    observed_response_keys: list[str] = field(default_factory=list)
    schema_mismatch: list[str] = field(default_factory=list)

    # Oligomer provenance and the flexibility caveat.
    oligomer: dict[str, Any] | None = None
    n_rotatable_bonds: int | None = None

    @property
    def best(self) -> DockedPose | None:
        """Highest-confidence pose, or the first when no scores came back."""
        scored = [p for p in self.poses if p.confidence is not None]
        if scored:
            return max(scored, key=lambda p: p.confidence or -math.inf)
        return self.poses[0] if self.poses else None

    @property
    def flexibility_caveat(self) -> str | None:
        if self.n_rotatable_bonds is None:
            return None
        if self.n_rotatable_bonds <= 10:
            return None
        return (
            f"the submitted oligomer has {self.n_rotatable_bonds} rotatable bonds; "
            "DiffDock was trained on drug-like ligands with roughly ten or fewer, so "
            "pose accuracy here is expected to be materially worse than the "
            "published benchmarks"
        )

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "n_poses": len(self.poses),
            "best_confidence": self.best.confidence if self.best else None,
            "poses": [p.to_dict() for p in self.poses],
            "n_rotatable_bonds": self.n_rotatable_bonds,
            "flexibility_caveat": self.flexibility_caveat,
            "oligomer": self.oligomer,
            "observed_response_keys": self.observed_response_keys,
            "schema_mismatch": self.schema_mismatch,
            "cached": self.cached,
            "interpretation": (
                "Poses are candidate starting geometries for simulation. The "
                "confidence score estimates pose correctness, not binding strength, "
                "and is not comparable across different ligands."
            ),
        }


def diffdock_availability(settings: Settings | None = None) -> dict[str, Any]:
    """Whether DiffDock can be called, without docking anything."""
    s = settings or get_settings()
    creds = credential_state(s.diffdock_base_url, s.nvidia_api_key)
    health = probe_health(s.diffdock_base_url, s.nvidia_api_key)

    reason = None
    if not creds["satisfied"]:
        reason = missing_credential_reason("DiffDock")
    elif health["probed"] and not health["reachable"]:
        reason = (
            f"DiffDock health check at {health.get('url')} failed ({health['detail']}). "
            "Start the container with: docker run --rm --gpus all -p 8001:8000 "
            "nvcr.io/nim/mit/diffdock:2.1.0"
        )

    return {
        "adapter": "bionemo-diffdock",
        "available": reason is None,
        "reason": reason,
        "endpoint": join(s.diffdock_base_url, s.diffdock_path),
        "model": s.diffdock_model,
        "licence": DIFFDOCK_LICENCE,
        "health": health,
        **{k: creds[k] for k in ("requires_api_key", "api_key_present")},
        "limits": {
            "num_poses": list(_RANGES["num_poses"]),
            "time_divisions": list(_RANGES["time_divisions"]),
            "steps": list(_RANGES["steps"]),
            "ligand_formats": list(LIGAND_FORMATS),
        },
        "api_contract_verified": "request only",
        "response_schema_note": (
            "NVIDIA's public reference documents the request body fully but not the "
            "200 response body, which it defers to the running service's /docs. The "
            "adapter parses the field names used in the NIM examples and records the "
            "keys it actually received."
        ),
        "code_path_executed_in_this_build": False,
        "verification_note": (
            "This adapter has not been executed against a live DiffDock endpoint in "
            "this repository's development environment. Request construction and "
            "response parsing are covered by tests against recorded fixtures."
        ),
    }


class DiffDockAdapter:
    """Client for the DiffDock NIM pose-generation endpoint."""

    name = "bionemo-diffdock"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        cache: JsonCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        self.cache = cache if cache is not None else JsonCache("diffdock")

    @property
    def endpoint(self) -> str:
        return join(self.settings.diffdock_base_url, self.settings.diffdock_path)

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.settings.nvidia_api_key)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0))
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> DiffDockAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def generate(
        self,
        protein: str,
        ligand: str,
        *,
        ligand_file_type: str = "txt",
        num_poses: int = 10,
        time_divisions: int = 20,
        steps: int = 18,
        save_trajectory: bool = False,
        skip_gen_conformer: bool = False,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Call the pose-generation endpoint once and return the parsed body."""
        availability = diffdock_availability(self.settings)
        if not availability["available"]:
            raise AdapterUnavailable(
                self.name,
                availability["reason"] or "not configured",
                remedy="set NVIDIA_API_KEY or point BIOLOGIX_DIFFDOCK_BASE_URL at a "
                "self-hosted diffdock NIM container",
            )

        if ligand_file_type not in LIGAND_FORMATS:
            raise ValueError(
                f"ligand_file_type must be one of {LIGAND_FORMATS}, got {ligand_file_type!r}"
            )
        if not protein.strip():
            raise ValueError("protein must be non-empty PDB text")
        if not ligand.strip():
            raise ValueError("ligand must be non-empty")
        if ligand_file_type == "txt" and skip_gen_conformer:
            raise ValueError(
                "skip_gen_conformer=True requires 3D ligand coordinates, but "
                "ligand_file_type='txt' supplies SMILES with no coordinates. Pass "
                "an SDF, or leave skip_gen_conformer False so DiffDock builds the "
                "conformer."
            )

        request: dict[str, Any] = {
            "protein": protein,
            "ligand": ligand,
            "ligand_file_type": ligand_file_type,
            "num_poses": int(num_poses),
            "time_divisions": int(time_divisions),
            "steps": int(steps),
            "save_trajectory": bool(save_trajectory),
            "skip_gen_conformer": bool(skip_gen_conformer),
            "is_staged": False,
        }
        for field_name, (low, high) in _RANGES.items():
            value = request[field_name]
            if not low <= value <= high:
                raise ValueError(
                    f"{field_name}={value} is outside the documented range "
                    f"[{low}, {high}] for DiffDock {self.settings.diffdock_model}"
                )

        # The protein text can be megabytes, so the cache key hashes it rather
        # than embedding it.
        key = self.cache.key_for(
            model=self.settings.diffdock_model,
            version=self.settings.diffdock_model,
            payload={
                **{k: v for k, v in request.items() if k not in ("protein", "ligand")},
                "protein_sha": stable_hash(protein, length=32),
                "ligand_sha": stable_hash(ligand, length=32),
            },
        )
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                cached["_cache_hit"] = True
                return cached

        try:
            response = self._get_client().post(
                self.endpoint, json=request, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise AdapterCallFailed(self.name, f"transport error: {exc!r}") from exc

        if response.status_code >= 400:
            raise AdapterCallFailed(
                self.name,
                f"HTTP {response.status_code}: {response.text[:400]}",
                status=response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise AdapterCallFailed(
                self.name, f"response was not JSON: {response.text[:200]}"
            ) from exc
        if not isinstance(body, dict):
            raise AdapterCallFailed(
                self.name, f"response was {type(body).__name__}, expected an object"
            )

        status = str(body.get("status", "")).lower()
        if status in ("failed", "error"):
            raise AdapterCallFailed(
                self.name, f"service reported failure: {body.get('message') or body.get('status')}"
            )

        if use_cache:
            self.cache.put(key, body)
        body["_cache_hit"] = False
        return body


def _parse_poses(body: dict[str, Any]) -> tuple[list[DockedPose], list[str]]:
    """Read poses out of a response, recording anything unexpected."""
    mismatch: list[str] = []
    for expected in _EXPECTED_KEYS:
        if expected not in body:
            mismatch.append(f"expected key {expected!r} absent from the response")

    positions = body.get("ligand_positions")
    if positions is None:
        # Tolerate the alternative spelling seen in some examples.
        positions = body.get("poses")
        if positions is not None:
            mismatch.append("poses were under 'poses' rather than 'ligand_positions'")
    if isinstance(positions, str):
        positions = [positions]
    if not isinstance(positions, list):
        return [], mismatch + [
            f"ligand positions were {type(positions).__name__}, expected a list"
        ]

    raw_confidence = body.get("position_confidence")
    if isinstance(raw_confidence, int | float):
        raw_confidence = [raw_confidence]
    confidences: list[float | None] = []
    if isinstance(raw_confidence, list):
        for c in raw_confidence:
            try:
                value = float(c)
            except (TypeError, ValueError):
                confidences.append(None)
                continue
            confidences.append(value if math.isfinite(value) else None)
    elif raw_confidence is not None:
        mismatch.append(
            f"position_confidence was {type(raw_confidence).__name__}, expected a list"
        )

    if confidences and len(confidences) != len(positions):
        mismatch.append(
            f"{len(positions)} poses but {len(confidences)} confidence values; "
            "pairing by index and leaving the remainder unscored"
        )

    poses: list[DockedPose] = []
    for i, structure in enumerate(positions):
        if not isinstance(structure, str) or not structure.strip():
            mismatch.append(f"pose {i} was not a non-empty string; skipped")
            continue
        poses.append(
            DockedPose(
                rank=len(poses) + 1,
                structure=structure,
                # DiffDock returns SDF-formatted ligand poses.
                structure_format="sdf",
                confidence=confidences[i] if i < len(confidences) else None,
            )
        )
    # Highest confidence first, keeping unscored poses in their original order
    # at the end rather than treating a missing score as a low one.
    scored = [p for p in poses if p.confidence is not None]
    unscored = [p for p in poses if p.confidence is None]
    scored.sort(key=lambda p: p.confidence or -math.inf, reverse=True)
    for rank, pose in enumerate(scored + unscored, start=1):
        pose.rank = rank
    return scored + unscored, mismatch


def _rotatable_bonds(smiles: str) -> int | None:
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return int(Descriptors.NumRotatableBonds(mol))


def dock_candidate(
    protein_pdb: str | Path,
    candidate: PolymerCandidate,
    *,
    adapter: DiffDockAdapter | None = None,
    settings: Settings | None = None,
    dp_override: int | None = 10,
    num_poses: int = 10,
    steps: int = 18,
    time_divisions: int = 20,
    use_cache: bool = True,
) -> DockingResult:
    """Dock a polymer candidate's oligomer against a prepared structure.

    ``protein_pdb`` is a path to a prepared PDB file (see
    :mod:`biologix.targets.prepare`) or the PDB text itself. The candidate is
    reduced to one explicit capped oligomer first, for the same reason as in
    :mod:`biologix.nvidia.boltz2`: a polymer specification is not a molecule.
    """
    s = settings or get_settings()
    own_adapter = adapter is None
    adapter = adapter or DiffDockAdapter(s)

    protein_digest: str | None = None
    if isinstance(protein_pdb, Path) or (
        isinstance(protein_pdb, str) and "\n" not in protein_pdb and len(protein_pdb) < 260
    ):
        path = Path(protein_pdb)
        if not path.exists():
            raise FileNotFoundError(f"no structure at {path}")
        protein_text = path.read_text(encoding="utf-8")
        protein_digest = file_digest(path)
        protein_ref: str = str(path)
    else:
        protein_text = str(protein_pdb)
        protein_digest = stable_hash(protein_text, length=64)
        protein_ref = "<inline PDB text>"

    try:
        build: OligomerBuild = build_oligomer(
            candidate.model_copy(
                update={
                    "chain_length": candidate.chain_length.model_copy(
                        update={"degree_of_polymerisation": dp_override}
                    )
                }
            )
            if dp_override is not None
            else candidate
        )
    except (ValueError, RuntimeError) as exc:
        raise AdapterCallFailed(
            DiffDockAdapter.name, f"could not build an oligomer to dock: {exc}"
        ) from exc

    n_rot = _rotatable_bonds(build.smiles)

    manifest = RunManifest(
        operation="diffdock-dock-candidate",
        evidence=EVIDENCE_COMPUTED,
        inputs={
            "protein": protein_ref,
            "protein_sha256": protein_digest,
            "candidate_id": candidate.candidate_id,
            "candidate": candidate.display_name(),
        },
        parameters={
            "dp_override": dp_override,
            "requested_dp": candidate.chain_length.degree_of_polymerisation,
            "num_poses": num_poses,
            "steps": steps,
            "time_divisions": time_divisions,
            "ligand_file_type": "txt",
            "endpoint": adapter.endpoint,
        },
        requested_backend="nvidia-nim-diffdock",
        actual_backend="nvidia-nim-diffdock",
    )
    manifest.record_model(
        s.diffdock_model,
        s.diffdock_model,
        kind="pose-predictor",
        endpoint=adapter.endpoint,
        licence=DIFFDOCK_LICENCE,
    )

    try:
        body = adapter.generate(
            protein_text,
            build.smiles,
            ligand_file_type="txt",
            num_poses=num_poses,
            steps=steps,
            time_divisions=time_divisions,
            use_cache=use_cache,
        )
    finally:
        if own_adapter:
            adapter.close()

    poses, mismatch = _parse_poses(body)
    oligomer_provenance = build.provenance()
    if dp_override is not None:
        oligomer_provenance["dp_override_reason"] = (
            f"docked as DP={dp_override} rather than the candidate's requested "
            f"DP={candidate.chain_length.degree_of_polymerisation}: DiffDock places a "
            "single ligand molecule, and pose quality falls off sharply with ligand "
            "flexibility"
        )

    for note in mismatch:
        manifest.warn(f"response schema: {note}")
    if not poses:
        manifest.warn("no poses were parsed from the response")

    result = DockingResult(
        poses=poses,
        status=str(body.get("status", "unknown")),
        request_summary={
            "num_poses": num_poses,
            "steps": steps,
            "time_divisions": time_divisions,
            "ligand_smiles": build.smiles,
        },
        manifest=manifest,
        cached=bool(body.get("_cache_hit")),
        trajectory=body.get("trajectory"),
        observed_response_keys=sorted(k for k in body if not k.startswith("_")),
        schema_mismatch=mismatch,
        oligomer=oligomer_provenance,
        n_rotatable_bonds=n_rot,
    )
    if result.flexibility_caveat:
        manifest.warn(result.flexibility_caveat)

    manifest.finish(
        n_poses=len(poses),
        best_confidence=result.best.confidence if result.best else None,
        oligomer=oligomer_provenance,
        n_rotatable_bonds=n_rot,
        cached=result.cached,
    )
    return result


__all__ = [
    "DIFFDOCK_LICENCE",
    "LIGAND_FORMATS",
    "DiffDockAdapter",
    "DockedPose",
    "DockingResult",
    "diffdock_availability",
    "dock_candidate",
]

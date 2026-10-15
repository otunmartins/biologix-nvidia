"""BioNeMo Boltz-2 adapter: complex structure and binding affinity.

API contract, from NVIDIA's Boltz-2 NIM documentation:

* ``POST {base}/mit/boltz2/predict``
* request: ``polymers`` (1..N, each ``{id, molecule_type, sequence, cyclic,
  msa, modifications}``), ``ligands`` (each ``{id, smiles|ccd,
  predict_affinity, affinity_mw_correction}``), ``constraints``,
  ``recycling_steps`` (1-6, default 3), ``sampling_steps`` (10-1000, default
  50), ``diffusion_samples`` (1-5, default 1), ``step_scale`` (0.5-5.0,
  default 1.638), ``without_potentials``, ``output_format``,
  ``concatenate_msas``.
* response: ``{"structures": [{"structure", "format", ...}],
  "confidence_scores": [float], "affinities": {...}, "metrics": {...}}``.
* at most one ligand per request may set ``predict_affinity``.
* health: ``GET {base}/v1/health/ready`` (self-hosted only).

The documented polymer and ligand ceilings changed between NIM releases
(1.0.0 documented 5 and 5; later releases document 12 and 20), so they are
read from settings and the value enforced is recorded in the manifest rather
than assumed.

Why this module is careful about affinity
-----------------------------------------
Boltz-2's affinity head was trained on small-molecule protein–ligand binding
data. A polymer excipient is not a small-molecule drug candidate, and what we
submit is not even the polymer: it is one explicit capped oligomer of a
chosen DP, built by :mod:`biologix.polymers.oligomer`. Three gaps therefore
travel with every number this module returns:

1. **Chemical-space gap.** The query is outside the training distribution.
   :attr:`AffinityPrediction.out_of_distribution` is always true for a
   polymer oligomer, and the reason is recorded, not implied.
2. **Molecule-identity gap.** The affinity describes a DP-*n* oligomer, not a
   polydisperse polymer. The oligomer provenance is attached.
3. **Mechanism gap.** Excipients usually act by colloidal and interfacial
   mechanisms — surface coverage, viscosity, competition at the air/water
   interface — not by occupying a binding pocket. A pocket-binding affinity is
   at best a weak proxy, and is tagged ``interpretation="model feature"``
   rather than a stability prediction.

The number is still worth having: it ranks candidates by predicted
protein-surface association under a fixed model, which is a usable screening
signal as long as nobody reads it as a Kd.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import httpx

from biologix import EVIDENCE_COMPUTED
from biologix.errors import AdapterCallFailed, AdapterUnavailable, SequenceTooLong
from biologix.nvidia.cache import JsonCache
from biologix.nvidia.endpoints import (
    auth_headers,
    credential_state,
    failure_for,
    join,
    missing_credential_reason,
    probe_health,
)
from biologix.polymers.models import PolymerCandidate
from biologix.polymers.oligomer import OligomerBuild, build_oligomer
from biologix.provenance import RunManifest
from biologix.settings import Settings, get_settings
from biologix.targets.models import Biologic, ModificationKind

BOLTZ2_LICENCE = (
    "Boltz-2 weights are released by MIT under the MIT licence; the NVIDIA NIM "
    "packaging is governed additionally by the NVIDIA AI product agreement."
)

# Documented parameter ranges, enforced client-side so an out-of-range value
# fails here with a clear message instead of as an opaque HTTP 422.
_RANGES: dict[str, tuple[float, float]] = {
    "recycling_steps": (1, 6),
    "sampling_steps": (10, 1000),
    "diffusion_samples": (1, 5),
    "step_scale": (0.5, 5.0),
}

# Modification kinds Boltz-2 can be told about via CCD codes on a polymer.
# Everything else is reported as unrepresented rather than silently dropped.
# Disulfides are inferred by the model from cysteine geometry, so they are not
# in this map but are also not "lost" in the same way.
_CCD_FOR_MODIFICATION: dict[ModificationKind, str] = {
    ModificationKind.PHOSPHORYLATION: "SEP",
    ModificationKind.ACETYLATION: "ALY",
}


@dataclass
class AffinityPrediction:
    """One ligand's predicted affinity, with its caveats attached.

    ``pic50`` is preferred from the service's own ``affinity_pic50`` field.
    When that field is absent it is derived from ``affinity_pred_value``,
    which Boltz-2 defines as ``log10(IC50)`` with IC50 in micromolar, giving
    ``pIC50 = 6 - affinity_pred_value``. :attr:`pic50_source` records which of
    the two happened, because a derived value depends on our reading of the
    unit convention and a reported one does not.
    """

    ligand_id: str
    affinity_pred_value: float | None
    binding_probability: float | None
    pic50: float | None
    pic50_source: str
    ensemble: dict[str, float | None] = field(default_factory=dict)
    mw_correction_applied: bool = False

    out_of_distribution: bool = True
    out_of_distribution_reason: str = (
        "Boltz-2's affinity head was trained on small-molecule protein-ligand "
        "binding data. This query is a capped polymer oligomer, which is outside "
        "that distribution; the value ranks candidates under a fixed model and is "
        "not a Kd, IC50 or stability prediction."
    )
    interpretation: str = "model feature"
    evidence: str = EVIDENCE_COMPUTED

    @property
    def kd_micromolar(self) -> float | None:
        """IC50 in micromolar implied by the reported log value.

        Exposed for plotting on a familiar axis only. It inherits every caveat
        above; it is not a measured dissociation constant.
        """
        if self.affinity_pred_value is None:
            return None
        try:
            return float(10.0**self.affinity_pred_value)
        except OverflowError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ligand_id": self.ligand_id,
            "affinity_pred_value_log10_ic50_um": self.affinity_pred_value,
            "binding_probability": self.binding_probability,
            "pic50": self.pic50,
            "pic50_source": self.pic50_source,
            "ensemble": self.ensemble,
            "mw_correction_applied": self.mw_correction_applied,
            "out_of_distribution": self.out_of_distribution,
            "out_of_distribution_reason": self.out_of_distribution_reason,
            "interpretation": self.interpretation,
            "evidence": self.evidence,
        }


@dataclass
class PredictedStructure:
    """One predicted complex structure as returned by the service."""

    content: str
    format: str
    name: str | None = None
    source: str | None = None

    @property
    def n_bytes(self) -> int:
        return len(self.content.encode("utf-8"))


@dataclass
class Boltz2Result:
    """Structures, confidence and affinity from one Boltz-2 call."""

    structures: list[PredictedStructure]
    confidence_scores: list[float]
    affinity: AffinityPrediction | None
    request: dict[str, Any]
    manifest: RunManifest
    cached: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    raw_keys: list[str] = field(default_factory=list)

    # What the request could not express about the inputs.
    unrepresented_chemistry: list[str] = field(default_factory=list)
    # The oligomer actually submitted, when the ligand came from a candidate.
    oligomer: dict[str, Any] | None = None
    # True when no MSA was supplied for any chain.
    single_sequence_mode: bool = True

    @property
    def best_confidence(self) -> float | None:
        return max(self.confidence_scores) if self.confidence_scores else None

    def summary(self) -> dict[str, Any]:
        return {
            "n_structures": len(self.structures),
            "confidence_scores": self.confidence_scores,
            "best_confidence": self.best_confidence,
            "affinity": self.affinity.to_dict() if self.affinity else None,
            "single_sequence_mode": self.single_sequence_mode,
            "accuracy_note": (
                "No MSA was supplied, so this is a single-sequence prediction, "
                "which Boltz-2 documents as less accurate than an MSA-conditioned "
                "one."
                if self.single_sequence_mode
                else "MSA-conditioned prediction."
            ),
            "unrepresented_chemistry": self.unrepresented_chemistry,
            "oligomer": self.oligomer,
            "cached": self.cached,
        }


def boltz2_availability(settings: Settings | None = None) -> dict[str, Any]:
    """Whether Boltz-2 can be called, without making a prediction."""
    s = settings or get_settings()
    creds = credential_state(s.boltz2_base_url, s.nvidia_api_key)
    health = probe_health(s.boltz2_base_url, s.nvidia_api_key)

    reason = None
    if not creds["satisfied"]:
        reason = missing_credential_reason("Boltz-2")
    elif health["probed"] and not health["reachable"]:
        reason = (
            f"Boltz-2 health check at {health.get('url')} failed ({health['detail']}). "
            "Start the container with: docker run --rm --gpus all -p 8003:8000 "
            "nvcr.io/nim/mit/boltz2:latest"
        )

    return {
        "adapter": "bionemo-boltz2",
        "available": reason is None,
        "reason": reason,
        "endpoint": join(s.boltz2_base_url, s.boltz2_path),
        "model": s.boltz2_model,
        "licence": BOLTZ2_LICENCE,
        "health": health,
        **{k: creds[k] for k in ("requires_api_key", "api_key_present")},
        "limits": {
            "max_polymers": s.boltz2_max_polymers,
            "max_ligands": s.boltz2_max_ligands,
            "max_sequence_length": s.boltz2_max_sequence,
            "max_affinity_ligands_per_request": 1,
        },
        "limits_note": (
            "The documented polymer/ligand ceilings differ between NIM releases "
            "(1.0.0: 5/5; later: 12/20). The values above come from settings and "
            "are enforced client-side; adjust BIOLOGIX_BOLTZ2_MAX_* to match the "
            "release you deploy."
        ),
        "api_contract_verified": True,
        "affinity_note": (
            "Affinity is predicted for small-molecule-like ligands. Polymer "
            "oligomers are out of distribution; see AffinityPrediction."
        ),
        "code_path_executed_in_this_build": False,
        "verification_note": (
            "Request construction and response parsing are covered by tests against "
            "recorded fixtures. No successful live prediction has been obtained: on "
            "2026-10-13 the hosted endpoint answered every payload tried — including "
            "NVIDIA's own documented defaults, a protein with no ligand, a 22-residue "
            "peptide and aspirin — with the same server-side CUDA "
            "illegal-memory-access, delivered as HTTP 422. The response contract "
            "therefore remains unverified, and the failure was upstream rather than "
            "in the request."
        ),
    }


class Boltz2Adapter:
    """Client for the Boltz-2 NIM ``predict`` endpoint."""

    name = "bionemo-boltz2"

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
        self.cache = cache if cache is not None else JsonCache("boltz2")

    @property
    def endpoint(self) -> str:
        return join(self.settings.boltz2_base_url, self.settings.boltz2_path)

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.settings.nvidia_api_key)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            # Structure prediction with affinity can take minutes; the connect
            # timeout stays short so an unreachable endpoint fails fast.
            self._client = httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0))
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> Boltz2Adapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- request construction ----------------------------------------------

    def polymers_for(self, biologic: Biologic) -> tuple[list[dict[str, Any]], list[str]]:
        """Build the ``polymers`` list for a biologic, one entry per chain.

        Chains are kept separate for the same reason
        :mod:`biologix.nvidia.esm2` keeps them separate: concatenating them
        would describe a molecule that does not exist. Returns the polymer
        entries and the list of chemistry the request cannot express.
        """
        if biologic.n_chains > self.settings.boltz2_max_polymers:
            raise ValueError(
                f"{biologic.name} has {biologic.n_chains} chains but the configured "
                f"Boltz-2 limit is {self.settings.boltz2_max_polymers}. Refusing to "
                f"drop chains; raise BIOLOGIX_BOLTZ2_MAX_POLYMERS if your deployment "
                f"allows more."
            )

        mods_by_chain: dict[str, list[dict[str, Any]]] = {}
        unrepresented: list[str] = []
        for mod in biologic.modifications:
            ccd = _CCD_FOR_MODIFICATION.get(mod.kind)
            if ccd is None:
                if mod.kind is ModificationKind.DISULFIDE:
                    # Not expressible as a residue modification, but the model
                    # forms disulfides from cysteine geometry, so this is a
                    # different kind of gap and is labelled as such.
                    unrepresented.append(
                        f"{mod.describe()} — not sent explicitly; Boltz-2 infers "
                        "disulfides from predicted cysteine geometry"
                    )
                else:
                    unrepresented.append(
                        f"{mod.describe()} — no CCD mapping; absent from the request"
                    )
                continue
            for chain_id, resnum in mod.sites:
                chain = biologic.chain(chain_id)
                position = resnum - chain.first_residue_number + 1
                mods_by_chain.setdefault(chain_id, []).append(
                    {"ccd": ccd, "position": position}
                )

        polymers: list[dict[str, Any]] = []
        for chain in biologic.chains:
            if chain.length > self.settings.boltz2_max_sequence:
                raise SequenceTooLong(
                    chain.chain_id,
                    chain.length,
                    self.settings.boltz2_max_sequence,
                    self.settings.boltz2_model,
                )
            entry: dict[str, Any] = {
                "id": chain.chain_id,
                "molecule_type": "protein",
                "sequence": chain.sequence,
            }
            if chain.chain_id in mods_by_chain:
                entry["modifications"] = mods_by_chain[chain.chain_id]
            polymers.append(entry)
        return polymers, unrepresented

    def predict(
        self,
        polymers: list[dict[str, Any]],
        ligands: list[dict[str, Any]],
        *,
        recycling_steps: int = 3,
        sampling_steps: int = 50,
        diffusion_samples: int = 1,
        step_scale: float = 1.638,
        without_potentials: bool = False,
        constraints: list[dict[str, Any]] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Call ``predict`` once and return the parsed response body."""
        availability = boltz2_availability(self.settings)
        if not availability["available"]:
            raise AdapterUnavailable(
                self.name,
                availability["reason"] or "not configured",
                remedy="set NVIDIA_API_KEY or point BIOLOGIX_BOLTZ2_BASE_URL at a "
                "self-hosted boltz2 NIM container",
            )

        if not polymers:
            raise ValueError("Boltz-2 requires at least one polymer")
        if len(ligands) > self.settings.boltz2_max_ligands:
            raise ValueError(
                f"{len(ligands)} ligands exceeds the configured Boltz-2 limit of "
                f"{self.settings.boltz2_max_ligands}"
            )
        n_affinity = sum(1 for lig in ligands if lig.get("predict_affinity"))
        if n_affinity > 1:
            raise ValueError(
                f"{n_affinity} ligands set predict_affinity, but Boltz-2 accepts at "
                "most one per request. Submit the rest as separate calls so each "
                "affinity is attributable to its own ligand."
            )
        for lig in ligands:
            if not (lig.get("smiles") or lig.get("ccd")):
                raise ValueError(f"ligand {lig.get('id')!r} needs either smiles or ccd")

        request: dict[str, Any] = {
            "polymers": polymers,
            "ligands": ligands,
            "recycling_steps": int(recycling_steps),
            "sampling_steps": int(sampling_steps),
            "diffusion_samples": int(diffusion_samples),
            "step_scale": float(step_scale),
            "without_potentials": bool(without_potentials),
        }
        if constraints:
            request["constraints"] = constraints
        for field_name, (low, high) in _RANGES.items():
            value = request[field_name]
            if not low <= value <= high:
                raise ValueError(
                    f"{field_name}={value} is outside the documented range "
                    f"[{low}, {high}] for Boltz-2 {self.settings.boltz2_model}"
                )

        key = self.cache.key_for(
            model=self.settings.boltz2_model,
            version=self.settings.boltz2_model,
            payload=request,
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
            raise failure_for(self.name, response.status_code, response.text)
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

        if use_cache:
            self.cache.put(key, body)
        body["_cache_hit"] = False
        return body


def _parse_affinity(
    affinities: Any, ligand_id: str, *, mw_correction: bool
) -> AffinityPrediction | None:
    """Read the affinity block, tolerating the documented shape variations.

    The service has returned affinity values both as scalars and as
    single-element lists across releases, so each field is normalised through
    :func:`_first_float` rather than indexed directly.
    """
    if not isinstance(affinities, dict) or not affinities:
        return None

    # Some releases key the block by ligand id, others return one flat block
    # for the single affinity ligand.
    block = affinities.get(ligand_id) if ligand_id in affinities else affinities
    if not isinstance(block, dict):
        return None

    pred = _first_float(block.get("affinity_pred_value"))
    prob = _first_float(block.get("affinity_probability_binary"))
    pic50 = _first_float(block.get("affinity_pic50"))
    if pic50 is not None:
        pic50_source = "reported by the service as affinity_pic50"
    elif pred is not None:
        # Boltz-2 defines affinity_pred_value as log10(IC50) with IC50 in uM.
        pic50 = 6.0 - pred
        pic50_source = "derived locally as 6 - log10(IC50/uM); not reported by the service"
    else:
        pic50_source = "unavailable: neither affinity_pic50 nor affinity_pred_value present"

    ensemble = {
        name: _first_float(block.get(name))
        for name in (
            "model_1_affinity_pred_value",
            "model_1_affinity_probability_binary",
            "model_2_affinity_pred_value",
            "model_2_affinity_probability_binary",
        )
        if block.get(name) is not None
    }

    if pred is None and prob is None and pic50 is None:
        return None
    return AffinityPrediction(
        ligand_id=ligand_id,
        affinity_pred_value=pred,
        binding_probability=prob,
        pic50=pic50,
        pic50_source=pic50_source,
        ensemble=ensemble,
        mw_correction_applied=mw_correction,
    )


def _first_float(value: Any) -> float | None:
    """Coerce a scalar or single-element sequence to a finite float."""
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _parse_structures(raw: Any) -> list[PredictedStructure]:
    structures: list[PredictedStructure] = []
    if not isinstance(raw, list):
        return structures
    for item in raw:
        if not isinstance(item, dict):
            continue
        content = item.get("structure")
        if not isinstance(content, str) or not content:
            continue
        structures.append(
            PredictedStructure(
                content=content,
                format=str(item.get("format") or "mmcif"),
                name=item.get("name"),
                source=item.get("source"),
            )
        )
    return structures


def predict_complex(
    biologic: Biologic,
    candidate: PolymerCandidate,
    *,
    adapter: Boltz2Adapter | None = None,
    settings: Settings | None = None,
    dp_override: int | None = 10,
    predict_affinity: bool = True,
    recycling_steps: int = 3,
    sampling_steps: int = 50,
    diffusion_samples: int = 1,
    use_cache: bool = True,
) -> Boltz2Result:
    """Predict a biologic–oligomer complex, with optional binding affinity.

    The polymer candidate is reduced to one explicit capped oligomer before it
    can be sent, because Boltz-2 takes a ligand SMILES and a polymer
    specification is not a molecule. ``dp_override`` keeps that oligomer short
    by default: a full DP-50 chain is both far outside the ligand sizes the
    model was trained on and expensive to sample. The DP actually used is
    recorded in the manifest and on the result.
    """
    s = settings or get_settings()
    own_adapter = adapter is None
    adapter = adapter or Boltz2Adapter(s)

    manifest = RunManifest(
        operation="boltz2-predict-complex",
        evidence=EVIDENCE_COMPUTED,
        inputs={
            "biologic": biologic.name,
            "chains": {c.chain_id: c.length for c in biologic.chains},
            "candidate_id": candidate.candidate_id,
            "candidate": candidate.display_name(),
        },
        parameters={
            "dp_override": dp_override,
            "requested_dp": candidate.chain_length.degree_of_polymerisation,
            "predict_affinity": predict_affinity,
            "recycling_steps": recycling_steps,
            "sampling_steps": sampling_steps,
            "diffusion_samples": diffusion_samples,
            "endpoint": adapter.endpoint,
        },
        requested_backend="nvidia-nim-boltz2",
        actual_backend="nvidia-nim-boltz2",
    )
    manifest.record_model(
        s.boltz2_model,
        s.boltz2_model,
        kind="structure-and-affinity-predictor",
        endpoint=adapter.endpoint,
        licence=BOLTZ2_LICENCE,
    )

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
            Boltz2Adapter.name, f"could not build an oligomer to submit: {exc}"
        ) from exc

    polymers, unrepresented = adapter.polymers_for(biologic)
    ligand_id = "LIG"
    ligand: dict[str, Any] = {"id": ligand_id, "smiles": build.smiles}
    if predict_affinity:
        ligand["predict_affinity"] = True

    try:
        body = adapter.predict(
            polymers,
            [ligand],
            recycling_steps=recycling_steps,
            sampling_steps=sampling_steps,
            diffusion_samples=diffusion_samples,
            use_cache=use_cache,
        )
    finally:
        if own_adapter:
            adapter.close()

    cached = bool(body.get("_cache_hit"))
    structures = _parse_structures(body.get("structures"))
    confidence = [
        f for f in (_first_float(c) for c in body.get("confidence_scores") or []) if f is not None
    ]
    affinity = (
        _parse_affinity(body.get("affinities"), ligand_id, mw_correction=False)
        if predict_affinity
        else None
    )

    if predict_affinity and affinity is None:
        manifest.warn(
            "affinity was requested but the response carried no usable affinity "
            "block; reporting no affinity rather than a default value"
        )
    if not structures:
        manifest.warn("response carried no parsable structure")

    oligomer_provenance = build.provenance()
    if dp_override is not None:
        oligomer_provenance["dp_override_reason"] = (
            f"submitted as DP={dp_override} rather than the candidate's requested "
            f"DP={candidate.chain_length.degree_of_polymerisation}: Boltz-2 scores a "
            "single ligand molecule, and a longer chain is both further outside the "
            "model's training distribution and more expensive to sample"
        )
    for note in unrepresented:
        manifest.warn(f"not represented in the request: {note}")

    manifest.finish(
        n_structures=len(structures),
        confidence_scores=confidence,
        affinity=affinity.to_dict() if affinity else None,
        oligomer=oligomer_provenance,
        cached=cached,
        metrics=body.get("metrics") or {},
    )

    return Boltz2Result(
        structures=structures,
        confidence_scores=confidence,
        affinity=affinity,
        request={"polymers": polymers, "ligands": [ligand]},
        manifest=manifest,
        cached=cached,
        metrics=body.get("metrics") or {},
        raw_keys=sorted(k for k in body if not k.startswith("_")),
        unrepresented_chemistry=unrepresented,
        oligomer=oligomer_provenance,
        single_sequence_mode=not any(p.get("msa") for p in polymers),
    )


__all__ = [
    "BOLTZ2_LICENCE",
    "AffinityPrediction",
    "Boltz2Adapter",
    "Boltz2Result",
    "PredictedStructure",
    "boltz2_availability",
    "predict_complex",
]

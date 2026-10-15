"""BioNeMo MolMIM adapter: latent-space molecule sampling and optimisation.

API contract, from NVIDIA's MolMIM NIM documentation:

* ``POST {base}/generate`` — request: ``smi`` (seed SMILES), ``algorithm``
  (``CMA-ES`` | ``none``, default ``CMA-ES``), ``num_molecules`` (1-100,
  default 10), ``property_name`` (``QED`` | ``plogP``, default ``QED``),
  ``minimize`` (default false), ``min_similarity`` (0-0.7, default 0.7),
  ``particles`` (2-1000, default 30), ``iterations`` (1-1000, default 10),
  ``scaled_radius`` (0-2, default 1.0, random-sampling mode only).
  Response: ``{"generated": ["<smiles>", ...]}``. The **hosted** endpoint
  answers differently (observed live 2026-10-13): ``{"molecules": "<JSON
  string of [{"sample": smiles, "score": float}]>", "score_type": ...}``.
  Both are parsed.
* ``POST {base}/embedding`` — ``{"sequences": [smiles]}`` →
  ``{"embeddings": [[float]]}``.
* ``POST {base}/sampling`` — ``{"sequences", "beam_size" (1-10),
  "num_molecules" (1-10), "scaled_radius" (0-2)}`` →
  ``{"generated": [[smiles]]}``.
* ``POST {base}/hidden`` and ``POST {base}/decode`` round-trip the latent
  representation, which is what makes an external optimiser possible.

Why the default here is *not* CMA-ES on QED
-------------------------------------------
MolMIM's built-in objectives are QED and penalised logP. Both are
**oral-small-molecule drug-likeness** scores: QED is fitted to the property
distributions of marketed oral drugs, and plogP rewards lipophilicity minus
synthetic-accessibility and ring penalties. A polymer excipient is not an oral
drug and is not meant to cross a membrane — optimising a candidate excipient
for QED would select *against* the very features excipients need (high
molecular weight, repeated polar units, many hydrogen-bond donors and
acceptors).

So this adapter defaults to ``algorithm="none"``: similarity-constrained
random sampling in the latent neighbourhood of a seed, which is a genuine
diversification operator and makes no claim about what is "better". CMA-ES is
available but must be asked for explicitly, and when it is used the chosen
objective and its mismatch with excipient design are recorded as a warning in
the run manifest. :func:`property_objective_caveat` is the single place that
text lives.

As with GenMol, MolMIM returns *molecules*. A molecule is not a repeat unit:
every output goes through :mod:`biologix.polymers.monomer_to_repeat` and then
:mod:`biologix.polymers.rules`, and the yield that matters is
molecules-to-valid-repeat-units.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from biologix import EVIDENCE_HYPOTHESIS
from biologix.errors import AdapterCallFailed, AdapterUnavailable
from biologix.nvidia.cache import JsonCache
from biologix.nvidia.endpoints import (
    auth_headers,
    credential_state,
    join,
    missing_credential_reason,
    probe_health,
)
from biologix.polymers.derive import derivation_statistics, derive_repeat_units
from biologix.polymers.generate import GenerationRecord, GenerationResult
from biologix.polymers.models import (
    ChainLength,
    EndGroup,
    MonomerBlock,
    PolymerCandidate,
    RepeatUnit,
)
from biologix.polymers.oligomer import build_oligomer
from biologix.polymers.rules import PolymerizationRules, validate_repeat_unit
from biologix.provenance import RunManifest
from biologix.settings import Settings, get_settings

MOLMIM_LICENCE = (
    "NVIDIA AI Foundation Models Community License / NVIDIA AI product agreement; "
    "see the MolMIM NIM EULA. Generated structures are subject to those terms."
)

ALGORITHMS = ("CMA-ES", "none")
PROPERTIES = ("QED", "plogP")

# Documented parameter ranges, enforced client-side so an out-of-range value
# fails here with a clear message instead of as an opaque HTTP 422.
_RANGES: dict[str, tuple[float, float]] = {
    "num_molecules": (1, 100),
    "particles": (2, 1000),
    "iterations": (1, 1000),
    "min_similarity": (0.0, 0.7),
    "scaled_radius": (0.0, 2.0),
}

# Seed molecules whose latent neighbourhoods are worth sampling for excipient
# chemistry. These are the monomers of polymers with a real history of use in
# protein formulation, so "nearby in MolMIM's latent space" starts from
# somewhere relevant rather than from drug space.
EXCIPIENT_SEEDS: tuple[tuple[str, str], ...] = (
    ("N-vinylpyrrolidone (povidone)", "C=CN1CCCC1=O"),
    ("ethylene glycol (PEG repeat)", "OCCO"),
    ("vinyl alcohol (PVA repeat)", "C=CO"),
    ("2-ethyl-2-oxazoline (POx)", "CCC1=NCCO1"),
    ("acrylamide", "C=CC(=O)N"),
    ("hydroxyethyl methacrylate", "CC(=C)C(=O)OCCO"),
    ("glycerol", "OCC(O)CO"),
    ("trehalose-like diol fragment", "OCC1OC(O)C(O)C1O"),
)


def property_objective_caveat(property_name: str, minimize: bool) -> str:
    """The warning that must accompany any CMA-ES-optimised output."""
    direction = "minimised" if minimize else "maximised"
    what = {
        "QED": (
            "QED is a quantitative estimate of drug-likeness fitted to marketed "
            "oral drugs. It penalises high molecular weight and large numbers of "
            "hydrogen-bond donors and acceptors — properties excipient polymers "
            "generally need."
        ),
        "plogP": (
            "Penalised logP rewards lipophilicity less synthetic-accessibility and "
            "ring penalties. Excipients for aqueous biologic formulations are "
            "typically hydrophilic, so a higher plogP is usually the wrong "
            "direction."
        ),
    }.get(property_name, f"{property_name} is not a documented MolMIM objective.")
    return (
        f"candidates were generated by optimising {property_name} ({direction}). "
        f"{what} Treat the selection pressure as a diversification heuristic, not "
        f"as evidence of excipient suitability; nothing downstream should read "
        f"{property_name} as a formulation-performance score."
    )


@dataclass
class MolMIMResponse:
    """One raw MolMIM ``/generate`` call."""

    generated: list[str]
    request: dict[str, Any]
    cached: bool = False
    raw_keys: list[str] = field(default_factory=list)
    # Hosted endpoint only: one score per entry of ``generated`` (not of
    # ``smiles``, which drops blanks), and what the score measures.
    scores: list[float | None] = field(default_factory=list)
    score_type: str | None = None

    @property
    def smiles(self) -> list[str]:
        return [s for s in self.generated if isinstance(s, str) and s.strip()]


def molmim_availability(settings: Settings | None = None) -> dict[str, Any]:
    """Whether MolMIM can be reached, without generating anything."""
    s = settings or get_settings()
    creds = credential_state(s.molmim_base_url, s.nvidia_api_key)
    health = probe_health(s.molmim_base_url, s.nvidia_api_key)

    reason = None
    if not creds["satisfied"]:
        reason = missing_credential_reason("MolMIM")
    elif health["probed"] and not health["reachable"]:
        reason = (
            f"MolMIM health check at {health.get('url')} failed ({health['detail']}). "
            "Start the container with: docker run --rm --gpus all -p 8002:8000 "
            "nvcr.io/nim/nvidia/molmim:1.0.0"
        )

    return {
        "adapter": "bionemo-molmim",
        "available": reason is None,
        "reason": reason,
        "endpoint": join(s.molmim_base_url, s.molmim_path),
        "model": s.molmim_model,
        "licence": MOLMIM_LICENCE,
        "health": health,
        **{k: creds[k] for k in ("requires_api_key", "api_key_present")},
        "algorithms": list(ALGORITHMS),
        "properties": list(PROPERTIES),
        "default_algorithm": "none",
        "objective_note": (
            "MolMIM's built-in objectives (QED, plogP) are oral-drug-likeness "
            "scores and are a poor fit for excipient design. This adapter defaults "
            "to similarity-constrained sampling (algorithm='none'); CMA-ES must be "
            "requested explicitly and is recorded with a caveat."
        ),
        "api_contract_verified": True,
        "code_path_executed_in_this_build": False,
        "verification_note": (
            "This adapter has not been executed against a live MolMIM endpoint in "
            "this repository's development environment. Request construction and "
            "response parsing are covered by tests against recorded fixtures."
        ),
    }


class MolMIMAdapter:
    """Client for the MolMIM NIM endpoints."""

    name = "bionemo-molmim"

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
        self.cache = cache if cache is not None else JsonCache("molmim")

    @property
    def endpoint(self) -> str:
        return join(self.settings.molmim_base_url, self.settings.molmim_path)

    def _sibling_endpoint(self, name: str) -> str:
        """A sibling endpoint (``/embedding``, ``/sampling``) of ``/generate``.

        Derived by replacing the last path segment so that a self-hosted
        deployment on a non-standard prefix keeps working.
        """
        base = self.endpoint.rsplit("/", 1)[0]
        return f"{base}/{name}"

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.settings.nvidia_api_key)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> MolMIMAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _require_available(self) -> None:
        availability = molmim_availability(self.settings)
        if not availability["available"]:
            raise AdapterUnavailable(
                self.name,
                availability["reason"] or "not configured",
                remedy="set NVIDIA_API_KEY or point BIOLOGIX_MOLMIM_BASE_URL at a "
                "self-hosted molmim NIM container",
            )

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._get_client().post(url, json=payload, headers=self._headers())
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
        return body

    def generate_molecules(
        self,
        seed_smiles: str,
        *,
        algorithm: str = "none",
        num_molecules: int = 20,
        property_name: str = "QED",
        minimize: bool = False,
        min_similarity: float = 0.4,
        particles: int = 30,
        iterations: int = 10,
        scaled_radius: float = 1.0,
        use_cache: bool = True,
    ) -> MolMIMResponse:
        """Call ``/generate`` once around ``seed_smiles``.

        ``algorithm="none"`` performs similarity-constrained random sampling
        and ignores ``property_name``, ``minimize``, ``min_similarity`` and
        ``iterations``; those fields are omitted from the request rather than
        sent and silently discarded, so the recorded request matches what the
        service acted on.
        """
        self._require_available()
        if algorithm not in ALGORITHMS:
            raise ValueError(f"algorithm must be one of {ALGORITHMS}, got {algorithm!r}")
        if algorithm == "CMA-ES" and property_name not in PROPERTIES:
            raise ValueError(f"property_name must be one of {PROPERTIES}, got {property_name!r}")
        if not seed_smiles.strip():
            raise ValueError("seed_smiles must be a non-empty SMILES string")

        request: dict[str, Any] = {
            "smi": seed_smiles,
            "algorithm": algorithm,
            "num_molecules": int(num_molecules),
            "particles": int(particles),
        }
        if algorithm == "CMA-ES":
            request.update(
                {
                    "property_name": property_name,
                    "minimize": bool(minimize),
                    "min_similarity": float(min_similarity),
                    "iterations": int(iterations),
                }
            )
        else:
            request["scaled_radius"] = float(scaled_radius)

        for field_name, (low, high) in _RANGES.items():
            if field_name not in request:
                continue
            value = request[field_name]
            if not low <= value <= high:
                raise ValueError(
                    f"{field_name}={value} is outside the documented range "
                    f"[{low}, {high}] for MolMIM {self.settings.molmim_model}"
                )

        key = self.cache.key_for(
            model=self.settings.molmim_model,
            version=self.settings.molmim_model,
            payload=request,
        )
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return MolMIMResponse(
                    generated=cached.get("generated", []),
                    request=request,
                    cached=True,
                    raw_keys=sorted(cached),
                    scores=cached.get("scores") or [],
                    score_type=cached.get("score_type"),
                )

        body = self._post(self.endpoint, request)
        generated, scores, score_type = self._parse_generated(body)

        if use_cache:
            self.cache.put(
                key,
                {
                    "generated": generated,
                    "scores": scores,
                    "score_type": score_type,
                    "request": request,
                },
            )
        return MolMIMResponse(
            generated=generated,
            request=request,
            cached=False,
            raw_keys=sorted(body),
            scores=scores,
            score_type=score_type,
        )

    def _parse_generated(
        self, body: dict[str, Any]
    ) -> tuple[list[Any], list[float | None], str | None]:
        """Molecules from a ``/generate`` response, in either shape in use.

        The self-hosted container returns the documented
        ``{"generated": [smiles]}``. The hosted endpoint does not: observed
        live on 2026-10-13 it returns ``{"molecules": "<JSON string>",
        "score_type": ...}``, where the string decodes to
        ``[{"sample": smiles, "score": float}]``.
        """
        if "generated" not in body and "molecules" in body:
            molecules = body["molecules"]
            if isinstance(molecules, str):
                try:
                    molecules = json.loads(molecules)
                except ValueError as exc:
                    raise AdapterCallFailed(
                        self.name, f"'molecules' was a string but not JSON: {molecules[:200]}"
                    ) from exc
            if not isinstance(molecules, list):
                raise AdapterCallFailed(
                    self.name, f"'molecules' was {type(molecules).__name__}, expected a list"
                )
            generated: list[Any] = []
            scores: list[float | None] = []
            for item in molecules:
                if isinstance(item, dict):
                    generated.append(item.get("sample", item.get("smiles")))
                    score = item.get("score")
                    scores.append(float(score) if isinstance(score, (int, float)) else None)
                else:
                    generated.append(item)
                    scores.append(None)
            score_type = body.get("score_type")
            return generated, scores, score_type if isinstance(score_type, str) else None

        generated = body.get("generated")
        # Some deployments return the list wrapped one level deeper, as
        # /sampling does. Flatten one level rather than failing.
        if isinstance(generated, list) and generated and isinstance(generated[0], list):
            generated = [s for group in generated for s in group]
        if not isinstance(generated, list):
            raise AdapterCallFailed(
                self.name,
                f"'generated' was {type(generated).__name__}, expected a list "
                f"(response keys: {sorted(body)})",
            )
        return generated, [], None

    def embed(self, smiles: list[str], *, use_cache: bool = True) -> np.ndarray:
        """Latent embeddings for molecules, shape ``(n, hidden_dim)``.

        These are features, not predictions: they are useful as surrogate-model
        inputs and for diversity selection, and mean nothing on their own.
        """
        # Nothing to embed needs no service: checking availability first would
        # make an empty list fail on a machine where the NIM is simply absent.
        if not smiles:
            return np.empty((0, 0), dtype=np.float32)
        self._require_available()

        url = self._sibling_endpoint("embedding")
        key = self.cache.key_for(
            model=self.settings.molmim_model,
            version=self.settings.molmim_model,
            payload={"endpoint": "embedding", "sequences": smiles},
        )
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None and "embeddings" in cached:
                return np.asarray(cached["embeddings"], dtype=np.float32)

        body = self._post(url, {"sequences": smiles})
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            raise AdapterCallFailed(
                self.name, f"'embeddings' was {type(embeddings).__name__}, expected a list"
            )
        array = np.asarray(embeddings, dtype=np.float32)
        if array.ndim != 2 or array.shape[0] != len(smiles):
            raise AdapterCallFailed(
                self.name,
                f"expected embeddings of shape ({len(smiles)}, hidden_dim), got "
                f"{array.shape}",
            )
        if use_cache:
            self.cache.put(key, {"embeddings": array.tolist()})
        return array


class MolMIMGenerator:
    """MolMIM-assisted candidate generation around excipient seeds.

    Produces the same :class:`GenerationResult` type as the baseline and the
    GenMol generator, so the three can be compared on validity, uniqueness,
    diversity and runtime.
    """

    name = "molmim-assisted"

    def __init__(
        self,
        adapter: MolMIMAdapter | None = None,
        *,
        rules: PolymerizationRules | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.adapter = adapter or MolMIMAdapter(self.settings)
        self.rules = rules or PolymerizationRules()

    def generate(
        self,
        n: int = 24,
        *,
        seeds: tuple[tuple[str, str], ...] = EXCIPIENT_SEEDS,
        molecules_per_seed: int = 20,
        algorithm: str = "none",
        property_name: str = "QED",
        minimize: bool = False,
        min_similarity: float = 0.4,
        scaled_radius: float = 1.0,
        dp_choices: tuple[int, ...] = (10, 25, 50),
        end_group: EndGroup = EndGroup.METHYL,
        generation_round: int = 0,
        use_cache: bool = True,
    ) -> GenerationResult:
        """Sample around each seed, convert to repeat units, and validate."""
        manifest = RunManifest(
            operation="generate-molmim",
            evidence=EVIDENCE_HYPOTHESIS,
            parameters={
                "n_requested": n,
                "seeds": [{"label": s[0], "smiles": s[1]} for s in seeds],
                "molecules_per_seed": molecules_per_seed,
                "algorithm": algorithm,
                "property_name": property_name if algorithm == "CMA-ES" else None,
                "minimize": minimize if algorithm == "CMA-ES" else None,
                "min_similarity": min_similarity if algorithm == "CMA-ES" else None,
                "scaled_radius": scaled_radius if algorithm == "none" else None,
                "dp_choices": list(dp_choices),
                "end_group": str(end_group),
                "endpoint": self.adapter.endpoint,
            },
            requested_backend="nvidia-nim-molmim",
            actual_backend="nvidia-nim-molmim",
        )
        manifest.record_model(
            self.settings.molmim_model,
            self.settings.molmim_model,
            kind="molecule-generator",
            endpoint=self.adapter.endpoint,
            licence=MOLMIM_LICENCE,
        )
        if algorithm == "CMA-ES":
            manifest.warn(property_objective_caveat(property_name, minimize))

        records: list[GenerationRecord] = []
        candidates: list[PolymerCandidate] = []
        seen_units: set[str] = set()
        emitted: set[str] = set()
        conversions = []
        routes: dict[str, dict[str, object]] = {}
        n_generated = 0

        for label, seed in seeds:
            if len(candidates) >= n:
                break
            try:
                response = self.adapter.generate_molecules(
                    seed,
                    algorithm=algorithm,
                    num_molecules=molecules_per_seed,
                    property_name=property_name,
                    minimize=minimize,
                    min_similarity=min_similarity,
                    scaled_radius=scaled_radius,
                    use_cache=use_cache,
                )
            except (AdapterUnavailable, AdapterCallFailed) as exc:
                manifest.fail(f"seed {label!r}: {exc}")
                continue

            if response.cached:
                manifest.record_model(
                    self.settings.molmim_model,
                    self.settings.molmim_model,
                    kind="molecule-generator",
                    endpoint=self.adapter.endpoint,
                    licence=MOLMIM_LICENCE,
                    cached=True,
                )

            for smiles in response.smiles:
                n_generated += 1
                derivation = derive_repeat_units(smiles)
                conversions.append(derivation)

                if not derivation.usable:
                    records.append(
                        GenerationRecord(
                            proposed_smiles=smiles,
                            source=f"{self.name}:{label}",
                            accepted=False,
                            rejection_codes=derivation.rejection_codes(),
                            rejection_detail=derivation.rejection_detail(),
                        )
                    )
                    manifest.exclude(
                        smiles, derivation.rejection_detail(), stage="monomer-to-repeat-unit"
                    )
                    continue

                for unit in derivation.units:
                    unit_smiles, mechanism = unit.repeat_unit, unit.label
                    validation = validate_repeat_unit(unit_smiles, self.rules)
                    record = GenerationRecord(
                        proposed_smiles=f"{smiles} -> {unit_smiles}",
                        source=f"{self.name}:{label}:{mechanism}",
                        accepted=False,
                        canonical_smiles=validation.canonical_smiles,
                        cautions=list(validation.cautions),
                    )
                    if not validation.valid:
                        record.rejection_codes = validation.reason_codes
                        record.rejection_detail = validation.summary
                        records.append(record)
                        manifest.exclude(
                            unit_smiles, validation.summary, stage="repeat-unit-validation"
                        )
                        continue

                    canonical = validation.canonical_smiles or unit_smiles
                    if canonical in seen_units:
                        record.duplicate_of = canonical
                        records.append(record)
                        continue
                    seen_units.add(canonical)
                    record.accepted = True
                    records.append(record)
                    # The route is keyed by the canonical unit, so the pipeline
                    # can store "how would this be made" with the candidate.
                    routes[canonical] = unit.to_dict()

                    if len(candidates) >= n:
                        continue
                    dp = dp_choices[len(candidates) % len(dp_choices)]
                    try:
                        candidate = PolymerCandidate(
                            blocks=(
                                MonomerBlock(
                                    unit=RepeatUnit(
                                        smiles=canonical,
                                        name=f"molmim:{mechanism}",
                                        family=f"molmim-{mechanism}",
                                        source="molmim",
                                    ),
                                    mole_fraction=1.0,
                                ),
                            ),
                            chain_length=ChainLength(degree_of_polymerisation=dp),
                            end_group=end_group,
                            generator=f"{self.name}/{self.settings.molmim_model}",
                            generation_round=generation_round,
                            notes=(
                                f"sampled from MolMIM around {label} ({seed}) "
                                f"via {mechanism}"
                            ),
                        )
                    except ValueError as exc:
                        record.accepted = False
                        record.rejection_detail = str(exc)
                        continue
                    if candidate.candidate_id not in emitted:
                        emitted.add(candidate.candidate_id)
                        candidates.append(candidate)

        stats = derivation_statistics(conversions)
        manifest.finish(
            n_molecules_generated=n_generated,
            conversion=stats,
            n_repeat_units_accepted=len(seen_units),
            n_candidates=len(candidates),
        )
        if n_generated == 0:
            manifest.warn(
                "no molecules were generated; MolMIM was unavailable or every seed "
                "call failed. See errors."
            )

        return GenerationResult(
            candidates=candidates,
            records=records,
            generator=self.name,
            manifest=manifest,
            routes=routes,
        )

    def optimise_candidate(
        self,
        candidate: PolymerCandidate,
        *,
        n: int = 12,
        dp_override: int | None = 3,
        algorithm: str = "none",
        min_similarity: float = 0.4,
        scaled_radius: float = 1.0,
        property_name: str = "QED",
        minimize: bool = False,
        generation_round: int = 1,
        use_cache: bool = True,
    ) -> GenerationResult:
        """Explore the latent neighbourhood of an existing candidate.

        The seed is a short capped oligomer of the candidate rather than its
        repeat-unit SMILES, because a SMILES containing ``[*]`` dummy atoms is
        not a molecule MolMIM was trained on. ``dp_override`` defaults to 3 to
        keep the seed inside the size range the model handles; the resulting
        children are converted back to repeat units through the usual path, so
        the oligomer is only ever a *query*, never a candidate.
        """
        build = build_oligomer(
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
        label = f"parent {candidate.display_name()} (DP-{build.degree_of_polymerisation} seed)"
        result = self.generate(
            n,
            seeds=((label, build.smiles),),
            molecules_per_seed=max(n * 2, 20),
            algorithm=algorithm,
            property_name=property_name,
            minimize=minimize,
            min_similarity=min_similarity,
            scaled_radius=scaled_radius,
            dp_choices=(candidate.chain_length.degree_of_polymerisation,),
            end_group=candidate.end_group,
            generation_round=generation_round,
            use_cache=use_cache,
        )
        result.manifest.inputs["parent_candidate_id"] = candidate.candidate_id
        result.manifest.inputs["parent_oligomer"] = build.provenance()
        result.candidates = [
            c.model_copy(update={"parent_id": candidate.candidate_id}) for c in result.candidates
        ]
        return result


__all__ = [
    "ALGORITHMS",
    "EXCIPIENT_SEEDS",
    "MOLMIM_LICENCE",
    "PROPERTIES",
    "MolMIMAdapter",
    "MolMIMGenerator",
    "MolMIMResponse",
    "molmim_availability",
    "property_objective_caveat",
]

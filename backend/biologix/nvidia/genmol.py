"""BioNeMo GenMol adapter: fragment-based molecule generation.

API contract, taken from NVIDIA's GenMol NIM documentation for 2.0.0:

* ``POST {base}/generate``
* request: ``smiles`` (SAFE or SMILES, may contain ``[*{min-max}]`` masks),
  ``num_molecules`` (1-1000, default 30), ``temperature`` (0.01-10.0),
  ``noise`` (0.0-2.0), ``gamma`` (0.0-1.0), ``min_add_len`` (1-128),
  ``scoring`` (``QED`` or ``LogP``), ``unique`` (bool), ``filter`` (bool).
  ``step_size`` is deprecated in 2.0.0 and is not sent.
* response: ``{"status": "success"|"failed", "molecules": [{"smiles", "score"}],
  "error": ...}``. The model works internally in SAFE but **validates and
  converts back to standard SMILES before returning**, so the adapter
  receives SMILES.
* maximum input length is 512 tokens.
* health: ``GET {base}/v1/health/ready``; models: ``GET {base}/v1/models``.

GenMol generates *molecules*. This adapter never treats one as a repeat unit:
every returned SMILES goes through
:mod:`biologix.polymers.monomer_to_repeat` to find a polymerisable handle and
then through :mod:`biologix.polymers.rules`, and every rejection is recorded
with its reason. The yield that matters is molecules-to-valid-repeat-units,
not molecules-generated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from rdkit import Chem

from biologix import EVIDENCE_HYPOTHESIS
from biologix.errors import AdapterCallFailed, AdapterUnavailable
from biologix.nvidia.cache import JsonCache
from biologix.nvidia.endpoints import is_local, join, probe_health
from biologix.polymers.derive import derivation_statistics, derive_repeat_units
from biologix.polymers.generate import GenerationRecord, GenerationResult
from biologix.polymers.models import (
    ChainLength,
    EndGroup,
    MonomerBlock,
    PolymerCandidate,
    RepeatUnit,
)
from biologix.polymers.rules import PolymerizationRules, validate_repeat_unit
from biologix.provenance import RunManifest
from biologix.settings import Settings, get_settings

GENMOL_LICENCE = (
    "NVIDIA AI Foundation Models Community License / NVIDIA AI product agreement; "
    "see the GenMol NIM EULA. Generated structures are subject to those terms."
)

# Documented parameter ranges, enforced client-side so an out-of-range value
# fails here with a clear message instead of as an opaque HTTP 422.
_RANGES: dict[str, tuple[float, float]] = {
    "num_molecules": (1, 1000),
    "temperature": (0.01, 10.0),
    "noise": (0.0, 2.0),
    "gamma": (0.0, 1.0),
    "min_add_len": (1, 128),
}

# Fragment templates used as generation seeds. Each is a polymer-relevant
# fragment plus a mask telling GenMol how many tokens to add. The mask syntax
# is ``[*{min-max}]``.
# Seed templates.
#
# Chosen by measurement against the live NIM on 2026-10-15, not by taste.
#
# The original set asked for 10-30 added atoms, three of them growing from a
# *disconnected* seed fragment. Measured, that gave drug-sized output: median
# repeat-unit mass 344 Da, few polymerisable handles, and everything that
# survived was too lipophilic to be an aqueous excipient — median logP per
# repeat unit around 2.
#
# Each template below keeps a polymerisable core **connected** to the growth
# point, asks for only a few atoms, and seeds the decoration with polar
# chemistry. Measured on the same endpoint, these produced poly(acrylamide)
# (logP/repeat -0.38), poly(hydroxyethyl acrylamide) (-0.76), poly(acryloyl-
# morpholine) (-0.01) and a PEG-like acrylate (0.32) — all of them real
# excipient-class polymers, and all of them through the default constraint set.
#
# The cores span the mechanism families deliberately, so one run explores
# radical, ring-opening and step-growth chemistry rather than just one.
#
# One caveat the measurement also exposed: GenMol frequently returns the seed
# core unchanged. Those molecules are counted separately as
# ``n_unchanged_seed``, because a candidate that is simply our own seed handed
# back is not something the model contributed.
POLYMER_SEED_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("acrylamide core", "C=CC(=O)N[*{3-8}]"),
    ("hydroxyethyl acrylamide core", "C=CC(=O)NCCO[*{2-4}]"),
    ("morpholino acrylamide core", "C=CC(=O)N1CCOCC1[*{1-3}]"),
    ("methacrylamide core", "C=C(C)C(=O)N[*{3-7}]"),
    ("PEG-like acrylate core", "C=CC(=O)OCCOC[*{1-4}]"),
    ("acrylate core", "C=CC(=O)OC[*{2-6}]"),
    ("N-vinyl amide core", "C=CN(C=O)[*{2-6}]"),
    ("aliphatic diol core", "OCCO[*{2-6}]"),
    ("lactone core", "O=C1CCCO1[*{2-5}]"),
    ("oxazoline core", "CC1=NCCO1[*{2-4}]"),
)


def template_core(template: str) -> str | None:
    """The seed fragment of a template, with the mask removed.

    Used to tell a molecule the model actually built from one that is the seed
    handed straight back.
    """
    stripped = re.sub(r"\[\*\{\d+-\d+\}\]", "", template).strip(".")
    if not stripped:
        return None
    mol = Chem.MolFromSmiles(stripped)
    return Chem.MolToSmiles(mol) if mol is not None else None


@dataclass
class GenMolResponse:
    """One raw GenMol call."""

    molecules: list[dict[str, Any]]
    status: str
    request: dict[str, Any]
    cached: bool = False
    error: str | None = None
    raw_keys: list[str] = field(default_factory=list)

    @property
    def smiles(self) -> list[str]:
        return [m["smiles"] for m in self.molecules if isinstance(m, dict) and m.get("smiles")]


def genmol_availability(settings: Settings | None = None) -> dict[str, Any]:
    """Whether GenMol can be reached, without making a generation call."""
    s = settings or get_settings()
    base = s.genmol_base_url.rstrip("/")
    local = is_local(base)
    needs_key = s.genmol_hosted or not local
    has_key = bool(s.nvidia_api_key)
    # Only a local container has a readiness path; the hosted endpoint answers
    # 404 there, which used to be misreported as GenMol being unreachable.
    health = probe_health(base, s.nvidia_api_key)

    reason = None
    if needs_key and not has_key:
        reason = "endpoint is remote but NVIDIA_API_KEY is not set"
    elif health["probed"] and not health["reachable"]:
        reason = (
            f"GenMol health check at {health.get('url')} failed ({health['detail']}). "
            "Start the container with: docker run --rm --gpus all -p 8000:8000 "
            "nvcr.io/nim/nvidia/genmol:2.0.0"
        )

    return {
        "adapter": "bionemo-genmol",
        "available": reason is None,
        "reason": reason,
        "endpoint": f"{base}/{s.genmol_path.lstrip('/')}",
        "health": health,
        "health_endpoint": health.get("url"),
        "health_detail": health["detail"],
        "model": s.genmol_model,
        "container": "nvcr.io/nim/nvidia/genmol:2.0.0",
        "licence": GENMOL_LICENCE,
        "requires_gpu": local,
        "gpu_note": (
            "Self-hosted GenMol runs as a GPU NIM container with no CPU mode. "
            "A hosted endpoint also exists on build.nvidia.com, so with "
            "NVIDIA_API_KEY set this adapter needs no local GPU: point "
            "BIOLOGIX_GENMOL_BASE_URL at https://health.api.nvidia.com/v1/biology "
            "and set BIOLOGIX_GENMOL_HOSTED=true. Without either, the baseline "
            "generator is used instead."
        ),
        "api_contract_verified": True,
        "optional": True,
    }


class GenMolAdapter:
    """Client for the GenMol NIM ``/generate`` endpoint."""

    name = "bionemo-genmol"

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
        self.cache = cache if cache is not None else JsonCache("genmol")

    @property
    def endpoint(self) -> str:
        return join(self.settings.genmol_base_url, self.settings.genmol_path)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.nvidia_api_key:
            headers["Authorization"] = f"Bearer {self.settings.nvidia_api_key}"
        return headers

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> GenMolAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def generate_molecules(
        self,
        template: str = "[*{20-30}]",
        *,
        num_molecules: int = 30,
        temperature: float = 1.0,
        noise: float = 1.0,
        gamma: float = 0.0,
        min_add_len: int = 24,
        scoring: str = "QED",
        unique: bool = True,
        filter_by_fragment: bool = False,
        use_cache: bool = True,
    ) -> GenMolResponse:
        """Call ``/generate`` once and return the raw molecules.

        Parameters are range-checked against the documented limits before the
        request is sent.
        """
        availability = genmol_availability(self.settings)
        if not availability["available"]:
            raise AdapterUnavailable(
                self.name,
                availability["reason"] or "not configured",
                remedy="GenMol requires a GPU NIM container; the baseline "
                "generator runs without it",
            )

        if scoring not in ("QED", "LogP"):
            raise ValueError(f"scoring must be 'QED' or 'LogP', got {scoring!r}")
        request: dict[str, Any] = {
            "smiles": template,
            "num_molecules": int(num_molecules),
            "temperature": float(temperature),
            "noise": float(noise),
            "gamma": float(gamma),
            "min_add_len": int(min_add_len),
            "scoring": scoring,
            "unique": bool(unique),
            "filter": bool(filter_by_fragment),
        }
        for field_name, (low, high) in _RANGES.items():
            value = request[field_name]
            if not low <= value <= high:
                raise ValueError(
                    f"{field_name}={value} is outside the documented range "
                    f"[{low}, {high}] for GenMol {self.settings.genmol_model}"
                )
        if len(template) > 512:
            raise ValueError(
                f"template is {len(template)} characters; GenMol accepts at most "
                "512 tokens of input. Shorten the seed fragment."
            )

        key = self.cache.key_for(
            model=self.settings.genmol_model,
            version=self.settings.genmol_model,
            payload=request,
        )
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return GenMolResponse(
                    molecules=cached.get("molecules", []),
                    status=cached.get("status", "success"),
                    request=request,
                    cached=True,
                    raw_keys=sorted(cached),
                )

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

        status = str(body.get("status", "unknown"))
        if status.lower() == "failed":
            raise AdapterCallFailed(
                self.name, f"model reported failure: {body.get('error', 'no error field')}"
            )
        molecules = body.get("molecules") or []
        if not isinstance(molecules, list):
            raise AdapterCallFailed(
                self.name, f"'molecules' was {type(molecules).__name__}, expected a list"
            )

        if use_cache:
            self.cache.put(key, {"status": status, "molecules": molecules, "request": request})

        return GenMolResponse(
            molecules=molecules,
            status=status,
            request=request,
            cached=False,
            raw_keys=sorted(body),
        )


class GenMolGenerator:
    """GenMol-assisted candidate generation.

    Produces the same :class:`GenerationResult` type as the baseline so the
    two can be compared on validity, uniqueness, diversity and runtime.
    """

    name = "genmol-assisted"

    def __init__(
        self,
        adapter: GenMolAdapter | None = None,
        *,
        rules: PolymerizationRules | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.adapter = adapter or GenMolAdapter(self.settings)
        self.rules = rules or PolymerizationRules()

    def generate(
        self,
        n: int = 24,
        *,
        templates: tuple[tuple[str, str], ...] = POLYMER_SEED_TEMPLATES,
        molecules_per_template: int = 30,
        min_add_len: int = 6,
        dp_choices: tuple[int, ...] = (10, 25, 50),
        end_group: EndGroup = EndGroup.METHYL,
        temperature: float = 1.0,
        noise: float = 1.0,
        scoring: str = "QED",
        generation_round: int = 0,
        use_cache: bool = True,
    ) -> GenerationResult:
        """Generate molecules, convert them to repeat units, and validate.

        The manifest records molecules generated, how many had a polymerisable
        handle, and how many of the resulting repeat units passed validation.
        """
        manifest = RunManifest(
            operation="generate-genmol",
            evidence=EVIDENCE_HYPOTHESIS,
            parameters={
                "n_requested": n,
                "templates": [{"label": t[0], "template": t[1]} for t in templates],
                "molecules_per_template": molecules_per_template,
                "min_add_len": min_add_len,
                "temperature": temperature,
                "noise": noise,
                "scoring": scoring,
                "dp_choices": list(dp_choices),
                "end_group": str(end_group),
                "endpoint": self.adapter.endpoint,
            },
            requested_backend="nvidia-nim-genmol",
            actual_backend="nvidia-nim-genmol",
        )
        manifest.record_model(
            self.settings.genmol_model,
            self.settings.genmol_model,
            kind="molecule-generator",
            endpoint=self.adapter.endpoint,
            licence=GENMOL_LICENCE,
        )

        records: list[GenerationRecord] = []
        candidates: list[PolymerCandidate] = []
        seen_units: set[str] = set()
        emitted: set[str] = set()
        conversions = []
        routes: dict[str, dict[str, object]] = {}
        n_generated = 0

        seed_cores = {label: template_core(template) for label, template in templates}
        n_unchanged_seed = 0

        for label, template in templates:
            if len(candidates) >= n:
                break
            try:
                response = self.adapter.generate_molecules(
                    template,
                    num_molecules=molecules_per_template,
                    temperature=temperature,
                    noise=noise,
                    scoring=scoring,
                    min_add_len=min_add_len,
                    use_cache=use_cache,
                )
            except (AdapterUnavailable, AdapterCallFailed) as exc:
                manifest.fail(f"template {label!r}: {exc}")
                continue

            if response.cached:
                manifest.record_model(
                    self.settings.genmol_model,
                    self.settings.genmol_model,
                    kind="molecule-generator",
                    endpoint=self.adapter.endpoint,
                    licence=GENMOL_LICENCE,
                    cached=True,
                )

            for smiles in response.smiles:
                n_generated += 1
                core = seed_cores.get(label)
                parsed = Chem.MolFromSmiles(smiles)
                unchanged = bool(
                    core and parsed is not None and Chem.MolToSmiles(parsed) == core
                )
                if unchanged:
                    n_unchanged_seed += 1
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
                        source=(
                            f"{self.name}:{label}:{mechanism}"
                            + (":unchanged-seed" if unchanged else "")
                        ),
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
                                        name=f"genmol:{mechanism}",
                                        family=f"genmol-{mechanism}",
                                        source="genmol",
                                    ),
                                    mole_fraction=1.0,
                                ),
                            ),
                            chain_length=ChainLength(degree_of_polymerisation=dp),
                            end_group=end_group,
                            generator=f"{self.name}/{self.settings.genmol_model}",
                            generation_round=generation_round,
                            notes=(
                                f"from GenMol molecule {smiles} via {mechanism}"
                                + (
                                    " (grafted onto a polymerisable backbone: the "
                                    "molecule itself is not a monomer)"
                                    if unit.derivation == "graft"
                                    else ""
                                )
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
        stats["n_unchanged_seed"] = n_unchanged_seed
        stats["unchanged_seed_note"] = (
            f"{n_unchanged_seed} of {n_generated} molecules were the seed core returned "
            "unchanged. Those are this repository's own input, not something GenMol "
            "contributed, and they are flagged in each record's source."
        )
        manifest.finish(
            n_molecules_generated=n_generated,
            conversion=stats,
            n_repeat_units_accepted=len(seen_units),
            n_candidates=len(candidates),
        )
        if n_generated == 0:
            manifest.warn(
                "no molecules were generated; GenMol was unavailable or every "
                "template call failed. See errors."
            )

        return GenerationResult(
            candidates=candidates,
            records=records,
            generator=self.name,
            manifest=manifest,
            routes=routes,
        )

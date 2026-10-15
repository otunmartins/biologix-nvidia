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
from rdkit.Chem import Crippen

from biologix import EVIDENCE_HYPOTHESIS
from biologix.errors import AdapterCallFailed, AdapterUnavailable, BiologixError
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
from biologix.polymers.oligomer import oligomer_for_screening
from biologix.polymers.rules import PolymerizationRules, validate_repeat_unit
from biologix.provenance import RunManifest
from biologix.settings import Settings, get_settings
from biologix.targets.formulation import FormulationProfile

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
# Each template carries the chemistry tags it belongs to, so a target's
# formulation profile can weight it. The tags are the same vocabulary
# :mod:`biologix.targets.formulation` produces: a profile that penalises
# ``michael-acceptor`` down-weights every activated-alkene template here,
# and one that favours ``polyether`` pulls the PEG-like and oxazoline seeds up.
#
# A second measurement, on 2026-10-15, is why the list is longer than it was.
# Ten templates asked for 30 molecules each but the generator stopped at the
# first ``n`` candidates, so a run saw only ~47 molecules and surfaced whichever
# happened to come first in template order. Under the aqueous-parenteral gate
# that left 5 of 24 standing, and on an unlucky ordering as few as one. The
# templates below are run to completion and the pool is ranked, not truncated.
SeedTemplate = tuple[str, str, tuple[str, ...]]

POLYMER_SEED_TEMPLATES: tuple[SeedTemplate, ...] = (
    # Activated alkenes: radical chain growth. Michael acceptors, so a target
    # with free thiols penalises these.
    ("acrylamide core", "C=CC(=O)N[*{3-8}]", ("neutral", "hydrophilic", "michael-acceptor")),
    (
        "hydroxyethyl acrylamide core",
        "C=CC(=O)NCCO[*{2-4}]",
        ("neutral", "hydrophilic", "michael-acceptor"),
    ),
    (
        "morpholino acrylamide core",
        "C=CC(=O)N1CCOCC1[*{1-3}]",
        ("neutral", "hydrophilic", "amphiphilic", "michael-acceptor"),
    ),
    ("methacrylamide core", "C=C(C)C(=O)N[*{3-7}]", ("neutral", "amphiphilic")),
    (
        "PEG-like acrylate core",
        "C=CC(=O)OCCOC[*{1-4}]",
        ("neutral", "hydrophilic", "polyether", "michael-acceptor"),
    ),
    (
        "oligo-ethylene-glycol acrylate core",
        "C=CC(=O)OCCOCCO[*{1-3}]",
        ("neutral", "hydrophilic", "polyether", "michael-acceptor"),
    ),
    ("acrylate core", "C=CC(=O)OC[*{2-6}]", ("neutral", "amphiphilic", "michael-acceptor")),
    (
        "glycerol-like acrylamide core",
        "C=CC(=O)NCC(O)CO[*{1-3}]",
        ("neutral", "hydrophilic", "michael-acceptor"),
    ),
    # N-vinyl amides: the povidone family. Not Michael acceptors in the same
    # sense, and the most established parenteral excipient chemistry here.
    ("N-vinyl amide core", "C=CN(C=O)[*{2-6}]", ("neutral", "hydrophilic")),
    ("N-vinyl pyrrolidone core", "C=CN1CCCC1=O[*{1-3}]", ("neutral", "hydrophilic")),
    ("N-vinyl caprolactam core", "C=CN1CCCCCC1=O[*{1-2}]", ("neutral", "amphiphilic")),
    # Step growth and ring opening: polyester, polyether, polyamide.
    ("aliphatic diol core", "OCCO[*{2-6}]", ("neutral", "hydrophilic", "polyether")),
    ("triol core", "OCC(O)CO[*{2-5}]", ("neutral", "hydrophilic")),
    ("lactone core", "O=C1CCCO1[*{2-5}]", ("neutral", "amphiphilic")),
    ("lactide-like core", "CC1OC(=O)C(C)OC1=O[*{1-3}]", ("neutral", "amphiphilic")),
    ("morpholinedione core", "O=C1COC(=O)CN1[*{1-3}]", ("neutral", "hydrophilic")),
    ("oxazoline core", "CC1=NCCO1[*{2-4}]", ("neutral", "amphiphilic", "polyether")),
    (
        "hydroxyethyl oxazoline core",
        "OCCC1=NCCO1[*{1-3}]",
        ("neutral", "hydrophilic", "polyether"),
    ),
    ("lactam core", "O=C1CCCCN1[*{2-4}]", ("neutral", "hydrophilic")),
    ("aliphatic diacid core", "OC(=O)CCCC(=O)O[*{1-4}]", ("anionic", "hydrophilic")),
    ("aliphatic diamine core", "NCCCCN[*{1-4}]", ("cationic", "hydrophilic")),
    # Sugar-like and zwitterionic: the most hydrophilic chemistry available,
    # for a target that needs a non-interacting excipient.
    ("polyol acrylamide core", "C=CC(=O)NC(CO)CO[*{1-3}]", ("neutral", "hydrophilic")),
    (
        "sulfobetaine-like core",
        # The ammonium must carry its charge: a neutral N with four bonds is
        # not a molecule, and RDKit rejects it, so the seed would have burned
        # a NIM call and returned nothing.
        "C=CC(=O)NCC[N+](C)(C)CCCS(=O)(=O)[O-][*{1-2}]",
        ("zwitterionic", "hydrophilic", "michael-acceptor"),
    ),
    (
        "phosphorylcholine-like core",
        "C=CC(=O)OCCOP(=O)([O-])OCC[N+](C)(C)C[*{1-2}]",
        ("zwitterionic", "hydrophilic", "michael-acceptor"),
    ),
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


def template_weight(tags: tuple[str, ...], profile: FormulationProfile | None) -> float:
    """How many molecules to ask a template for, relative to the others.

    A profile that favours a tag doubles the template's share; one that
    penalises a tag halves it. A template is never dropped to zero: the point
    is to bias the pool, not to decide the chemistry in advance, and a seed
    that the profile dislikes can still yield the unit that wins on measured
    descriptors.
    """
    if profile is None:
        return 1.0
    weight = 1.0
    for tag in tags:
        if tag in profile.favour:
            weight *= 2.0
        if tag in profile.penalise:
            weight *= 0.5
    return max(0.25, min(4.0, weight))


# The caps to try, in the order preferred when more than one works. Methyl
# first because it is the least chemically eventful terminus; hydroxyl last
# because it is the one most likely to make a peroxide against an
# oxygen-terminated unit.
_CAP_PREFERENCE: tuple[EndGroup, ...] = (
    EndGroup.METHYL,
    EndGroup.HYDROGEN,
    EndGroup.ACETYL,
    EndGroup.HYDROXYL,
)


def constructible_end_group(
    repeat_smiles: str, *, dp: int = 10
) -> tuple[EndGroup | None, str | None]:
    """The first cap that builds a clean oligomer from this unit, if any.

    A repeat unit is only a candidate if the *polymer* can actually be made
    from it, and that is not implied by the unit passing validation on its
    own. Two failures are caught here that validation misses:

    * A cap incompatible with the unit's termini. ``*OCC(=O)NCC(*)=O`` is a
      perfectly good polyester unit, but a hydroxyl cap on its oxygen-attached
      end makes a peroxide. Choosing the cap per unit fixes it; the previous
      code applied methyl to everything and took whatever happened.
    * A unit whose own head-to-tail linkage is the problem. ``*OCC(CO*)NC(=O)C=C``
      carries both attachment points on oxygen, so polymerising it puts an
      O-O bond in the *backbone* — a poly(peroxide), which is an explosive
      hazard rather than an excipient. No cap can rescue that, and the unit
      has to be refused. These ranked at the very top on polarity, so without
      this gate the most attractive-looking candidates were the unsafe ones.

    Returns the chosen cap and ``None``, or ``None`` and the reason the unit
    could not be built at all.
    """
    reason: str | None = None
    for cap in _CAP_PREFERENCE:
        try:
            candidate = PolymerCandidate(
                blocks=(
                    MonomerBlock(
                        unit=RepeatUnit(
                            smiles=repeat_smiles,
                            name="probe",
                            family="probe",
                            source="probe",
                        ),
                        mole_fraction=1.0,
                    ),
                ),
                chain_length=ChainLength(degree_of_polymerisation=dp),
                end_group=cap,
            )
            oligomer_for_screening(candidate)
        except (ValueError, BiologixError) as exc:
            if reason is None:
                reason = str(exc)
            continue
        return cap, None
    return None, reason or "no end group produced a constructible oligomer"


@dataclass
class UnitFit:
    """A validated repeat unit scored for how well it suits the target.

    The score decides which units a capped run surfaces. It is computed from
    RDKit descriptors on the repeat unit and from the target's formulation
    profile — never from the generator's own QED, which is a drug-likeness
    score and the wrong objective for an excipient.
    """

    score: float
    logp: float | None
    aromatic_fraction: float | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fit_score": round(self.score, 3),
            "logp_per_repeat": self.logp,
            "aromatic_fraction": self.aromatic_fraction,
            "reasons": list(self.reasons),
        }


def score_unit_fit(
    repeat_smiles: str,
    tags: tuple[str, ...],
    profile: FormulationProfile | None,
) -> UnitFit:
    """Score one repeat unit for excipient fit against a target profile.

    The two descriptors dominating the aqueous-parenteral constraint set are
    logP per repeat unit and aromatic fraction, so they are computed here
    directly: a unit that will fail the gate is ranked below one that will
    pass, which is what stops a capped run from surfacing a cohort that the
    screen then throws away.
    """
    mol = Chem.MolFromSmiles(repeat_smiles.replace("*", "[H]"))
    if mol is None:
        return UnitFit(score=0.0, logp=None, aromatic_fraction=None, reasons=["unparseable"])

    logp = round(float(Crippen.MolLogP(mol)), 3)
    heavy = mol.GetNumHeavyAtoms()
    aromatic = sum(1 for atom in mol.GetAtoms() if atom.GetIsAromatic())
    aromatic_fraction = round(aromatic / heavy, 4) if heavy else 0.0

    fit = UnitFit(score=0.0, logp=logp, aromatic_fraction=aromatic_fraction)

    # Lipophilicity against the band this target wants. This is the dominant
    # term and the one that makes two biologics get different chemistry:
    # ranking everything on "as polar as possible" gave a hydrophobic target
    # and a hydrophilic one the same cohort, which made target-awareness
    # nominal. A hydrophobic target needs an amphiphile, so its band sits
    # above zero; a hydrophilic one wants the most polar chain available.
    low, high = profile.logp_window if profile is not None else (-3.0, 0.2)
    if low <= logp <= high:
        fit.score += 3.0
        fit.reasons.append(
            f"logP/repeat {logp} is inside the band {low} to {high} this target wants"
        )
    else:
        distance = (low - logp) if logp < low else (logp - high)
        fit.score += 3.0 - 2.5 * min(1.0, distance / 1.5)
        fit.reasons.append(
            f"logP/repeat {logp} is {distance:.2f} outside the band {low} to "
            f"{high} this target wants"
        )
    # The gate's hard ceiling, independent of the target's preferred band.
    if logp > 1.2:
        fit.score -= 1.5 * min(1.0, (logp - 1.2) / 2.0)
        fit.reasons.append(f"logP/repeat {logp} is above the aqueous ceiling of 1.2")

    # Aromatic content, against the gate's ceiling of 0.35.
    if aromatic_fraction <= 0.35:
        fit.score += 1.0 * (0.35 - aromatic_fraction) / 0.35
    else:
        fit.score -= 2.0 * min(1.0, (aromatic_fraction - 0.35) / 0.35)
        fit.reasons.append(
            f"aromatic fraction {aromatic_fraction} is above the ceiling of 0.35"
        )

    # Hydrogen-bond capacity: an excipient works by holding water, so donors
    # and acceptors per heavy atom count for it. Weighted down when the target
    # wants an amphiphile, where maximum polarity is the wrong objective and
    # rewarding it would flatten the difference between targets.
    if heavy:
        polar = sum(1 for atom in mol.GetAtoms() if atom.GetSymbol() in ("N", "O"))
        polar_weight = 0.4 if (profile is not None and "amphiphilic" in profile.favour) else 1.0
        fit.score += polar_weight * min(1.0, (polar / heavy) / 0.35)

    if profile is not None:
        for tag in tags:
            if tag in profile.favour:
                fit.score += 0.5
                fit.reasons.append(f"{tag} chemistry is favoured for this target")
            if tag in profile.penalise:
                fit.score -= 0.75
                fit.reasons.append(f"{tag} chemistry is penalised for this target")
        if "aromatic" in profile.penalise and aromatic_fraction > 0.0:
            fit.score -= 1.0 * min(1.0, aromatic_fraction / 0.35)
            fit.reasons.append(
                "the target is aromatic-rich, so any aromatic content in the "
                "repeat unit carries a pi-stacking risk"
            )

    fit.score = round(fit.score, 4)
    return fit


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
        templates: tuple[SeedTemplate, ...] = POLYMER_SEED_TEMPLATES,
        molecules_per_template: int = 30,
        min_add_len: int = 6,
        dp_choices: tuple[int, ...] = (10, 25, 50),
        end_group: EndGroup = EndGroup.METHYL,
        temperature: float = 1.0,
        noise: float = 1.0,
        scoring: str = "QED",
        generation_round: int = 0,
        use_cache: bool = True,
        profile: FormulationProfile | None = None,
        oversample: float = 4.0,
    ) -> GenerationResult:
        """Generate molecules, convert them to repeat units, and validate.

        Every template is run to completion and the validated units are then
        **ranked** by :func:`score_unit_fit` before the top ``n`` become
        candidates. The previous behaviour — stop as soon as ``n`` candidates
        exist — made the cohort an artefact of template ordering: measured
        against the live NIM it surfaced 24 candidates of which only 5 cleared
        the aqueous-parenteral gate, because the aromatic, lipophilic molecules
        GenMol favours arrived first and filled the quota.

        ``oversample`` sets how much larger the validated pool should be than
        ``n``. Nothing is loosened downstream to compensate: the screen keeps
        its ceilings, and the pool is simply big enough that units which clear
        them exist to be found.

        ``profile`` is the target's formulation profile. When given, it weights
        which seeds are asked for more molecules and how units are ranked, so
        two different biologics get different chemistry. When absent the run is
        target-blind and says so in the manifest.
        """
        manifest = RunManifest(
            operation="generate-genmol",
            evidence=EVIDENCE_HYPOTHESIS,
            parameters={
                "n_requested": n,
                "templates": [
                    {"label": t[0], "template": t[1], "tags": list(t[2])} for t in templates
                ],
                "molecules_per_template": molecules_per_template,
                "oversample": oversample,
                "min_add_len": min_add_len,
                "temperature": temperature,
                "noise": noise,
                "scoring": scoring,
                "dp_choices": list(dp_choices),
                "end_group": str(end_group),
                "endpoint": self.adapter.endpoint,
                "target_profile": profile.to_dict() if profile is not None else None,
                "target_aware": profile is not None,
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
        conversions = []
        routes: dict[str, dict[str, object]] = {}
        n_generated = 0

        seed_cores = {label: template_core(template) for label, template, _ in templates}
        n_unchanged_seed = 0
        n_not_constructible = 0

        # The validated pool: every unique repeat unit that survived
        # derivation and the polymerisation rules, with its fit score. This is
        # deliberately allowed to grow past ``n`` — the ranking at the end is
        # what makes the cap meaningful.
        pool: list[dict[str, Any]] = []
        seen_units: set[str] = set()

        # How many molecules to ask each template for, biased by the profile
        # and scaled so the pool should comfortably exceed ``n``.
        weights = {label: template_weight(tags, profile) for label, _, tags in templates}
        total_weight = sum(weights.values()) or 1.0
        wanted_pool = max(int(n * oversample), n)
        requests: dict[str, int] = {}
        for label, _, _ in templates:
            share = weights[label] / total_weight
            requests[label] = max(
                8, min(1000, int(round(max(molecules_per_template, wanted_pool) * share * 2)))
            )

        manifest.parameters["molecules_requested_per_template"] = requests
        manifest.parameters["template_weights"] = {
            label: round(w, 3) for label, w in weights.items()
        }

        for label, template, tags in templates:
            try:
                response = self.adapter.generate_molecules(
                    template,
                    num_molecules=requests[label],
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

                    # The unit has to yield a real polymer, with a cap that
                    # suits it. A unit that cannot be built is refused here
                    # with the construction error as its reason, rather than
                    # being surfaced and failing in screening.
                    cap, build_error = constructible_end_group(canonical)
                    if cap is None:
                        record.rejection_codes = ["not_constructible"]
                        record.rejection_detail = (
                            f"no end group builds a clean oligomer from this unit: "
                            f"{build_error}"
                        )
                        records.append(record)
                        manifest.exclude(
                            canonical,
                            record.rejection_detail,
                            stage="oligomer-construction",
                        )
                        n_not_constructible += 1
                        continue

                    record.accepted = True
                    records.append(record)
                    # The route is keyed by the canonical unit, so the pipeline
                    # can store "how would this be made" with the candidate.
                    routes[canonical] = unit.to_dict()

                    pool.append(
                        {
                            "canonical": canonical,
                            "mechanism": mechanism,
                            "molecule": smiles,
                            "label": label,
                            "tags": tags,
                            "derivation": unit.derivation,
                            "end_group": cap,
                            "fit": score_unit_fit(canonical, tags, profile),
                        }
                    )

        # Rank the pool and keep the best ``n``. Ties break on the canonical
        # SMILES so a run is reproducible for a fixed pool.
        pool.sort(key=lambda entry: (-entry["fit"].score, entry["canonical"]))
        selected = pool[:n]

        candidates: list[PolymerCandidate] = []
        emitted: set[str] = set()
        for entry in selected:
            canonical = entry["canonical"]
            mechanism = entry["mechanism"]
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
                    # The cap proved constructible for this unit, not a global default.
                    end_group=entry["end_group"],
                    generator=f"{self.name}/{self.settings.genmol_model}",
                    generation_round=generation_round,
                    notes=(
                        f"from GenMol molecule {entry['molecule']} via {mechanism}"
                        + (
                            " (grafted onto a polymerisable backbone: the "
                            "molecule itself is not a monomer)"
                            if entry["derivation"] == "graft"
                            else ""
                        )
                    ),
                )
            except ValueError:
                continue
            if candidate.candidate_id not in emitted:
                emitted.add(candidate.candidate_id)
                candidates.append(candidate)
                # The fit that won this unit its place belongs with the route,
                # so a user can see why it was surfaced and not merely that it
                # was.
                if canonical in routes:
                    routes[canonical] = {
                        **routes[canonical],
                        "selection": entry["fit"].to_dict(),
                    }

        stats = derivation_statistics(conversions)
        stats["n_unchanged_seed"] = n_unchanged_seed
        stats["unchanged_seed_note"] = (
            f"{n_unchanged_seed} of {n_generated} molecules were the seed core returned "
            "unchanged. Those are this repository's own input, not something GenMol "
            "contributed, and they are flagged in each record's source."
        )

        # The selection funnel, so a short cohort can be explained rather than
        # guessed at: how many molecules came back, how many became valid
        # units, and how many of those the cap left behind.
        fit_scores = [entry["fit"].score for entry in pool]
        selection = {
            "n_molecules_generated": n_generated,
            "n_valid_units": len(pool),
            "n_refused_not_constructible": n_not_constructible,
            "n_requested": n,
            "n_selected": len(candidates),
            "n_pool_discarded_by_cap": max(0, len(pool) - len(candidates)),
            "oversample": oversample,
            "fit_score_selected_min": (
                round(min(e["fit"].score for e in selected), 3) if selected else None
            ),
            "fit_score_max": round(max(fit_scores), 3) if fit_scores else None,
            "target_aware": profile is not None,
            "note": (
                "Units are ranked on measured descriptors (logP and aromatic "
                "fraction per repeat unit, polar-atom density) plus the target's "
                "formulation profile, then the top n are surfaced. Ranking does "
                "not relax the screen: a selected unit can still fail it, and "
                "the discarded pool is counted here rather than hidden."
            ),
        }
        if profile is not None:
            selection["profile_favoured"] = list(profile.favour)
            selection["profile_penalised"] = list(profile.penalise)
        stats["selection"] = selection

        manifest.finish(
            n_molecules_generated=n_generated,
            conversion=stats,
            selection=selection,
            n_repeat_units_accepted=len(seen_units),
            n_candidates=len(candidates),
        )
        if n_generated == 0:
            manifest.warn(
                "no molecules were generated; GenMol was unavailable or every "
                "template call failed. See errors."
            )
        elif len(candidates) < n:
            manifest.warn(
                f"{len(candidates)} candidates were produced out of {n} requested: "
                f"{n_generated} molecules yielded only {len(pool)} valid repeat "
                "units. Raise oversample, or widen the seed templates."
            )

        return GenerationResult(
            candidates=candidates,
            records=records,
            generator=self.name,
            manifest=manifest,
            routes=routes,
        )

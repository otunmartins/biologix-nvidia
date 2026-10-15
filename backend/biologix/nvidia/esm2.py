"""BioNeMo ESM-2 adapter: protein sequence embeddings.

Four things this module refuses to do, each because doing it would produce a
number that means something other than what it appears to mean:

1. **Concatenate chains.** Insulin's A and B chains are separate molecules
   held by disulfides. Joining them into one string would ask the model to
   embed a 51-residue peptide that does not exist. Each chain is embedded
   separately and the results are keyed by chain id.
2. **Truncate.** ESM-2 emits at most 1022 residue embeddings. A longer chain
   raises :class:`~biologix.errors.SequenceTooLong` instead of being cut,
   because a truncated protein is a different protein.
3. **Fold modifications into the sequence.** Acylation, PEGylation and
   glycosylation are invisible to a sequence model. They are recorded
   alongside the embedding as ``unrepresented_chemistry`` so downstream code
   can state the gap.
4. **Present embeddings as stability predictions.** An embedding is a feature
   vector. It is tagged ``evidence="computed"`` and
   ``interpretation="model feature"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from biologix import EVIDENCE_COMPUTED
from biologix.errors import AdapterCallFailed, AdapterUnavailable, SequenceTooLong
from biologix.nvidia.cache import ArrayCache
from biologix.nvidia.endpoints import is_local
from biologix.provenance import RunManifest, stable_hash
from biologix.settings import Settings, get_settings
from biologix.targets.models import Biologic

# ESM-2 model licence. The weights originate from Meta's ESM-2 release; the
# NVIDIA NIM packaging is governed additionally by the NVIDIA AI product
# agreement. Recorded in provenance; see docs/NVIDIA_SETUP.md.
ESM2_LICENCE = "MIT (ESM-2 weights, Meta) + NVIDIA AI product terms for NIM packaging"


@dataclass
class ChainEmbedding:
    """Embedding for one chain, with its length limits made explicit."""

    chain_id: str
    sequence: str
    n_residues: int
    # Per-residue embeddings, shape (n_residues, hidden_dim).
    residue_embeddings: np.ndarray | None
    # Mean-pooled vector, shape (hidden_dim,). The pooling is ours, not the
    # model's, and is recorded as such.
    pooled: np.ndarray
    pooling: str = "mean-over-residues"
    cached: bool = False

    @property
    def hidden_dim(self) -> int:
        return int(self.pooled.shape[-1])


@dataclass
class EmbeddingResult:
    """Embeddings for a whole biologic, chain by chain."""

    biologic_name: str
    model: str
    model_version: str
    chains: dict[str, ChainEmbedding] = field(default_factory=dict)
    unrepresented_chemistry: list[str] = field(default_factory=list)
    manifest: RunManifest | None = None

    @property
    def evidence(self) -> str:
        return EVIDENCE_COMPUTED

    def concatenated_pooled(self) -> np.ndarray:
        """Per-chain pooled vectors stacked in sorted chain order.

        This concatenates *vectors*, which is a legitimate feature-engineering
        choice, unlike concatenating *sequences*. Chain order is sorted so the
        feature layout is stable.
        """
        return np.concatenate([self.chains[k].pooled for k in sorted(self.chains)])

    def feature_names(self) -> list[str]:
        names: list[str] = []
        for chain_id in sorted(self.chains):
            dim = self.chains[chain_id].hidden_dim
            names.extend(f"esm2[{chain_id}][{i}]" for i in range(dim))
        return names

    def to_dict(self) -> dict[str, Any]:
        return {
            "biologic": self.biologic_name,
            "model": self.model,
            "model_version": self.model_version,
            "evidence": self.evidence,
            "interpretation": (
                "model feature vector, not a validated prediction of stability, "
                "aggregation propensity or any measurable property"
            ),
            "chains": {
                k: {
                    "n_residues": v.n_residues,
                    "hidden_dim": v.hidden_dim,
                    "pooling": v.pooling,
                    "has_residue_embeddings": v.residue_embeddings is not None,
                    "cached": v.cached,
                }
                for k, v in self.chains.items()
            },
            "unrepresented_chemistry": self.unrepresented_chemistry,
            "chain_handling": "each chain embedded separately; sequences never concatenated",
        }


def esm2_availability(settings: Settings | None = None) -> dict[str, Any]:
    """Whether the ESM-2 adapter can run, and what is missing if not."""
    s = settings or get_settings()
    local = is_local(s.esm2_base_url)
    has_key = bool(s.nvidia_api_key)
    available = local or has_key
    reason = None
    if not available:
        reason = (
            "NVIDIA_API_KEY is not set and ESM2_BASE_URL is not a local endpoint. "
            "Hosted BioNeMo NIM calls require an API key from build.nvidia.com."
        )
    return {
        "adapter": "bionemo-esm2",
        "available": available,
        "reason": reason,
        "endpoint": f"{s.esm2_base_url.rstrip('/')}/{s.esm2_path.lstrip('/')}",
        "model": s.esm2_model,
        "max_residues": s.esm2_max_residues,
        "licence": ESM2_LICENCE,
        "requires_api_key": not local,
        "api_key_present": has_key,
        "endpoint_verified": False,
        "endpoint_note": (
            "The hosted request/response schema could not be confirmed from a "
            "publicly fetchable NVIDIA reference page at build time. The adapter "
            "parses several documented response shapes and records which one it "
            "saw. Confirm the path against your own build.nvidia.com model page "
            "and override BIOLOGIX_ESM2_PATH if it differs."
        ),
    }


class Esm2Adapter:
    """Client for an ESM-2 embedding endpoint, hosted or self-hosted."""

    kind = "protein-embedding"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        cache: ArrayCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        self.cache = cache if cache is not None else ArrayCache("esm2")

    # -- plumbing ---------------------------------------------------------
    @property
    def endpoint(self) -> str:
        base = self.settings.esm2_base_url.rstrip("/")
        path = self.settings.esm2_path.lstrip("/")
        return f"{base}/{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.nvidia_api_key:
            headers["Authorization"] = f"Bearer {self.settings.nvidia_api_key}"
        return headers

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(180.0, connect=30.0))
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> Esm2Adapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- embedding --------------------------------------------------------
    def embed_biologic(
        self,
        biologic: Biologic,
        *,
        return_residue_embeddings: bool = False,
        use_cache: bool = True,
    ) -> EmbeddingResult:
        """Embed every chain of ``biologic`` separately.

        Raises :class:`AdapterUnavailable` when no credentials or endpoint are
        configured, and :class:`SequenceTooLong` for any chain over the
        model's limit.
        """
        availability = esm2_availability(self.settings)
        if not availability["available"]:
            raise AdapterUnavailable(
                "bionemo-esm2",
                availability["reason"] or "not configured",
                remedy="set NVIDIA_API_KEY, or point BIOLOGIX_ESM2_BASE_URL at a "
                "self-hosted ESM-2 NIM container",
            )

        manifest = RunManifest(
            operation="esm2-embed",
            evidence=EVIDENCE_COMPUTED,
            inputs={
                "biologic": biologic.name,
                "chains": {c.chain_id: c.length for c in biologic.chains},
                "modifications": biologic.modification_summary(),
            },
            parameters={
                "model": self.settings.esm2_model,
                "endpoint": self.endpoint,
                "max_residues": self.settings.esm2_max_residues,
                "return_residue_embeddings": return_residue_embeddings,
                "pooling": "mean-over-residues (applied by this adapter, not the model)",
            },
            requested_backend="nvidia-nim-esm2",
            actual_backend="nvidia-nim-esm2",
        )
        manifest.record_model(
            self.settings.esm2_model,
            self.settings.esm2_model,
            kind=self.kind,
            endpoint=self.endpoint,
            licence=ESM2_LICENCE,
        )

        # Length check for every chain *before* any network call, so a long
        # chain fails fast rather than after partial billing.
        limit = self.settings.esm2_max_residues
        for chain in biologic.chains:
            if chain.length > limit:
                raise SequenceTooLong(
                    chain.chain_id, chain.length, limit, self.settings.esm2_model
                )

        result = EmbeddingResult(
            biologic_name=biologic.name,
            model=self.settings.esm2_model,
            model_version=self.settings.esm2_model,
            unrepresented_chemistry=biologic.unmodelled_chemistry(),
            manifest=manifest,
        )
        if result.unrepresented_chemistry:
            manifest.warn(
                "the following chemistry is not represented in the embedding because "
                f"ESM-2 sees only the amino-acid sequence: {result.unrepresented_chemistry}"
            )

        for chain in biologic.chains:
            key = self.cache.key_for(
                model=self.settings.esm2_model,
                version=self.settings.esm2_model,
                payload={
                    "sequence": chain.sequence,
                    "residue_level": return_residue_embeddings,
                },
            )
            cached = self.cache.get(key) if use_cache else None
            if cached is not None:
                array, meta = cached
                residue = array if array.ndim == 2 and return_residue_embeddings else None
                pooled = array.mean(axis=0) if array.ndim == 2 else array
                result.chains[chain.chain_id] = ChainEmbedding(
                    chain_id=chain.chain_id,
                    sequence=chain.sequence,
                    n_residues=chain.length,
                    residue_embeddings=residue,
                    pooled=pooled,
                    cached=True,
                )
                manifest.record_model(
                    self.settings.esm2_model,
                    self.settings.esm2_model,
                    kind=self.kind,
                    endpoint=self.endpoint,
                    licence=ESM2_LICENCE,
                    cached=True,
                )
                continue

            array, shape_seen = self._request_embedding(chain.sequence)
            manifest.parameters.setdefault("response_shapes_seen", []).append(shape_seen)

            if array.ndim == 2:
                n_emitted = array.shape[0]
                if n_emitted < chain.length:
                    manifest.warn(
                        f"chain {chain.chain_id}: model returned {n_emitted} residue "
                        f"embeddings for {chain.length} residues; the difference is "
                        "recorded, not padded over"
                    )
                pooled = array.mean(axis=0)
                residue = array if return_residue_embeddings else None
            else:
                pooled = array
                residue = None

            if use_cache:
                self.cache.put(
                    key,
                    array,
                    {
                        "model": self.settings.esm2_model,
                        "endpoint": self.endpoint,
                        "sequence_sha": stable_hash(chain.sequence, length=32),
                        "n_residues": chain.length,
                        "chain_id": chain.chain_id,
                        "response_shape": shape_seen,
                    },
                )

            result.chains[chain.chain_id] = ChainEmbedding(
                chain_id=chain.chain_id,
                sequence=chain.sequence,
                n_residues=chain.length,
                residue_embeddings=residue,
                pooled=pooled,
                cached=False,
            )

        manifest.finish(
            n_chains=len(result.chains),
            hidden_dims={k: v.hidden_dim for k, v in result.chains.items()},
        )
        return result

    def _request_embedding(self, sequence: str) -> tuple[np.ndarray, str]:
        """POST one sequence and parse whichever documented shape comes back.

        The ESM-2 NIM response schema could not be verified from a public
        reference page, so several shapes are accepted and the one actually
        seen is returned for the manifest. An unrecognised shape is an error,
        never a silent zero vector.
        """
        payload = {"sequences": [sequence]}
        try:
            response = self._get_client().post(
                self.endpoint, json=payload, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise AdapterCallFailed("bionemo-esm2", f"transport error: {exc!r}") from exc

        if response.status_code >= 400:
            raise AdapterCallFailed(
                "bionemo-esm2",
                f"HTTP {response.status_code}: {response.text[:400]}",
                status=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise AdapterCallFailed(
                "bionemo-esm2", f"response was not JSON: {response.text[:200]}"
            ) from exc

        array, shape_seen = _parse_embedding_response(body)
        if array is None:
            raise AdapterCallFailed(
                "bionemo-esm2",
                "could not locate embeddings in the response. Keys seen: "
                f"{sorted(body)[:12] if isinstance(body, dict) else type(body).__name__}. "
                "Set BIOLOGIX_ESM2_PATH correctly or extend _parse_embedding_response.",
            )
        return array, shape_seen


def _parse_embedding_response(body: Any) -> tuple[np.ndarray | None, str]:
    """Extract an embedding array from any of the documented response shapes.

    Returns ``(array, description)``. ``array`` is 1-D for a pooled vector or
    2-D ``(n_residues, hidden)`` for residue-level output.
    """
    candidates: list[tuple[str, Any]] = []
    if isinstance(body, dict):
        for key in ("embeddings", "embedding", "data", "outputs", "output", "result"):
            if key in body:
                candidates.append((key, body[key]))
    elif isinstance(body, list):
        candidates.append(("<root list>", body))

    for key, value in candidates:
        # {"data": [{"embedding": [...]}, ...]} — OpenAI-compatible shape.
        if isinstance(value, list) and value and isinstance(value[0], dict):
            inner = value[0]
            for ikey in ("embedding", "embeddings", "vector"):
                if ikey in inner:
                    arr = np.asarray(inner[ikey], dtype=np.float32)
                    return arr, f"{key}[0].{ikey} ndim={arr.ndim}"
            continue
        if isinstance(value, list):
            arr = np.asarray(value, dtype=np.float32)
            # (1, n_res, hidden) -> drop the batch axis.
            if arr.ndim == 3 and arr.shape[0] == 1:
                return arr[0], f"{key} ndim=3 batch-stripped"
            # (1, hidden) -> single pooled vector.
            if arr.ndim == 2 and arr.shape[0] == 1:
                return arr[0], f"{key} ndim=2 batch-stripped"
            if arr.ndim in (1, 2):
                return arr, f"{key} ndim={arr.ndim}"
    return None, "unrecognised"

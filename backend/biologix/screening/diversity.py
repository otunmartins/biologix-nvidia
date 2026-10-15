"""Deduplication, diversity quantification and diversity-based selection.

Diversity selection is the honest default for choosing experiments before any
model is trained: it makes no claim about which candidate will perform well,
only that a spread of chemistries is more informative than a cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from rdkit import Chem

from biologix.polymers.models import PolymerCandidate
from biologix.screening.fingerprints import (
    DEFAULT_N_BITS,
    DEFAULT_RADIUS,
    BackendReport,
    get_backend,
)


@dataclass
class DedupResult:
    """Outcome of deduplicating a candidate list."""

    unique: list[PolymerCandidate]
    duplicate_groups: dict[str, list[str]] = field(default_factory=dict)
    near_duplicate_pairs: list[tuple[str, str, float]] = field(default_factory=list)
    similarity_threshold: float | None = None

    @property
    def n_removed(self) -> int:
        return sum(len(v) for v in self.duplicate_groups.values())

    def to_dict(self) -> dict[str, object]:
        return {
            "n_unique": len(self.unique),
            "n_removed": self.n_removed,
            "duplicate_groups": self.duplicate_groups,
            "near_duplicate_pairs": [
                {"kept": a, "dropped": b, "similarity": round(s, 4)}
                for a, b, s in self.near_duplicate_pairs
            ],
            "similarity_threshold": self.similarity_threshold,
        }


@dataclass
class DiversityReport:
    """Quantified diversity of a candidate set."""

    n_candidates: int
    mean_pairwise_distance: float
    min_pairwise_distance: float
    median_pairwise_distance: float
    n_clusters: int
    cluster_cutoff: float
    largest_cluster_size: int
    n_singletons: int
    n_families: int
    families: list[str]
    backend: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "n_candidates": self.n_candidates,
            "mean_pairwise_distance": round(self.mean_pairwise_distance, 4),
            "median_pairwise_distance": round(self.median_pairwise_distance, 4),
            "min_pairwise_distance": round(self.min_pairwise_distance, 4),
            "n_clusters": self.n_clusters,
            "cluster_cutoff": self.cluster_cutoff,
            "largest_cluster_size": self.largest_cluster_size,
            "n_singletons": self.n_singletons,
            "n_families": self.n_families,
            "families": self.families,
            "backend": self.backend,
            "interpretation": (
                "Distances are Tanimoto distances between Morgan fingerprints of "
                "fixed-length screening oligomers. They measure structural spread, "
                "not functional diversity."
            ),
        }


def exact_dedup(candidates: list[PolymerCandidate]) -> DedupResult:
    """Collapse candidates with identical structural identity."""
    kept: dict[str, PolymerCandidate] = {}
    groups: dict[str, list[str]] = {}
    for cand in candidates:
        cid = cand.candidate_id
        if cid in kept:
            groups.setdefault(cid, []).append(
                f"{cand.generator}/round{cand.generation_round}"
            )
            continue
        kept[cid] = cand
    return DedupResult(unique=list(kept.values()), duplicate_groups=groups)


def similarity_dedup(
    candidates: list[PolymerCandidate],
    oligomer_smiles: dict[str, str],
    *,
    threshold: float = 0.95,
    prefer_gpu: bool = False,
) -> tuple[DedupResult, BackendReport]:
    """Drop candidates that are near-duplicates of one already kept.

    ``threshold`` is a Tanimoto similarity above which two candidates are
    treated as the same chemistry. The dropped member of each pair is
    recorded, not discarded silently.
    """
    first = exact_dedup(candidates)
    pool = [c for c in first.unique if c.candidate_id in oligomer_smiles]

    if len(pool) < 2:
        first.similarity_threshold = threshold
        _, report = get_backend(prefer_gpu)
        return first, report

    mols = [Chem.MolFromSmiles(oligomer_smiles[c.candidate_id]) for c in pool]
    backend, report = get_backend(prefer_gpu)
    fps = backend.morgan_fingerprints(mols, radius=DEFAULT_RADIUS, n_bits=DEFAULT_N_BITS)
    sim = backend.bulk_tanimoto(fps)

    keep: list[int] = []
    dropped: list[tuple[str, str, float]] = []
    for i in range(len(pool)):
        duplicate_of = None
        for j in keep:
            if sim[i, j] >= threshold:
                duplicate_of = j
                break
        if duplicate_of is None:
            keep.append(i)
        else:
            dropped.append(
                (
                    pool[duplicate_of].candidate_id,
                    pool[i].candidate_id,
                    float(sim[i, duplicate_of]),
                )
            )

    result = DedupResult(
        unique=[pool[i] for i in keep],
        duplicate_groups=first.duplicate_groups,
        near_duplicate_pairs=dropped,
        similarity_threshold=threshold,
    )
    return result, report


def _butina_clusters(distance: np.ndarray, cutoff: float) -> list[list[int]]:
    """Butina clustering on a dense distance matrix.

    Implemented here so clustering works without optional dependencies; the
    nvMolKit path benchmarks against this.
    """
    n = distance.shape[0]
    neighbours = [np.flatnonzero(distance[i] <= cutoff).tolist() for i in range(n)]
    order = sorted(range(n), key=lambda i: -len(neighbours[i]))
    assigned = np.zeros(n, dtype=bool)
    clusters: list[list[int]] = []
    for centroid in order:
        if assigned[centroid]:
            continue
        members = [centroid]
        assigned[centroid] = True
        for nb in neighbours[centroid]:
            if not assigned[nb]:
                assigned[nb] = True
                members.append(nb)
        clusters.append(members)
    return clusters


def diversity_report(
    candidates: list[PolymerCandidate],
    oligomer_smiles: dict[str, str],
    *,
    cluster_cutoff: float = 0.35,
    prefer_gpu: bool = False,
) -> DiversityReport:
    """Quantify structural diversity of a candidate set."""
    pool = [c for c in candidates if c.candidate_id in oligomer_smiles]
    fams = sorted({f for c in pool for f in c.families})

    if len(pool) < 2:
        return DiversityReport(
            n_candidates=len(pool),
            mean_pairwise_distance=0.0,
            min_pairwise_distance=0.0,
            median_pairwise_distance=0.0,
            n_clusters=len(pool),
            cluster_cutoff=cluster_cutoff,
            largest_cluster_size=len(pool),
            n_singletons=len(pool),
            n_families=len(fams),
            families=fams,
            backend={"actual": "none", "note": "fewer than two candidates"},
        )

    mols = [Chem.MolFromSmiles(oligomer_smiles[c.candidate_id]) for c in pool]
    backend, report = get_backend(prefer_gpu)
    fps = backend.morgan_fingerprints(mols)
    sim = backend.bulk_tanimoto(fps)
    dist = 1.0 - sim

    iu = np.triu_indices(len(pool), k=1)
    upper = dist[iu]
    clusters = _butina_clusters(dist, cluster_cutoff)

    return DiversityReport(
        n_candidates=len(pool),
        mean_pairwise_distance=float(upper.mean()),
        min_pairwise_distance=float(upper.min()),
        median_pairwise_distance=float(np.median(upper)),
        n_clusters=len(clusters),
        cluster_cutoff=cluster_cutoff,
        largest_cluster_size=max(len(c) for c in clusters),
        n_singletons=sum(1 for c in clusters if len(c) == 1),
        n_families=len(fams),
        families=fams,
        backend=report.to_dict(),
    )


def select_diverse(
    candidates: list[PolymerCandidate],
    oligomer_smiles: dict[str, str],
    k: int,
    *,
    prefer_gpu: bool = False,
    seed: int = 20260101,
) -> tuple[list[PolymerCandidate], dict[str, object]]:
    """MaxMin selection of ``k`` structurally spread candidates.

    This is the pre-model experiment-selection strategy. It is deterministic
    given ``seed`` and makes no performance claim about what it picks.
    """
    pool = [c for c in candidates if c.candidate_id in oligomer_smiles]
    if k >= len(pool):
        return pool, {
            "method": "maxmin-tanimoto",
            "note": f"requested {k} of {len(pool)} available; returned all",
            "selected": [c.candidate_id for c in pool],
        }
    if k <= 0:
        return [], {"method": "maxmin-tanimoto", "selected": []}

    mols = [Chem.MolFromSmiles(oligomer_smiles[c.candidate_id]) for c in pool]
    backend, report = get_backend(prefer_gpu)
    fps = backend.morgan_fingerprints(mols)
    sim = backend.bulk_tanimoto(fps)
    dist = 1.0 - sim

    # Deterministic seed point: the candidate furthest from the set centroid,
    # ties broken by candidate_id so the result never depends on input order.
    mean_dist = dist.mean(axis=1)
    best = max(range(len(pool)), key=lambda i: (mean_dist[i], pool[i].candidate_id))
    chosen = [best]
    min_dist = dist[best].copy()

    while len(chosen) < k:
        min_dist_masked = min_dist.copy()
        min_dist_masked[chosen] = -1.0
        nxt = max(
            range(len(pool)),
            key=lambda i: (min_dist_masked[i], pool[i].candidate_id),
        )
        chosen.append(nxt)
        min_dist = np.minimum(min_dist, dist[nxt])

    selected = [pool[i] for i in chosen]
    detail = {
        "method": "maxmin-tanimoto",
        "rationale": (
            "No trained model is available, so selection maximises structural "
            "spread rather than predicted performance. This is an information "
            "argument, not a performance claim."
        ),
        "k": k,
        "n_pool": len(pool),
        "seed": seed,
        "backend": report.to_dict(),
        "selected": [c.candidate_id for c in selected],
        "min_pairwise_distance_in_selection": round(
            float(
                min(
                    dist[a, b]
                    for ai, a in enumerate(chosen)
                    for b in chosen[ai + 1 :]
                )
            ),
            4,
        )
        if len(chosen) > 1
        else None,
    }
    return selected, detail

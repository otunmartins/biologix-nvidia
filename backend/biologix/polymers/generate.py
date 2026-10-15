"""Candidate generation.

Two generators share one result type so that baseline and GenMol-assisted runs
can be compared directly: :class:`BaselineGenerator` here, and
:class:`biologix.nvidia.genmol.GenMolGenerator`. Every proposal is recorded
with its outcome, so a generator's real yield is observable instead of
inferred from the accepted set.

Generated candidates are *hypotheses*. Nothing in this module computes or
predicts stability.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, field

from rdkit import Chem

from biologix import EVIDENCE_HYPOTHESIS
from biologix.polymers.library import SEED_LIBRARY, LibraryEntry
from biologix.polymers.models import (
    ChainLength,
    CopolymerKind,
    EndGroup,
    MonomerBlock,
    PolymerCandidate,
    RepeatUnit,
)
from biologix.polymers.rules import (
    PolymerizationRules,
    RejectionCode,
    validate_repeat_unit,
)
from biologix.polymers.synthesis import assess_route
from biologix.provenance import RunManifest


@dataclass
class GenerationRecord:
    """One proposal and what became of it."""

    proposed_smiles: str
    source: str
    accepted: bool
    canonical_smiles: str | None = None
    rejection_codes: list[str] = field(default_factory=list)
    rejection_detail: str | None = None
    cautions: list[str] = field(default_factory=list)
    # Set when the proposal was valid but duplicated an earlier one.
    duplicate_of: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "proposed_smiles": self.proposed_smiles,
            "source": self.source,
            "accepted": self.accepted,
            "canonical_smiles": self.canonical_smiles,
            "rejection_codes": self.rejection_codes,
            "rejection_detail": self.rejection_detail,
            "cautions": self.cautions,
            "duplicate_of": self.duplicate_of,
        }


@dataclass
class GenerationResult:
    """Everything a generation run produced, including what it threw away."""

    candidates: list[PolymerCandidate]
    records: list[GenerationRecord]
    generator: str
    manifest: RunManifest
    #: Canonical repeat-unit SMILES -> the assessed synthesis route for it.
    #: A candidate without an entry here has no route on record, which is
    #: itself worth showing rather than papering over.
    routes: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def n_proposed(self) -> int:
        return len(self.records)

    @property
    def n_accepted(self) -> int:
        return sum(1 for r in self.records if r.accepted)

    @property
    def n_rejected(self) -> int:
        return sum(1 for r in self.records if not r.accepted and not r.duplicate_of)

    @property
    def n_duplicate(self) -> int:
        return sum(1 for r in self.records if r.duplicate_of)

    @property
    def validity_rate(self) -> float:
        """Fraction of proposals that passed polymerisation rules.

        Duplicates count as valid: they were chemically fine, just not new.
        """
        if not self.records:
            return 0.0
        valid = sum(1 for r in self.records if r.accepted or r.duplicate_of)
        return valid / len(self.records)

    @property
    def uniqueness_rate(self) -> float:
        """Fraction of *valid* proposals that were structurally new."""
        valid = sum(1 for r in self.records if r.accepted or r.duplicate_of)
        if not valid:
            return 0.0
        return self.n_accepted / valid

    def rejection_histogram(self) -> dict[str, int]:
        hist: dict[str, int] = {}
        for r in self.records:
            for code in r.rejection_codes:
                hist[code] = hist.get(code, 0) + 1
        return dict(sorted(hist.items(), key=lambda kv: -kv[1]))

    def stats(self) -> dict[str, object]:
        return {
            "generator": self.generator,
            "n_proposed": self.n_proposed,
            "n_accepted": self.n_accepted,
            "n_rejected": self.n_rejected,
            "n_duplicate": self.n_duplicate,
            "validity_rate": round(self.validity_rate, 4),
            "uniqueness_rate": round(self.uniqueness_rate, 4),
            "rejection_histogram": self.rejection_histogram(),
        }


# Pendant groups grafted onto a vinyl backbone to extend the seed library
# beyond hand-curated entries. Each is a complete side chain.
_PENDANTS: tuple[tuple[str, str], ...] = (
    ("hydroxyl", "O"),
    ("methoxy", "OC"),
    ("hydroxyethyl", "OCCO"),
    ("carboxamide", "C(N)=O"),
    ("N-methylamide", "C(=O)NC"),
    ("N,N-dimethylamide", "C(=O)N(C)C"),
    ("hydroxyethylamide", "C(=O)NCCO"),
    ("morpholinoamide", "C(=O)N1CCOCC1"),
    ("pyrrolidinone", "N1CCCC1=O"),
    ("methylsulfonyl", "S(=O)(=O)C"),
    ("imidazolyl", "n1ccnc1"),
    ("methyl ester", "C(=O)OC"),
    ("hydroxyethyl ester", "C(=O)OCCO"),
    ("dimethylaminoethyl ester", "C(=O)OCCN(C)C"),
)

# Backbone templates with a ``{R}`` slot for the pendant group.
_BACKBONES: tuple[tuple[str, str], ...] = (
    ("vinyl", "[*]CC([*]){R}"),
    ("vinyl-methyl", "[*]CC([*])(C){R}"),
    ("ethylene-spaced vinyl", "[*]CCC([*]){R}"),
)


class BaselineGenerator:
    """Deterministic, model-free generator.

    This is the comparison baseline for every learned generator. It is fully
    reproducible from ``(seed, n, options)`` and uses no network, no GPU and
    no model weights, so a GenMol run that cannot beat it has not earned its
    dependency.
    """

    name = "baseline-enumerative"
    version = "1.0"

    def __init__(
        self,
        *,
        seed: int = 20260101,
        rules: PolymerizationRules | None = None,
        library: Iterable[LibraryEntry] = SEED_LIBRARY,
    ) -> None:
        self.seed = seed
        self.rules = rules or PolymerizationRules()
        self.library = tuple(library)

    def _proposals(self) -> list[tuple[str, str, str | None, str | None]]:
        """Ordered proposal pool: ``(smiles, origin, name, family)``.

        The order is deterministic; a seeded shuffle then selects from it, so
        the same seed always yields the same candidate set.
        """
        pool: list[tuple[str, str, str | None, str | None]] = []
        for entry in self.library:
            pool.append((entry.smiles, "library", entry.name, entry.family))
        for bb_name, template in _BACKBONES:
            for p_name, pendant in _PENDANTS:
                smiles = template.replace("{R}", pendant)
                family = f"enumerated-{bb_name}"
                pool.append((smiles, "enumerated", f"{bb_name}-{p_name}", family))
        return pool

    def generate(
        self,
        n: int = 24,
        *,
        dp_choices: tuple[int, ...] = (10, 25, 50),
        end_groups: tuple[EndGroup, ...] = (EndGroup.METHYL,),
        copolymer_fraction: float = 0.25,
        generation_round: int = 0,
    ) -> GenerationResult:
        """Produce up to ``n`` distinct candidates.

        ``copolymer_fraction`` of the output is two-block copolymers drawn
        from accepted units, which is how composition space gets explored
        without a model.
        """
        manifest = RunManifest(
            operation="generate-baseline",
            evidence=EVIDENCE_HYPOTHESIS,
            parameters={
                "n_requested": n,
                "dp_choices": list(dp_choices),
                "end_groups": [str(e) for e in end_groups],
                "copolymer_fraction": copolymer_fraction,
                "generator": self.name,
                "generator_version": self.version,
                "library_size": len(self.library),
                "rules": {
                    "allowed_elements": sorted(self.rules.allowed_elements),
                    "required_attachment_points": self.rules.required_attachment_points,
                    "max_repeat_mw": self.rules.max_repeat_mw,
                    "min_repeat_mw": self.rules.min_repeat_mw,
                },
            },
            seeds={"generator": self.seed},
        )
        manifest.record_model(
            self.name, self.version, kind="rule-based-generator", licence="Apache-2.0 (this repo)"
        )

        rng = random.Random(self.seed)
        pool = self._proposals()
        rng.shuffle(pool)

        records: list[GenerationRecord] = []
        units: list[RepeatUnit] = []
        seen_canonical: dict[str, str] = {}

        for smiles, origin, name, family in pool:
            result = validate_repeat_unit(smiles, self.rules)
            record = GenerationRecord(
                proposed_smiles=smiles,
                source=f"{self.name}:{origin}",
                accepted=False,
                canonical_smiles=result.canonical_smiles,
                cautions=list(result.cautions),
            )
            if not result.valid:
                record.rejection_codes = result.reason_codes
                record.rejection_detail = result.summary
                records.append(record)
                manifest.exclude(smiles, result.summary, stage="repeat-unit-validation")
                continue

            canonical = result.canonical_smiles or smiles
            if canonical in seen_canonical:
                record.duplicate_of = seen_canonical[canonical]
                records.append(record)
                continue

            seen_canonical[canonical] = canonical
            record.accepted = True
            records.append(record)
            units.append(
                RepeatUnit(smiles=canonical, name=name, family=family, source="baseline")
            )

        # Assemble candidates: homopolymers first, then copolymers, both
        # drawn deterministically.
        candidates: list[PolymerCandidate] = []
        emitted: set[str] = set()

        n_copoly = int(round(n * copolymer_fraction)) if len(units) >= 2 else 0
        n_homo = max(n - n_copoly, 0)

        for unit in units:
            if len(candidates) >= n_homo:
                break
            dp = dp_choices[len(candidates) % len(dp_choices)]
            eg = end_groups[len(candidates) % len(end_groups)]
            cand = self._make_homopolymer(unit, dp, eg, generation_round)
            if cand is not None and cand.candidate_id not in emitted:
                emitted.add(cand.candidate_id)
                candidates.append(cand)

        if n_copoly and len(units) >= 2:
            pairs = [(a, b) for i, a in enumerate(units) for b in units[i + 1 :]]
            rng.shuffle(pairs)
            for a, b in pairs:
                if len(candidates) >= n:
                    break
                dp = dp_choices[len(candidates) % len(dp_choices)]
                eg = end_groups[len(candidates) % len(end_groups)]
                fraction = rng.choice((0.25, 0.5, 0.75))
                kind = rng.choice((CopolymerKind.BLOCK, CopolymerKind.RANDOM))
                cand = self._make_copolymer(a, b, fraction, dp, eg, kind, generation_round)
                if cand is not None and cand.candidate_id not in emitted:
                    emitted.add(cand.candidate_id)
                    candidates.append(cand)

        manifest.finish(
            n_proposed=len(records),
            n_accepted_units=len(units),
            n_candidates=len(candidates),
            rejection_histogram={
                code: sum(1 for r in records for c in r.rejection_codes if c == code)
                for code in {c for r in records for c in r.rejection_codes}
            },
        )
        return GenerationResult(
            candidates=candidates,
            records=records,
            generator=self.name,
            manifest=manifest,
            routes=assess_candidate_routes(candidates),
        )

    def _make_homopolymer(
        self, unit: RepeatUnit, dp: int, end_group: EndGroup, generation_round: int
    ) -> PolymerCandidate | None:
        try:
            return PolymerCandidate(
                blocks=(MonomerBlock(unit=unit, mole_fraction=1.0),),
                chain_length=ChainLength(degree_of_polymerisation=dp),
                end_group=end_group,
                copolymer_kind=CopolymerKind.HOMOPOLYMER,
                generator=f"{self.name}/{self.version}",
                generation_round=generation_round,
            )
        except ValueError:
            return None

    def _make_copolymer(
        self,
        a: RepeatUnit,
        b: RepeatUnit,
        fraction_a: float,
        dp: int,
        end_group: EndGroup,
        kind: CopolymerKind,
        generation_round: int,
    ) -> PolymerCandidate | None:
        try:
            return PolymerCandidate(
                blocks=(
                    MonomerBlock(unit=a, mole_fraction=fraction_a),
                    MonomerBlock(unit=b, mole_fraction=round(1.0 - fraction_a, 6)),
                ),
                chain_length=ChainLength(degree_of_polymerisation=dp),
                end_group=end_group,
                copolymer_kind=kind,
                generator=f"{self.name}/{self.version}",
                generation_round=generation_round,
            )
        except ValueError:
            return None


def candidates_from_smiles(
    smiles_list: Iterable[str],
    *,
    dp: int = 25,
    end_group: EndGroup = EndGroup.METHYL,
    source: str = "imported",
    rules: PolymerizationRules | None = None,
) -> GenerationResult:
    """Turn a list of repeat-unit SMILES into validated candidates.

    Used by the import path and by the GenMol adapter after conversion, so
    that externally sourced structures go through exactly the same gate as
    generated ones.
    """
    rules = rules or PolymerizationRules()
    manifest = RunManifest(
        operation="candidates-from-smiles",
        evidence=EVIDENCE_HYPOTHESIS,
        parameters={"dp": dp, "end_group": str(end_group), "source": source},
    )
    records: list[GenerationRecord] = []
    candidates: list[PolymerCandidate] = []
    seen: dict[str, str] = {}

    for smiles in smiles_list:
        result = validate_repeat_unit(smiles, rules)
        record = GenerationRecord(
            proposed_smiles=smiles,
            source=source,
            accepted=False,
            canonical_smiles=result.canonical_smiles,
            cautions=list(result.cautions),
        )
        if not result.valid:
            record.rejection_codes = result.reason_codes
            record.rejection_detail = result.summary
            records.append(record)
            manifest.exclude(smiles, result.summary, stage="repeat-unit-validation")
            continue
        canonical = result.canonical_smiles or smiles
        if canonical in seen:
            record.duplicate_of = seen[canonical]
            records.append(record)
            continue
        seen[canonical] = canonical
        record.accepted = True
        records.append(record)
        try:
            candidates.append(
                PolymerCandidate(
                    blocks=(
                        MonomerBlock(
                            unit=RepeatUnit(smiles=canonical, source=source), mole_fraction=1.0
                        ),
                    ),
                    chain_length=ChainLength(degree_of_polymerisation=dp),
                    end_group=end_group,
                    generator=source,
                )
            )
        except ValueError as exc:
            record.accepted = False
            record.rejection_codes = [str(RejectionCode.SANITIZATION_FAILED)]
            record.rejection_detail = str(exc)

    manifest.finish(n_proposed=len(records), n_candidates=len(candidates))
    return GenerationResult(
        candidates=candidates,
        records=records,
        generator=source,
        manifest=manifest,
        routes=assess_candidate_routes(candidates),
    )


def assess_candidate_routes(
    candidates: list[PolymerCandidate],
) -> dict[str, dict[str, object]]:
    """A synthesis route for every repeat unit in ``candidates``.

    Applied to hand-curated and baseline units as well as generated ones: a
    seed entry someone wrote by hand is still a claim that the chemistry works,
    and it earns the same scrutiny. The mechanism is inferred from the unit's
    own structure, which is why the assessment is run per unit rather than
    taken from the library entry's label.
    """

    routes: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        for block in candidate.blocks:
            smiles = block.unit.smiles
            if smiles in routes:
                continue
            # Which mechanism would have produced this unit: ask the converter
            # what it finds in the monomer the unit implies, rather than
            # guessing from the family name.
            best: dict[str, object] | None = None
            for mechanism in _plausible_mechanisms(smiles):
                route = assess_route(smiles, mechanism)
                if route.defensible:
                    best = {
                        "repeat_unit": smiles,
                        "mechanism": str(mechanism),
                        "derivation": "curated",
                        "label": str(mechanism),
                        "route": route.to_dict(),
                    }
                    break
                if best is None:
                    best = {
                        "repeat_unit": smiles,
                        "mechanism": str(mechanism),
                        "derivation": "curated",
                        "label": str(mechanism),
                        "route": route.to_dict(),
                    }
            if best is not None:
                routes[smiles] = best
    return routes


def _plausible_mechanisms(repeat_smiles: str) -> list[str]:
    """Mechanisms worth testing for a given unit, cheapest signal first."""
    from biologix.polymers.monomer_to_repeat import Mechanism

    mol = Chem.MolFromSmiles(repeat_smiles)
    if mol is None:
        return [str(Mechanism.CHAIN_GROWTH_VINYL)]
    attachments = [a for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    neighbours = [n for a in attachments for n in a.GetNeighbors()]
    elements = {n.GetSymbol() for n in neighbours}
    ordered: list[str] = []
    if elements <= {"C"}:
        ordered.append(str(Mechanism.CHAIN_GROWTH_VINYL))
    if "O" in elements:
        ordered += [
            str(Mechanism.STEP_GROWTH_DIOL),
            str(Mechanism.RING_OPENING_EPOXIDE),
            str(Mechanism.RING_OPENING_LACTONE),
        ]
    if "N" in elements:
        ordered += [str(Mechanism.STEP_GROWTH_DIAMINE), str(Mechanism.RING_OPENING_LACTAM)]
    if "C" in elements and not ordered:
        ordered.append(str(Mechanism.CHAIN_GROWTH_VINYL))
    return ordered or [str(Mechanism.CHAIN_GROWTH_VINYL)]

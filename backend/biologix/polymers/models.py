"""Polymer representation.

A polymer here is *not* a molecule. It is a specification from which many
molecules could be instantiated: a set of repeat units, a composition, an
architecture, a chain-length distribution. Code that needs an ordinary
molecule (fingerprints, conformers, simulation) must build an explicit capped
oligomer via :mod:`biologix.polymers.oligomer`, and the construction rules get
recorded so the resulting numbers can be interpreted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from biologix.provenance import stable_hash

# Attachment-point token used in repeat-unit SMILES. RDKit parses ``[*]`` as a
# wildcard/dummy atom with atomic number 0, which is what lets us validate
# valence at the connection points before building anything.
ATTACHMENT_TOKEN = "[*]"


class Architecture(StrEnum):
    """Chain topology. Only the values here are supported by the builders."""

    LINEAR = "linear"
    # Supported for metadata and as a labelled construction rule; the oligomer
    # builder emits a linear backbone with grafted side chains.
    GRAFT = "graft"
    # Recorded but NOT built: no construction rule is implemented, so
    # requesting one is an explicit error rather than a silent linear build.
    STAR = "star"
    BRANCHED = "branched"
    CROSSLINKED = "crosslinked"

    @property
    def buildable(self) -> bool:
        return self in (Architecture.LINEAR, Architecture.GRAFT)


class CopolymerKind(StrEnum):
    HOMOPOLYMER = "homopolymer"
    RANDOM = "random"
    ALTERNATING = "alternating"
    BLOCK = "block"


class EndGroup(StrEnum):
    """Cap chemistry applied to oligomer termini.

    The cap is part of the molecule that gets simulated, so it is recorded
    explicitly rather than assumed to be hydrogen.
    """

    HYDROGEN = "hydrogen"
    METHYL = "methyl"
    HYDROXYL = "hydroxyl"
    ACETYL = "acetyl"

    @property
    def smiles_fragment(self) -> str:
        return {
            EndGroup.HYDROGEN: "[H]",
            EndGroup.METHYL: "C",
            EndGroup.HYDROXYL: "O",
            EndGroup.ACETYL: "C(C)=O",
        }[self]


class RepeatUnit(BaseModel):
    """One repeat unit, as SMILES carrying attachment points.

    ``smiles`` must contain exactly two ``[*]`` dummy atoms for a linear
    backbone unit: one head, one tail. Validation lives in
    :mod:`biologix.polymers.rules` so that it can be applied uniformly to
    units from any source (hand-written, baseline generator, GenMol).
    """

    model_config = ConfigDict(frozen=True)

    smiles: str = Field(description="Repeat-unit SMILES with [*] attachment points")
    name: str | None = None
    # Free-text family label used for grouped evaluation splits, so that a
    # model is never scored on a polymer family it was trained on.
    family: str | None = None
    source: str = Field(
        default="manual",
        description="Where this unit came from: manual | baseline | genmol | imported",
    )

    @field_validator("smiles")
    @classmethod
    def _canonicalise(cls, v: str) -> str:
        """Normalise to canonical SMILES so identity is direction-independent.

        ``[*]CCO[*]`` and ``[*]OCC[*]`` are the same repeat unit read from
        opposite ends; both are the same molecular graph, so RDKit's canonical
        form collapses them and :attr:`PolymerCandidate.candidate_id`
        deduplicates correctly.

        Invalid SMILES are passed through unchanged: reporting *why* a unit is
        invalid belongs to :mod:`biologix.polymers.rules`, not here.
        """
        v = v.strip()
        if not v:
            raise ValueError("repeat-unit SMILES must not be empty")
        from rdkit import Chem  # local import keeps model import cheap

        mol = Chem.MolFromSmiles(v, sanitize=False)
        if mol is None:
            return v
        try:
            Chem.SanitizeMol(mol)
        except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
            return v
        return Chem.MolToSmiles(mol)

    @property
    def n_attachment_points(self) -> int:
        """Count of dummy atoms, by token.

        Note this is a syntactic count; :mod:`biologix.polymers.rules` does
        the authoritative count after RDKit parsing, which also catches
        ``[1*]``-style labelled dummies.
        """
        return self.smiles.count("*")


class MonomerBlock(BaseModel):
    """A repeat unit plus its share of the chain."""

    model_config = ConfigDict(frozen=True)

    unit: RepeatUnit
    mole_fraction: float = Field(ge=0.0, le=1.0, default=1.0)


class ChainLength(BaseModel):
    """Degree of polymerisation and derived molecular-weight metadata.

    ``degree_of_polymerisation`` is the number-average DP. ``dispersity`` is
    Mw/Mn when known; it is recorded metadata and is NOT represented in any
    simulation, which uses a single explicit oligomer length.
    """

    model_config = ConfigDict(frozen=True)

    degree_of_polymerisation: int = Field(ge=1, le=10_000)
    dispersity: float | None = Field(default=None, ge=1.0)
    # Molecular weight in g/mol; computed by the oligomer builder when not given.
    number_average_mw: float | None = Field(default=None, gt=0)
    weight_average_mw: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _mw_consistency(self) -> ChainLength:
        if (
            self.number_average_mw is not None
            and self.weight_average_mw is not None
            and self.weight_average_mw < self.number_average_mw
        ):
            raise ValueError("weight_average_mw must be >= number_average_mw")
        return self


class PolymerCandidate(BaseModel):
    """A candidate polymer excipient.

    Identity (:attr:`candidate_id`) is derived from the structural
    specification only — not from the provenance fields — so the same polymer
    proposed by two different generators deduplicates to one candidate while
    keeping both provenance records.
    """

    model_config = ConfigDict(frozen=True)

    blocks: tuple[MonomerBlock, ...]
    architecture: Architecture = Architecture.LINEAR
    copolymer_kind: CopolymerKind = CopolymerKind.HOMOPOLYMER
    chain_length: ChainLength
    end_group: EndGroup = EndGroup.METHYL

    # Provenance, excluded from identity.
    generator: str = "manual"
    generation_round: int = 0
    parent_id: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_composition(self) -> PolymerCandidate:
        if not self.blocks:
            raise ValueError("a candidate needs at least one monomer block")
        total = sum(b.mole_fraction for b in self.blocks)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"mole fractions must sum to 1.0, got {total:.6f}")
        if len(self.blocks) == 1 and self.copolymer_kind is not CopolymerKind.HOMOPOLYMER:
            raise ValueError(
                f"copolymer_kind={self.copolymer_kind} requires >1 block; "
                "a single block is a homopolymer"
            )
        if len(self.blocks) > 1 and self.copolymer_kind is CopolymerKind.HOMOPOLYMER:
            raise ValueError("more than one block cannot be a homopolymer")
        return self

    @property
    def structural_key(self) -> dict[str, Any]:
        """The fields that define chemical identity."""
        return {
            "blocks": sorted(
                ({"smiles": b.unit.smiles, "f": round(b.mole_fraction, 6)} for b in self.blocks),
                key=lambda d: d["smiles"],
            ),
            "architecture": str(self.architecture),
            "copolymer_kind": str(self.copolymer_kind),
            "dp": self.chain_length.degree_of_polymerisation,
            "end_group": str(self.end_group),
        }

    @property
    def candidate_id(self) -> str:
        return stable_hash(self.structural_key, length=16)

    @property
    def is_homopolymer(self) -> bool:
        return len(self.blocks) == 1

    @property
    def families(self) -> tuple[str, ...]:
        """Family labels present in this candidate, for grouped splits."""
        return tuple(sorted({b.unit.family for b in self.blocks if b.unit.family}))

    def display_name(self) -> str:
        names = [b.unit.name or b.unit.smiles for b in self.blocks]
        dp = self.chain_length.degree_of_polymerisation
        if self.is_homopolymer:
            return f"poly({names[0]})·DP{dp}"
        joiner = {
            CopolymerKind.BLOCK: "-b-",
            CopolymerKind.RANDOM: "-r-",
            CopolymerKind.ALTERNATING: "-alt-",
        }.get(self.copolymer_kind, "-co-")
        return f"poly({joiner.join(names)})·DP{dp}"


class FormulationConditions(BaseModel):
    """Formulation state.

    Crucially, :attr:`modelled_fields` reports which of these are actually
    represented in a given simulation. Most are recorded metadata only. Any
    report that shows a simulation result alongside these conditions must also
    show that distinction — see :mod:`biologix.simulation.run`.
    """

    model_config = ConfigDict(frozen=True)

    polymer_to_biologic_ratio: float = Field(
        default=1.0, gt=0, description="Molar ratio of polymer chains to biologic molecules"
    )
    polymer_concentration_mg_ml: float | None = Field(default=None, gt=0)
    biologic_concentration_mg_ml: float | None = Field(default=None, gt=0)
    temperature_k: float = Field(default=298.15, gt=0, le=500)
    ph: float | None = Field(default=7.4, ge=0, le=14)
    buffer_name: str | None = "phosphate"
    buffer_concentration_mm: float | None = Field(default=10.0, ge=0)
    ionic_strength_mm: float | None = Field(default=150.0, ge=0)
    excipients: tuple[str, ...] = ()
    container: str | None = None

    def modelled_fields(self, *, solvent: str, ph_method: str | None = None) -> dict[str, str]:
        """Map each condition to how (or whether) the simulation represents it.

        ``solvent`` is the solvent model actually used ("implicit-obc2",
        "explicit-tip3p", "vacuum").
        """
        implicit = solvent.startswith("implicit")
        explicit = solvent.startswith("explicit")
        return {
            "temperature_k": "modelled: thermostat set point",
            "ionic_strength_mm": (
                "modelled: Debye screening in implicit solvent"
                if implicit
                else "modelled: explicit counter-ions"
                if explicit
                else "not modelled: vacuum"
            ),
            "ph": (
                f"partially modelled: fixed protonation states assigned at pH "
                f"{self.ph} via {ph_method}"
                if ph_method
                else "not modelled: protonation states fixed, no titration"
            ),
            "buffer_name": "not modelled: recorded metadata only",
            "buffer_concentration_mm": "not modelled: recorded metadata only",
            "polymer_to_biologic_ratio": (
                "not modelled: simulation uses a fixed small number of chains, "
                "not the formulation ratio"
            ),
            "polymer_concentration_mg_ml": "not modelled: recorded metadata only",
            "biologic_concentration_mg_ml": "not modelled: recorded metadata only",
            "excipients": "not modelled: recorded metadata only",
            "container": "not modelled: recorded metadata only",
        }

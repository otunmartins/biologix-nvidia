"""Biologic target representation.

Multi-chain and chemically modified biologics are first-class. Chain
boundaries are never flattened into one sequence, because a model that is
handed ``chainA + chainB`` as a single string is being told something false
about the molecule. Chemical modifications are stored separately from the
amino-acid sequence, since a sequence model cannot see them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# One-letter codes accepted in a sequence. 'X' is allowed as an explicit
# unknown; 'U' (selenocysteine) and 'O' (pyrrolysine) are included because
# they occur in engineered constructs.
_AA_ALPHABET = set("ACDEFGHIKLMNPQRSTVWYXUO")

_THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    # Common variants that map to a standard residue for sequence purposes.
    "HID": "H", "HIE": "H", "HIP": "H", "CYX": "C", "CYM": "C",
    "MSE": "M", "SEC": "U", "PYL": "O",
}


class BiologicClass(StrEnum):
    PEPTIDE = "peptide"
    PROTEIN = "protein"
    MONOCLONAL_ANTIBODY = "mab"
    FUSION_PROTEIN = "fusion"
    ENZYME = "enzyme"
    OTHER = "other"


class ModificationKind(StrEnum):
    """Chemical modifications that a sequence model cannot represent."""

    DISULFIDE = "disulfide"
    PEGYLATION = "pegylation"
    ACYLATION = "acylation"
    GLYCOSYLATION = "glycosylation"
    AMIDATION = "amidation"
    ACETYLATION = "acetylation"
    PHOSPHORYLATION = "phosphorylation"
    METAL_COORDINATION = "metal_coordination"
    NON_NATURAL_RESIDUE = "non_natural_residue"
    OTHER = "other"


class Modification(BaseModel):
    """A chemical modification, located by chain and residue number.

    Stored separately from :attr:`Chain.sequence` so that embedding adapters
    can declare honestly that they did not see it.
    """

    model_config = ConfigDict(frozen=True)

    kind: ModificationKind
    # (chain_id, residue_number) pairs the modification involves. A disulfide
    # has two sites; an acylation has one.
    sites: tuple[tuple[str, int], ...]
    description: str | None = None
    # SMILES of the attached chemistry when applicable (PEG chain, fatty acid).
    attached_smiles: str | None = None

    @model_validator(mode="after")
    def _check_sites(self) -> Modification:
        if not self.sites:
            raise ValueError("a modification must name at least one site")
        if self.kind is ModificationKind.DISULFIDE and len(self.sites) != 2:
            raise ValueError("a disulfide links exactly two sites")
        return self

    def describe(self) -> str:
        where = ", ".join(f"{c}{n}" for c, n in self.sites)
        return f"{self.kind} at {where}" + (f" ({self.description})" if self.description else "")


class Chain(BaseModel):
    """One polypeptide chain."""

    model_config = ConfigDict(frozen=True)

    chain_id: str = Field(min_length=1, max_length=4)
    sequence: str = Field(min_length=1)
    # Residue number of the first residue, for mapping modifications onto
    # structures that do not start at 1.
    first_residue_number: int = 1
    description: str | None = None

    @field_validator("sequence")
    @classmethod
    def _check_sequence(cls, v: str) -> str:
        v = "".join(v.split()).upper()
        bad = sorted(set(v) - _AA_ALPHABET)
        if bad:
            raise ValueError(
                f"sequence contains characters outside the accepted alphabet: "
                f"{', '.join(bad)}. Accepted: {''.join(sorted(_AA_ALPHABET))}"
            )
        return v

    @property
    def length(self) -> int:
        return len(self.sequence)

    def residue_at(self, number: int) -> str:
        idx = number - self.first_residue_number
        if idx < 0 or idx >= len(self.sequence):
            last = self.first_residue_number + len(self.sequence) - 1
            raise IndexError(
                f"residue {number} is outside chain {self.chain_id} "
                f"({self.first_residue_number}..{last})"
            )
        return self.sequence[idx]


class Biologic(BaseModel):
    """A biologic drug target.

    Interfaces take a :class:`Biologic`, never a bare sequence string, so that
    adding an antibody later is a data change rather than a code change.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    biologic_class: BiologicClass = BiologicClass.PROTEIN
    chains: tuple[Chain, ...]
    modifications: tuple[Modification, ...] = ()

    # Structure provenance. ``pdb_id`` is a reference, not a bundled file;
    # see biologix.targets.prepare for fetching and hashing.
    pdb_id: str | None = None
    structure_source: str | None = None
    uniprot_id: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Biologic:
        if not self.chains:
            raise ValueError("a biologic needs at least one chain")
        ids = [c.chain_id for c in self.chains]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate chain ids: {ids}")
        known = {c.chain_id: c for c in self.chains}
        for mod in self.modifications:
            for chain_id, resnum in mod.sites:
                if chain_id not in known:
                    raise ValueError(
                        f"modification {mod.kind} references unknown chain {chain_id!r}"
                    )
                try:
                    known[chain_id].residue_at(resnum)
                except IndexError as exc:
                    raise ValueError(f"modification {mod.kind}: {exc}") from exc
        return self

    @property
    def total_residues(self) -> int:
        return sum(c.length for c in self.chains)

    @property
    def n_chains(self) -> int:
        return len(self.chains)

    @property
    def disulfides(self) -> tuple[Modification, ...]:
        return tuple(m for m in self.modifications if m.kind is ModificationKind.DISULFIDE)

    def chain(self, chain_id: str) -> Chain:
        for c in self.chains:
            if c.chain_id == chain_id:
                return c
        have = [c.chain_id for c in self.chains]
        raise KeyError(f"no chain {chain_id!r} in {self.name}; have {have}")

    def sequences_by_chain(self) -> dict[str, str]:
        """Per-chain sequences, which is how embedding adapters consume them.

        There is deliberately no ``concatenated_sequence`` helper: joining
        chains would invent a peptide bond that does not exist.
        """
        return {c.chain_id: c.sequence for c in self.chains}

    def modification_summary(self) -> list[str]:
        return [m.describe() for m in self.modifications]

    def unmodelled_chemistry(self) -> list[str]:
        """Modifications that a sequence-only model cannot represent.

        Returned so adapters and reports can state the gap explicitly.
        """
        return [
            m.describe()
            for m in self.modifications
            if m.kind is not ModificationKind.DISULFIDE
        ]


def three_letter_to_one(resname: str) -> str | None:
    """Map a PDB residue name to a one-letter code, or ``None`` if unknown."""
    return _THREE_TO_ONE.get(resname.strip().upper())

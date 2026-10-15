"""Getting an arbitrary biologic into the platform.

Three routes in, because that is how people actually hold a protein: as a
FASTA they paste, as a PDB entry, or as a UniProt accession.

What every route here guarantees:

* **Chains stay separate.** A multi-record FASTA becomes multiple chains, never
  one concatenated sequence. Concatenating two chains invents a peptide bond
  that does not exist, and every downstream model would then be answering a
  question about a different molecule.
* **Nothing is cleaned up silently.** Whitespace, digits and FASTA headers are
  structural noise and are stripped. An unexpected residue letter is a
  *refusal*, not a substitution — guessing that "B" meant "D" would change the
  protein.
* **Nothing is truncated.** A chain longer than a model's limit is kept whole
  and flagged; the limit is enforced later, by the adapter that has it.
* **Provenance is recorded.** Each import returns where the sequence came
  from, so a target can be re-derived rather than trusted.

This module makes no network call of its own except through
``fetch_structure`` (RCSB) and the UniProt REST endpoint, and both say so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from biologix.errors import ValidationFailure
from biologix.settings import Settings, get_settings
from biologix.targets.models import _AA_ALPHABET, Biologic, BiologicClass, Chain

UNIPROT_FASTA = "https://rest.uniprot.org/uniprotkb/{accession}.fasta"

# ESM-2 emits at most 1022 residue embeddings; longer chains are refused by the
# adapter rather than truncated, so a long chain is worth flagging at import.
ESM2_MAX_RESIDUES = 1022

# Letters that are legal in the model but mean "not a standard residue".
AMBIGUOUS = {"X", "U", "O"}

# UniProt's own documented accession pattern: the O/P/Q form and the newer
# A-N/R-Z form, including the ten-character extension.
_UNIPROT_ACCESSION = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]"
    r"|[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9](?:[A-Z][A-Z0-9]{2}[0-9])?)$"
)
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


@dataclass
class ImportedTarget:
    """Chains plus everything needed to judge whether they are usable."""

    name: str
    chains: list[Chain]
    source: str
    source_detail: str
    pdb_id: str | None = None
    uniprot_id: str | None = None
    notes: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def total_residues(self) -> int:
        return sum(chain.length for chain in self.chains)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "chains": [
                {
                    "chain_id": chain.chain_id,
                    "sequence": chain.sequence,
                    "length": chain.length,
                    "description": chain.description,
                }
                for chain in self.chains
            ],
            "n_chains": len(self.chains),
            "total_residues": self.total_residues,
            "source": self.source,
            "source_detail": self.source_detail,
            "pdb_id": self.pdb_id,
            "uniprot_id": self.uniprot_id,
            "notes": self.notes,
            "warnings": self.warnings,
        }


def _clean(sequence: str) -> str:
    """Strip structural noise, uppercase, and leave everything else alone."""
    return re.sub(r"[\s\d*\-]", "", sequence).upper()


def _validate(sequence: str, *, where: str) -> str:
    cleaned = _clean(sequence)
    if not cleaned:
        raise ValidationFailure(
            f"{where} has no residues",
            remedy="Paste one-letter amino acid sequence, optionally with FASTA headers.",
        )
    bad = sorted(set(cleaned) - _AA_ALPHABET)
    if bad:
        raise ValidationFailure(
            f"{where} contains {', '.join(bad)}, which is not a residue letter",
            field="sequence",
            remedy=(
                "Only one-letter amino acid codes are accepted "
                f"({''.join(sorted(_AA_ALPHABET))}). Nucleotide sequences, three-letter "
                "codes and modified-residue notation are not translated automatically, "
                "because guessing what a letter meant would change the protein."
            ),
        )
    return cleaned


def _chain_warnings(chains: list[Chain]) -> list[str]:
    warnings: list[str] = []
    for chain in chains:
        if chain.length > ESM2_MAX_RESIDUES:
            warnings.append(
                f"chain {chain.chain_id} is {chain.length} residues, over ESM-2's "
                f"{ESM2_MAX_RESIDUES}-residue ceiling. Embedding will refuse this chain "
                "rather than truncate it; everything else works."
            )
        ambiguous = sorted(set(chain.sequence) & AMBIGUOUS)
        if ambiguous:
            warnings.append(
                f"chain {chain.chain_id} contains {', '.join(ambiguous)} "
                "(non-standard or unknown residues), which is kept as written and "
                "carried into every model that reads this sequence."
            )
    return warnings


def parse_fasta(text: str, *, name: str | None = None) -> ImportedTarget:
    """Parse pasted FASTA, or a bare sequence, into chains.

    Each FASTA record becomes one chain. Chain ids come from the header when it
    carries something chain-shaped (``>Heavy chain``, ``>1ABC_A``), otherwise
    they are assigned A, B, C… in order.
    """
    if not text.strip():
        raise ValidationFailure("nothing was pasted", remedy="Paste a sequence or a FASTA record.")

    records: list[tuple[str | None, str]] = []
    header: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            if buffer:
                records.append((header, "".join(buffer)))
                buffer = []
            header = line[1:].strip() or None
        else:
            buffer.append(line)
    if buffer:
        records.append((header, "".join(buffer)))

    if not records:
        raise ValidationFailure("no sequence found", remedy="Paste a sequence or a FASTA record.")

    chains: list[Chain] = []
    used: set[str] = set()
    for index, (record_header, sequence) in enumerate(records):
        label = f"record {index + 1}" + (f" ({record_header})" if record_header else "")
        cleaned = _validate(sequence, where=label)
        chain_id = _chain_id_from(record_header, index, used)
        used.add(chain_id)
        chains.append(
            Chain(
                chain_id=chain_id,
                sequence=cleaned,
                description=record_header,
            )
        )

    # A single record's header describes the molecule, so it makes a decent
    # name. With several records each header describes a *chain*, so naming the
    # biologic after the first one would call an antibody "Heavy chain".
    single_header = (records[0][0] or "").strip() if len(records) == 1 else ""
    inferred = name or (single_header and readable_name(single_header)) or "pasted biologic"
    return ImportedTarget(
        name=inferred,
        chains=chains,
        source="pasted",
        source_detail=(
            f"{len(chains)} chain{'s' if len(chains) != 1 else ''} pasted as "
            f"{'FASTA' if any(h for h, _ in records) else 'raw sequence'} on "
            f"{datetime.now(UTC).date().isoformat()}"
        ),
        warnings=_chain_warnings(chains),
    )


def _chain_id_from(header: str | None, index: int, used: set[str]) -> str:
    """A chain id from the header if it offers one, else A, B, C…"""
    default = chr(ord("A") + index) if index < 26 else f"C{index + 1}"
    if header:
        # `1ABC_A` or `sp|P01308|INS_HUMAN Chain B` style hints.
        match = re.search(r"(?:chain[\s_:]*)([A-Za-z0-9]{1,4})\b", header, re.IGNORECASE)
        if not match:
            match = re.search(r"_([A-Za-z0-9]{1,4})\b", header)
        if match:
            candidate = match.group(1).upper()
            if candidate not in used:
                return candidate
    while default in used:
        default = chr(ord(default) + 1)
    return default


def from_pdb(pdb_id: str, *, name: str | None = None) -> ImportedTarget:
    """Read chains straight out of a PDB entry.

    The sequences come from the structure's own ATOM records, which is the
    honest source when the point of the import is to do structure-based work:
    what is in the file is what will be docked. Crystal structures routinely
    differ from the canonical sequence — missing termini, engineered mutations
    — and this route reports what the file actually contains rather than
    reconciling it with a database.
    """
    identifier = pdb_id.strip().upper()
    if not _PDB_ID.match(identifier):
        raise ValidationFailure(
            f"{pdb_id!r} is not a PDB id",
            field="pdb_id",
            remedy="A PDB id is four characters beginning with a digit, such as 3I40.",
        )

    from biologix.targets.prepare import fetch_structure, read_chain_sequences

    record = fetch_structure(identifier)
    sequences, hetero = read_chain_sequences(record.path)
    if not sequences:
        raise ValidationFailure(
            f"{identifier} has no protein chains this parser can read",
            remedy=(
                "Nucleic-acid-only and ligand-only entries have no amino acid chains. "
                "Check the entry on rcsb.org, or paste the sequence instead."
            ),
        )

    chains = [
        Chain(
            chain_id=chain_id,
            sequence=_validate(sequence, where=f"{identifier} chain {chain_id}"),
            description=f"from {identifier} ATOM records",
        )
        for chain_id, sequence in sorted(sequences.items())
    ]

    warnings = _chain_warnings(chains)
    # Water is in nearly every crystal structure and says nothing about the
    # molecule; real ligands and modified residues do.
    hetero = [name for name in hetero if name not in {"HOH", "DOD", "WAT"}]
    if hetero:
        # Worth stating plainly: these are in the structure but not in any
        # sequence this platform reasons about.
        warnings.append(
            "the structure also contains non-standard residues or ligands "
            f"({', '.join(sorted(hetero)[:8])}"
            f"{', …' if len(hetero) > 8 else ''}), which are not part of any chain "
            "sequence here and are invisible to every sequence model."
        )

    return ImportedTarget(
        name=name or f"PDB {identifier}",
        chains=chains,
        source="pdb",
        source_detail=(
            f"chains read from {identifier} ({record.path.name}, sha256 "
            f"{record.sha256[:12]}…) on {datetime.now(UTC).date().isoformat()}"
        ),
        pdb_id=identifier,
        notes=(
            "Sequences are those present in the deposited coordinates, which may "
            "differ from the canonical sequence."
        ),
        warnings=warnings,
    )


def from_uniprot(
    accession: str, *, name: str | None = None, settings: Settings | None = None
) -> ImportedTarget:
    """Fetch the canonical sequence for a UniProt accession.

    One chain: UniProt describes a polypeptide, not an assembly. A multi-chain
    biologic is imported either from its PDB entry or by pasting each chain,
    and this route says so rather than pretending a mAb is one sequence.
    """
    s = settings or get_settings()
    identifier = accession.strip().upper()
    if not _UNIPROT_ACCESSION.match(identifier):
        raise ValidationFailure(
            f"{accession!r} is not a UniProt accession",
            field="uniprot_id",
            remedy="An accession looks like P01308 or A0A024R161.",
        )
    if not s.allow_network:
        raise ValidationFailure(
            "network access is disabled, so UniProt cannot be reached",
            remedy="Set BIOLOGIX_ALLOW_NETWORK=true, or paste the sequence instead.",
        )

    url = UNIPROT_FASTA.format(accession=identifier)
    try:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ValidationFailure(
            f"UniProt could not be reached: {exc}",
            remedy="Check the connection, or paste the sequence instead.",
        ) from None
    if response.status_code == 404:
        raise ValidationFailure(
            f"UniProt has no entry {identifier}",
            field="uniprot_id",
            remedy="Check the accession on uniprot.org.",
        )
    if response.status_code != 200:
        raise ValidationFailure(
            f"UniProt returned HTTP {response.status_code} for {identifier}",
            remedy="Try again, or paste the sequence instead.",
        )

    parsed = parse_fasta(response.text)
    header = parsed.chains[0].description or identifier
    return ImportedTarget(
        # UniProt headers look like `sp|P01308|INS_HUMAN Insulin OS=Homo sapiens …`
        name=name or readable_name(header) or identifier,
        chains=[
            Chain(
                chain_id="A",
                sequence=parsed.chains[0].sequence,
                description=f"canonical sequence, UniProt {identifier}",
            )
        ],
        source="uniprot",
        source_detail=(
            f"canonical sequence retrieved from {url} on {datetime.now(UTC).date().isoformat()}"
        ),
        uniprot_id=identifier,
        notes=(
            "One chain, as UniProt describes one polypeptide. If the biologic is a "
            "multi-chain assembly, import it from its PDB entry or paste each chain "
            "so the chains stay separate."
        ),
        warnings=parsed.warnings,
    )


def readable_name(header: str) -> str | None:
    """A biologic's name out of a FASTA header.

    Handles the UniProt form (`sp|P01308|INS_HUMAN Insulin OS=Homo sapiens …`)
    by dropping the accession prefix and the trailing key=value fields, and
    leaves anything else as written.
    """
    without_fields = re.split(r"\s+[A-Z]{2}=", header)[0]
    parts = without_fields.split("|")
    tail = parts[-1].strip() if parts else without_fields.strip()
    # `INS_HUMAN Insulin` -> `Insulin`
    pieces = tail.split(None, 1)
    if len(pieces) == 2 and "_" in pieces[0]:
        return pieces[1].strip() or None
    return tail or None


def build_biologic(
    imported: ImportedTarget,
    *,
    biologic_class: str | BiologicClass = BiologicClass.PROTEIN,
    name: str | None = None,
    notes: str | None = None,
) -> Biologic:
    """Turn an import into a validated :class:`Biologic`.

    Modifications are deliberately not inferred. A disulfide or a PEGylation
    that nobody stated is not evidence, and an invented one would be carried
    into every downstream claim.
    """
    try:
        resolved_class = BiologicClass(str(biologic_class))
    except ValueError:
        raise ValidationFailure(
            f"unknown biologic class {biologic_class!r}",
            field="biologic_class",
            remedy=f"Use one of: {', '.join(c.value for c in BiologicClass)}",
        ) from None

    return Biologic(
        name=(name or imported.name).strip(),
        biologic_class=resolved_class,
        chains=tuple(imported.chains),
        pdb_id=imported.pdb_id,
        structure_source=imported.source_detail if imported.pdb_id else None,
        uniprot_id=imported.uniprot_id,
        notes=notes or imported.notes,
    )


__all__ = [
    "AMBIGUOUS",
    "ESM2_MAX_RESIDUES",
    "ImportedTarget",
    "build_biologic",
    "from_pdb",
    "from_uniprot",
    "parse_fasta",
]

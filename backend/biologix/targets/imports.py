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
from biologix.targets.models import (
    _AA_ALPHABET,
    Biologic,
    BiologicClass,
    Chain,
    Modification,
    ModificationKind,
)

UNIPROT_FASTA = "https://rest.uniprot.org/uniprotkb/{accession}.fasta"
# Only the fields that are actually read, so the response stays small and the
# request says plainly what it is for.
UNIPROT_FEATURES = (
    "https://rest.uniprot.org/uniprotkb/{accession}.json"
    "?fields=ft_chain,ft_peptide,ft_signal,ft_propep,ft_disulfid"
)

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
    #: Modifications the *source* stated, never ones this module worked out.
    #: A disulfide here came from a UniProt annotation and carries that
    #: provenance; the distinction is the whole reason it is allowed through.
    modifications: list[Modification] = field(default_factory=list)

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
            "modifications": [m.model_dump(mode="json") for m in self.modifications],
            "modification_summary": [m.describe() for m in self.modifications],
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


def _chain_id_for(description: str, index: int, used: set[str]) -> str:
    """A chain id from UniProt's own description of the chain.

    ``Insulin A chain`` becomes ``A``, which matters: numbering the mature
    chains in sequence order would call the B chain "A", because the B chain
    comes first in the precursor. Every downstream disulfide and every
    published reference to "A6-A11" would then point at the wrong chain.
    """
    match = re.search(r"\b([A-Z])\s+chain\b", description or "")
    if match and match.group(1) not in used:
        return match.group(1)
    match = re.search(r"\bchain\s+([A-Z])\b", description or "", re.IGNORECASE)
    if match and match.group(1).upper() not in used:
        return match.group(1).upper()
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if letter not in used:
            return letter
    return f"C{index + 1}"


def _mature_chains_from_features(
    sequence: str, features: list[dict[str, Any]]
) -> tuple[list[Chain], list[tuple[int, int, str]], list[str]]:
    """Mature chains from UniProt's Chain and Peptide annotations.

    Returns the chains, the precursor span each one occupies (so disulfides in
    precursor numbering can be mapped onto them), and any warnings.

    A UniProt entry is the *precursor*: for insulin that is 110 residues of
    preproinsulin, which is a different molecule from the 51-residue hormone
    and would give a different pI, a different charge and different chemistry.
    The signal peptide and the C peptide are cleaved during maturation and are
    not present in the drug.
    """
    spans: list[tuple[int, int, str]] = []
    for feature in features:
        if feature.get("type") not in ("Chain", "Peptide"):
            continue
        location = feature.get("location") or {}
        start = (location.get("start") or {}).get("value")
        end = (location.get("end") or {}).get("value")
        if not isinstance(start, int) or not isinstance(end, int):
            # An unknown boundary cannot be trimmed on safely.
            continue
        if start < 1 or end > len(sequence) or start > end:
            continue
        spans.append((start, end, str(feature.get("description") or "")))

    spans.sort()
    chains: list[Chain] = []
    used: set[str] = set()
    warnings: list[str] = []
    for index, (start, end, description) in enumerate(spans):
        chain_id = _chain_id_for(description, index, used)
        used.add(chain_id)
        chains.append(
            Chain(
                chain_id=chain_id,
                sequence=sequence[start - 1 : end],
                description=(
                    f"{description or 'mature chain'}, residues {start}-{end} of the precursor"
                ),
            )
        )

    trimmed = [
        f"{feature.get('type', '').lower()} "
        f"{(feature.get('location') or {}).get('start', {}).get('value')}-"
        f"{(feature.get('location') or {}).get('end', {}).get('value')}"
        for feature in features
        if feature.get("type") in ("Signal", "Propeptide", "Transit peptide")
    ]
    if chains and trimmed:
        warnings.append(
            "the precursor was trimmed to its mature chain(s) using UniProt's own "
            f"annotation; removed: {', '.join(trimmed)}"
        )
    return chains, spans, warnings


def _disulfides_from_features(
    features: list[dict[str, Any]], spans: list[tuple[int, int, str]], chains: list[Chain]
) -> tuple[list[Modification], list[str]]:
    """Disulfides UniProt states, renumbered onto the mature chains.

    Nothing is inferred. A cysteine pair appears here only because the entry
    annotates it, and a bond whose partner was cleaved away during maturation
    is dropped with a warning rather than being bent to fit.
    """

    def locate(position: int) -> tuple[str, int] | None:
        for (start, end, _), chain in zip(spans, chains, strict=True):
            if start <= position <= end:
                return chain.chain_id, position - start + 1
        return None

    modifications: list[Modification] = []
    warnings: list[str] = []
    for feature in features:
        if feature.get("type") != "Disulfide bond":
            continue
        location = feature.get("location") or {}
        first = (location.get("start") or {}).get("value")
        second = (location.get("end") or {}).get("value")
        if not isinstance(first, int) or not isinstance(second, int):
            continue
        a, b = locate(first), locate(second)
        if a is None or b is None:
            warnings.append(
                f"UniProt annotates a disulfide at {first}-{second}, which falls outside "
                "the mature chains and was not carried over"
            )
            continue
        description = str(feature.get("description") or "").strip()
        modifications.append(
            Modification(
                kind=ModificationKind.DISULFIDE,
                sites=(a, b),
                description=(
                    f"{a[0]}{a[1]}-{b[0]}{b[1]}"
                    + (f" ({description})" if description else "")
                    + f" [UniProt {first}-{second}]"
                ),
            )
        )
    return modifications, warnings


def from_uniprot(
    accession: str,
    *,
    name: str | None = None,
    settings: Settings | None = None,
    mature: bool = True,
) -> ImportedTarget:
    """Fetch a UniProt accession as the molecule the drug actually is.

    A UniProt entry is the **precursor**. For insulin that is 110 residues of
    preproinsulin — signal peptide, B chain, C peptide, A chain — which is a
    different molecule from the 51-residue two-chain hormone. Importing it
    whole gave a pI of 4.96 against the real 5.3, a charge and hydrophobicity
    computed over a sequence that is a third propeptide, and no disulfides at
    all, so every cysteine counted as a free thiol.

    With ``mature=True`` (the default) the entry's own Chain and Peptide
    annotations are used to trim it to the mature chains, and its Disulfide
    bond annotations are renumbered onto them. For P01308 that reproduces the
    hand-curated two-chain insulin exactly, disulfides included.

    Nothing is inferred. Trimming and disulfides both come from annotations in
    the entry, carry their precursor positions for audit, and are skipped
    entirely when the entry does not state them. ``mature=False`` returns the
    precursor as deposited.
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
    resolved_name = name or readable_name(header) or identifier
    precursor = parsed.chains[0].sequence
    warnings = list(parsed.warnings)

    chains: list[Chain] = []
    modifications: list[Modification] = []
    notes = (
        "One chain, as UniProt describes one polypeptide. If the biologic is a "
        "multi-chain assembly, import it from its PDB entry or paste each chain "
        "so the chains stay separate."
    )

    if mature:
        features, feature_warnings = _uniprot_features(identifier, s)
        warnings.extend(feature_warnings)
        if features:
            chains, spans, trim_warnings = _mature_chains_from_features(precursor, features)
            warnings.extend(trim_warnings)
            if chains:
                modifications, disulfide_warnings = _disulfides_from_features(
                    features, spans, chains
                )
                warnings.extend(disulfide_warnings)
                warnings.extend(_assembly_warnings(chains, modifications, resolved_name))
                notes = (
                    f"Trimmed from the {len(precursor)}-residue precursor to "
                    f"{len(chains)} mature chain(s) using UniProt's Chain and Peptide "
                    f"annotations, with {len(modifications)} annotated disulfide(s) "
                    "renumbered onto them. The precursor is not the molecule that is "
                    "formulated; import with mature=False to keep it."
                )
            else:
                warnings.append(
                    "UniProt annotates no mature chain for this entry, so the "
                    "precursor sequence is used as deposited"
                )

    if not chains:
        chains = [
            Chain(
                chain_id="A",
                sequence=precursor,
                description=f"canonical sequence, UniProt {identifier}",
            )
        ]

    return ImportedTarget(
        # UniProt headers look like `sp|P01308|INS_HUMAN Insulin OS=Homo sapiens …`
        name=resolved_name,
        chains=chains,
        source="uniprot",
        source_detail=(
            f"canonical sequence retrieved from {url} on "
            f"{datetime.now(UTC).date().isoformat()}"
            + (
                "; mature chains and disulfides from "
                + UNIPROT_FEATURES.format(accession=identifier)
                if modifications or len(chains) > 1
                else ""
            )
        ),
        uniprot_id=identifier,
        notes=notes,
        warnings=warnings,
        modifications=modifications,
    )


def _assembly_warnings(
    chains: list[Chain], modifications: list[Modification], name: str
) -> list[str]:
    """Warn when the mature chains are probably not one molecule.

    Trimming a precursor is right, but it does not establish that what is left
    belongs together. Insulin's A and B chains are one hormone, held by two
    interchain disulfides. Proglucagon's annotations yield eight peptides —
    glucagon, GLP-1, GLP-2, glicentin and more — which are *different drugs*
    that happen to share a precursor, and stitching them into one target
    silently invents a 296-residue molecule that does not exist.

    The discriminator is an interchain disulfide: with one, the chains are
    covalently one assembly. Without one, several mature chains are a reason
    to say so loudly rather than to guess.
    """
    if len(chains) < 2:
        return []

    linked: set[str] = set()
    for modification in modifications:
        ids = {site[0] for site in modification.sites}
        if len(ids) > 1:
            linked |= ids

    unlinked = [chain.chain_id for chain in chains if chain.chain_id not in linked]
    if not unlinked:
        return []
    how_many = (
        "none of them are"
        if len(unlinked) == len(chains)
        else f"{len(unlinked)} of them are not"
    )
    return [
        f"{name} yields {len(chains)} mature chains and {how_many}"
        " joined by an annotated interchain disulfide. A precursor can encode "
        "several separate products — proglucagon encodes glucagon, GLP-1 and "
        "GLP-2 — and those are different biologics, not one multi-chain "
        f"molecule. Check that chain(s) {', '.join(unlinked)} belong in this "
        "target, and import the one peptide you mean if they do not."
    ]


def _uniprot_features(
    accession: str, settings: Settings
) -> tuple[list[dict[str, Any]], list[str]]:
    """The entry's sequence features, or nothing plus the reason why.

    A failure here is never fatal: the sequence has already been retrieved, so
    the import degrades to the precursor with a warning rather than failing
    outright over an annotation lookup.
    """
    url = UNIPROT_FEATURES.format(accession=accession)
    try:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return [], [f"UniProt feature annotations could not be retrieved ({exc})"]
    if response.status_code != 200:
        return [], [
            f"UniProt returned HTTP {response.status_code} for the feature "
            f"annotations of {accession}"
        ]
    try:
        body = response.json()
    except ValueError:
        return [], ["UniProt's feature response was not JSON"]
    features = body.get("features")
    if not isinstance(features, list):
        return [], []
    return [f for f in features if isinstance(f, dict)], []


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

    Modifications are still never *inferred*. What is carried through is only
    what the source stated: a disulfide reaches ``ImportedTarget.modifications``
    because a UniProt annotation declared it, and arrives with its precursor
    positions attached. A disulfide nobody stated remains absent, because an
    invented one would be carried into every downstream claim.
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
        modifications=tuple(imported.modifications),
        pdb_id=imported.pdb_id,
        structure_source=imported.source_detail if imported.pdb_id else None,
        uniprot_id=imported.uniprot_id,
        notes=notes or imported.notes,
    )


__all__ = [
    "AMBIGUOUS",
    "ESM2_MAX_RESIDUES",
    "UNIPROT_FEATURES",
    "ImportedTarget",
    "build_biologic",
    "from_pdb",
    "from_uniprot",
    "parse_fasta",
]

"""Fetch and prepare a target structure for simulation.

Structures are downloaded at run time from RCSB and hashed, never vendored
into the repository. The sequence read out of the structure is compared with
the registry's sequence, and a mismatch is reported rather than ignored: a
structure with missing or mutated residues is a different molecule from the
one the project claims to be studying.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from biologix.errors import ValidationFailure
from biologix.provenance import file_digest
from biologix.settings import get_settings
from biologix.targets.models import Biologic, three_letter_to_one

RCSB_URL = "https://files.rcsb.org/download/{pdb_id}.pdb"


@dataclass
class StructureRecord:
    """A structure file on disk plus everything needed to cite it."""

    pdb_id: str
    path: Path
    sha256: str
    n_bytes: int
    source_url: str
    chains_found: dict[str, str] = field(default_factory=dict)
    sequence_matches: dict[str, bool] = field(default_factory=dict)
    discrepancies: list[str] = field(default_factory=list)
    hetero_residues: list[str] = field(default_factory=list)

    def provenance(self) -> dict[str, object]:
        return {
            "pdb_id": self.pdb_id,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "n_bytes": self.n_bytes,
            "chains_found": {k: len(v) for k, v in self.chains_found.items()},
            "sequence_matches_registry": self.sequence_matches,
            "discrepancies": self.discrepancies,
            "hetero_residues": self.hetero_residues,
        }


def fetch_structure(
    pdb_id: str, *, directory: str | Path | None = None, force: bool = False
) -> StructureRecord:
    """Download ``pdb_id`` from RCSB into ``directory`` and hash it.

    Raises :class:`ValidationFailure` when network access is disabled and the
    file is not already cached, rather than proceeding without a structure.
    """
    settings = get_settings()
    directory = Path(directory) if directory else settings.data_dir / "structures"
    directory.mkdir(parents=True, exist_ok=True)

    pdb_id = pdb_id.strip().upper()
    path = directory / f"{pdb_id}.pdb"
    url = RCSB_URL.format(pdb_id=pdb_id)

    if path.exists() and not force:
        return _record(pdb_id, path, url)

    if not settings.allow_network:
        raise ValidationFailure(
            f"structure {pdb_id} is not cached at {path} and network access is "
            "disabled (BIOLOGIX_ALLOW_NETWORK=false). Download it manually or "
            "enable network access."
        )

    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValidationFailure(
            f"could not download {pdb_id} from {url}: {exc!r}"
        ) from exc

    if not data.startswith(b"HEADER") and b"ATOM" not in data[:4096]:
        raise ValidationFailure(
            f"{url} did not return a PDB file (first bytes: {data[:60]!r})"
        )
    path.write_bytes(data)
    return _record(pdb_id, path, url)


def _record(pdb_id: str, path: Path, url: str) -> StructureRecord:
    return StructureRecord(
        pdb_id=pdb_id,
        path=path,
        sha256=file_digest(path),
        n_bytes=path.stat().st_size,
        source_url=url,
    )


def read_chain_sequences(pdb_path: str | Path) -> tuple[dict[str, str], list[str]]:
    """Extract per-chain one-letter sequences from a PDB file.

    Returns ``(sequences, hetero_residue_names)``. Parsing is done directly
    off ATOM records so this works without OpenMM installed, and so that
    chain boundaries come from the file rather than being assumed.
    """
    sequences: dict[str, list[str]] = {}
    seen: dict[str, set[tuple[int, str]]] = {}
    hetero: set[str] = set()

    with open(pdb_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            tag = line[:6]
            if tag == "ENDMDL":
                # Only read the first model of an NMR ensemble.
                break
            if tag == "HETATM":
                hetero.add(line[17:20].strip())
                continue
            if tag != "ATOM  ":
                continue
            chain_id = line[21].strip() or "_"
            resname = line[17:20].strip()
            try:
                resseq = int(line[22:26])
            except ValueError:
                continue
            icode = line[26].strip()
            key = (resseq, icode)
            bucket = seen.setdefault(chain_id, set())
            if key in bucket:
                continue
            bucket.add(key)
            one = three_letter_to_one(resname)
            if one is None:
                hetero.add(resname)
                continue
            sequences.setdefault(chain_id, []).append(one)

    return {k: "".join(v) for k, v in sequences.items()}, sorted(hetero)


def prepare_target(
    biologic: Biologic,
    *,
    directory: str | Path | None = None,
    pdb_id: str | None = None,
    strict: bool = False,
) -> StructureRecord:
    """Fetch the structure for ``biologic`` and check it against the registry.

    ``strict=True`` raises on any sequence mismatch. The default records the
    discrepancies on the returned record so the caller can surface them, which
    is usually what a researcher wants: crystal structures legitimately differ
    from the canonical sequence (missing termini, engineered mutations) and
    the right response is to show the difference, not to hide or reject it.
    """
    target_pdb = pdb_id or biologic.pdb_id
    if not target_pdb:
        raise ValidationFailure(
            f"{biologic.name} has no pdb_id, so there is no structure to prepare",
            field="pdb_id",
            remedy=(
                "Anything structure-based — docking, complex prediction, simulation — "
                "needs coordinates. Either pass pdb_id explicitly, or register this "
                "target with one: a close homologue is a different protein and is not "
                "substituted automatically."
            ),
        )

    record = fetch_structure(target_pdb, directory=directory)
    found, hetero = read_chain_sequences(record.path)
    record.chains_found = found
    record.hetero_residues = hetero

    for chain in biologic.chains:
        observed = found.get(chain.chain_id)
        if observed is None:
            record.sequence_matches[chain.chain_id] = False
            record.discrepancies.append(
                f"chain {chain.chain_id} is in the registry but absent from "
                f"{record.pdb_id} (file has chains: {', '.join(sorted(found)) or 'none'})"
            )
            continue
        exact = observed == chain.sequence
        record.sequence_matches[chain.chain_id] = exact
        if not exact:
            if chain.sequence in observed:
                record.discrepancies.append(
                    f"chain {chain.chain_id}: structure has {len(observed)} residues "
                    f"containing the registry sequence ({chain.length}) plus extra residues"
                )
            elif observed in chain.sequence:
                record.discrepancies.append(
                    f"chain {chain.chain_id}: structure is missing residues — "
                    f"{len(observed)} of {chain.length} present"
                )
            else:
                mismatches = [
                    f"{i + 1}{a}->{b}"
                    for i, (a, b) in enumerate(zip(chain.sequence, observed, strict=False))
                    if a != b
                ]
                record.discrepancies.append(
                    f"chain {chain.chain_id}: sequence differs at "
                    f"{len(mismatches)} position(s): {', '.join(mismatches[:10])}"
                    + (" ..." if len(mismatches) > 10 else "")
                )

    extra = sorted(set(found) - {c.chain_id for c in biologic.chains})
    if extra:
        record.discrepancies.append(
            f"structure contains chains not in the registry: {', '.join(extra)}"
        )

    if strict and record.discrepancies:
        raise ValidationFailure(
            f"structure {record.pdb_id} does not match {biologic.name}:\n  "
            + "\n  ".join(record.discrepancies)
        )
    return record

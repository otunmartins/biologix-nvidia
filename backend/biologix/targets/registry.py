"""Built-in biologic targets, plus loading of user-supplied ones.

Insulin is the demonstration target. The registry exists so that adding a
different protein or peptide is a data change: drop a JSON file in
``var/targets/`` or call :func:`register`, and every downstream stage works
unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from biologix.targets.models import (
    Biologic,
    BiologicClass,
    Chain,
    Modification,
    ModificationKind,
)

# --------------------------------------------------------------------------
# Human insulin.
#
# Chain A (21 residues) and chain B (30 residues), held together by three
# disulfide bonds: A6-A11 (intrachain), A7-B7 and A20-B19 (interchain).
# Reference structure 3I40 is human insulin at 1.85 A; it is fetched at run
# time rather than vendored, so no third-party file is redistributed here.
# --------------------------------------------------------------------------
HUMAN_INSULIN = Biologic(
    name="human insulin",
    biologic_class=BiologicClass.PEPTIDE,
    chains=(
        Chain(
            chain_id="A",
            sequence="GIVEQCCTSICSLYQLENYCN",
            description="Insulin A chain, 21 residues",
        ),
        Chain(
            chain_id="B",
            sequence="FVNQHLCGSHLVEALYLVCGERGFFYTPKT",
            description="Insulin B chain, 30 residues",
        ),
    ),
    modifications=(
        Modification(
            kind=ModificationKind.DISULFIDE,
            sites=(("A", 6), ("A", 11)),
            description="intrachain A6-A11",
        ),
        Modification(
            kind=ModificationKind.DISULFIDE,
            sites=(("A", 7), ("B", 7)),
            description="interchain A7-B7",
        ),
        Modification(
            kind=ModificationKind.DISULFIDE,
            sites=(("A", 20), ("B", 19)),
            description="interchain A20-B19",
        ),
    ),
    pdb_id="3I40",
    structure_source="RCSB PDB entry 3I40 (fetched at run time, not vendored)",
    uniprot_id="P01308",
    notes=(
        "Formulation-relevant behaviour includes self-association into dimers "
        "and hexamers and zinc coordination. Neither oligomeric state nor zinc "
        "is represented in the default single-chain-pair simulation; see the "
        "modelled-conditions report for each run."
    ),
)

# Insulin aspart: human insulin with B28 Pro -> Asp. Included to show that a
# sequence variant is a data change, not a code change.
INSULIN_ASPART = Biologic(
    name="insulin aspart",
    biologic_class=BiologicClass.PEPTIDE,
    chains=(
        Chain(chain_id="A", sequence="GIVEQCCTSICSLYQLENYCN"),
        Chain(
            chain_id="B",
            sequence="FVNQHLCGSHLVEALYLVCGERGFFYTDKT",
            description="B chain with B28 Pro->Asp",
        ),
    ),
    modifications=HUMAN_INSULIN.modifications,
    uniprot_id="P01308",
    notes="Rapid-acting analogue; B28P->D reduces self-association.",
)

# GLP-1(7-37), a 31-residue peptide with no disulfides: a second chemistry to
# check that nothing in the pipeline assumes insulin's two-chain topology.
GLP1_7_37 = Biologic(
    name="GLP-1 (7-37)",
    biologic_class=BiologicClass.PEPTIDE,
    chains=(
        Chain(
            chain_id="A",
            sequence="HAEGTFTSDVSSYLEGQAAKEFIAWLVKGRG",
            description="GLP-1(7-37), single chain, no disulfides",
        ),
    ),
    uniprot_id="P01275",
    notes=(
        "Single-chain peptide with no disulfide bonds. Useful as a contrast "
        "case to insulin for chain-handling code paths."
    ),
)


_BUILTIN: dict[str, Biologic] = {
    "human-insulin": HUMAN_INSULIN,
    "insulin-aspart": INSULIN_ASPART,
    "glp1-7-37": GLP1_7_37,
}

_REGISTRY: dict[str, Biologic] = dict(_BUILTIN)


def register(key: str, biologic: Biologic, *, overwrite: bool = False) -> None:
    """Add a target to the in-process registry."""
    if key in _REGISTRY and not overwrite:
        raise ValueError(f"target {key!r} already registered; pass overwrite=True to replace")
    _REGISTRY[key] = biologic


def get_target(key: str) -> Biologic:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"unknown target {key!r}. Available: {', '.join(sorted(_REGISTRY))}"
        ) from None


def list_targets() -> list[dict[str, object]]:
    """Summary of every registered target, for the API and UI."""
    out = []
    for key, bio in sorted(_REGISTRY.items()):
        out.append(
            {
                "key": key,
                "name": bio.name,
                "class": str(bio.biologic_class),
                "n_chains": bio.n_chains,
                "total_residues": bio.total_residues,
                "chains": [
                    {"chain_id": c.chain_id, "length": c.length, "description": c.description}
                    for c in bio.chains
                ],
                "n_modifications": len(bio.modifications),
                "modifications": bio.modification_summary(),
                "pdb_id": bio.pdb_id,
                "builtin": key in _BUILTIN,
            }
        )
    return out


def load_from_json(path: str | Path) -> Biologic:
    """Load one target from a JSON file matching the :class:`Biologic` schema."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Biologic.model_validate(data)


def load_directory(directory: str | Path) -> list[str]:
    """Register every ``*.json`` target in ``directory``; returns the keys added."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    added: list[str] = []
    for path in sorted(directory.glob("*.json")):
        bio = load_from_json(path)
        key = path.stem
        register(key, bio, overwrite=True)
        added.append(key)
    return added

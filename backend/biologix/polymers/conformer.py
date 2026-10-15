"""Three-dimensional coordinates for a candidate's own oligomer.

The console could already show a protein–polymer complex in 3D, from a
DiffDock pose, a Boltz-2 prediction or an OpenMM frame. What it could not show
was the candidate *by itself*: a user looking at a repeat unit saw a SMILES
string and a flat schematic, and had to run a docking job before any molecule
appeared in three dimensions. For triage that is backwards — the shape of the
chain is what makes a polymer look reasonable or absurd, and it is knowable
without a GPU or a target.

This module embeds the capped oligomer with RDKit's ETKDGv3 and relaxes it
with MMFF94s, which is the standard, deterministic way to get a conformer for
a flexible chain. What comes back is one low-energy conformer of an isolated
chain in vacuum. It is a *depiction* of the molecule's connectivity and
preferred local geometry, not a prediction of the conformation it adopts in
formulation: a real polymer in water is an ensemble, and an excipient's
behaviour depends on that ensemble and on the protein it is next to. The
evidence tier and the caveat travel with the coordinates so the picture is
never mistaken for a simulation result.

The conformer is deterministic for a given candidate: the embedding seed is
fixed, so the same candidate renders identically on every request and in every
session, and a user comparing two screenshots is comparing chemistry rather
than two draws from a random embedder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors

from biologix.errors import BiologixError
from biologix.polymers.models import EndGroup, PolymerCandidate
from biologix.polymers.oligomer import oligomer_for_screening

# The chain length actually embedded. Long enough to show how the backbone
# turns and whether side groups crowd each other; short enough that ETKDG plus
# MMFF finishes inside a web request. The repeat unit is what is being judged,
# not the molecular weight, and the depiction says which DP it used.
DEPICTION_DP = 8

# Heavy atoms to aim for in the depicted chain. The degree of polymerisation
# is chosen per candidate to land near this, so a bulky repeat unit is shown
# with fewer repeats than a small one. Two things come out of that: embedding
# stays fast enough for a web request, and every candidate is drawn at roughly
# the same visual scale, so comparing two pictures compares the chemistry
# rather than the molecular weight.
TARGET_HEAVY_ATOMS = 70
MIN_DEPICTION_DP = 3
MAX_DEPICTION_DP = 8

# Above this many heavy atoms, distance geometry is started from random
# coordinates rather than from its own initial guess. For a flexible chain
# that is the appropriate algorithm rather than a fallback — measured, the
# pure-distance-geometry start failed on essentially every oligomer past this
# size — so choosing it up front saves a wasted attempt and does not warrant a
# warning about geometry quality.
RANDOM_COORDS_ABOVE_HEAVY = 55

# A hard ceiling on heavy atoms. Beyond this, embedding is slow enough to be a
# denial-of-service risk on a shared endpoint, and the picture is a hairball
# that teaches nothing anyway.
MAX_HEAVY_ATOMS = 360

EMBED_SEED = 0xB10

CAVEAT = (
    "One low-energy conformer of an isolated chain in vacuum, embedded with "
    "ETKDGv3 and relaxed with MMFF94s. It shows connectivity and preferred "
    "local geometry for this repeat unit — not the conformation the polymer "
    "adopts in a formulation, which is an ensemble in water and depends on the "
    "protein it is beside. Nothing here is a simulation result or a stability "
    "claim."
)


@dataclass
class Conformer3D:
    """An embedded oligomer, with the facts needed to label the picture."""

    pdb: str
    sdf: str
    smiles: str
    degree_of_polymerisation: int
    n_atoms: int
    n_heavy_atoms: int
    molecular_weight: float
    force_field: str
    converged: bool
    energy: float | None = None
    warnings: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "smiles": self.smiles,
            "degree_of_polymerisation": self.degree_of_polymerisation,
            "n_atoms": self.n_atoms,
            "n_heavy_atoms": self.n_heavy_atoms,
            "molecular_weight": round(self.molecular_weight, 2),
            "force_field": self.force_field,
            "converged": self.converged,
            "energy_kcal_per_mol": (None if self.energy is None else round(self.energy, 2)),
            "evidence": "computed",
            "caveat": CAVEAT,
            "warnings": list(self.warnings),
        }


class ConformerFailed(Exception):
    """Embedding did not produce usable coordinates, with the reason why."""


def depiction_dp(candidate: PolymerCandidate) -> int:
    """How many repeats to draw, so the picture lands near a constant size."""
    heavy = 0
    for block in candidate.blocks:
        mol = Chem.MolFromSmiles(block.unit.smiles.replace("*", "[H]"))
        if mol is not None:
            heavy = max(heavy, mol.GetNumHeavyAtoms())
    if heavy <= 0:
        return DEPICTION_DP
    return max(MIN_DEPICTION_DP, min(MAX_DEPICTION_DP, round(TARGET_HEAVY_ATOMS / heavy)))


def embed_candidate(
    candidate: PolymerCandidate,
    *,
    dp: int | None = None,
    max_heavy_atoms: int = MAX_HEAVY_ATOMS,
) -> Conformer3D:
    """Embed one candidate's capped oligomer and return its coordinates.

    ``dp`` defaults to :func:`depiction_dp`, which picks a chain length suited
    to the size of the repeat unit. Raises :class:`ConformerFailed` with a
    reason a user can act on, rather than returning an empty structure that the
    viewer would render as nothing.
    """
    if dp is None:
        dp = depiction_dp(candidate)
    try:
        build = oligomer_for_screening(candidate, dp_override=dp)
    except BiologixError as exc:
        # Assembly has its own limits and its own reasons for refusing a
        # chain. They are answers about this candidate, so they are reported
        # as such rather than escaping as a server error.
        raise ConformerFailed(str(exc)) from exc
    mol = Chem.MolFromSmiles(build.smiles)
    if mol is None:
        raise ConformerFailed(
            f"the assembled oligomer did not parse as a molecule: {build.smiles[:120]}"
        )

    heavy = mol.GetNumHeavyAtoms()
    if heavy > max_heavy_atoms:
        raise ConformerFailed(
            f"the oligomer has {heavy} heavy atoms, above the {max_heavy_atoms} "
            "embedding limit. Lower the depicted degree of polymerisation."
        )

    warnings: list[str] = []
    mol = Chem.AddHs(mol)

    params = AllChem.ETKDGv3()
    params.randomSeed = EMBED_SEED
    params.useSmallRingTorsions = True
    # A flexible chain needs the retries; without them embedding fails
    # outright on perfectly ordinary backbones.
    params.maxIterations = 800
    params.useRandomCoords = heavy > RANDOM_COORDS_ABOVE_HEAVY

    if AllChem.EmbedMolecule(mol, params) != 0:
        # Try the other starting strategy before giving up: whichever was not
        # used first is the remaining option.
        params.useRandomCoords = not params.useRandomCoords
        if AllChem.EmbedMolecule(mol, params) != 0:
            raise ConformerFailed(
                "ETKDG could not place this oligomer in three dimensions, with "
                "or without random starting coordinates. The connectivity may "
                "be over-constrained."
            )

    force_field = "MMFF94s"
    converged = False
    energy: float | None = None
    if AllChem.MMFFHasAllMoleculeParams(mol):
        properties = AllChem.MMFFGetMoleculeProperties(mol, mmffVariant="MMFF94s")
        field_ = AllChem.MMFFGetMoleculeForceField(mol, properties)
        if field_ is not None:
            converged = field_.Minimize(maxIts=2000) == 0
            energy = float(field_.CalcEnergy())
    else:
        # UFF covers chemistry MMFF has no parameters for. Which one ran is
        # reported rather than implied, because the geometries differ.
        force_field = "UFF"
        field_ = AllChem.UFFGetMoleculeForceField(mol)
        if field_ is not None:
            converged = field_.Minimize(maxIts=2000) == 0
            energy = float(field_.CalcEnergy())
        warnings.append(
            "MMFF94s has no parameters for part of this molecule, so UFF was "
            "used instead; the geometry is correspondingly less reliable"
        )

    if not converged:
        warnings.append(
            f"{force_field} minimisation did not converge within 2000 steps; the "
            "geometry is a partially relaxed conformer"
        )

    Chem.AssignStereochemistryFrom3D(mol)

    return Conformer3D(
        pdb=Chem.MolToPDBBlock(mol, flavor=4),
        sdf=Chem.MolToMolBlock(mol),
        smiles=build.smiles,
        degree_of_polymerisation=build.degree_of_polymerisation,
        n_atoms=mol.GetNumAtoms(),
        n_heavy_atoms=heavy,
        molecular_weight=float(Descriptors.MolWt(mol)),
        force_field=force_field,
        converged=converged,
        energy=energy,
        warnings=warnings,
    )


@lru_cache(maxsize=256)
def _embed_cached(repeat_smiles: str, dp: int, end_group: str) -> Conformer3D:
    """The embedding itself, memoised on what actually determines it.

    Embedding plus minimisation takes a few seconds for a larger chain, which
    is too slow to repeat on every page view. The conformer is deterministic
    for a fixed seed, so caching returns the identical structure rather than an
    equivalent one, and a user reloading a candidate sees the same molecule in
    the same orientation.
    """
    from biologix.polymers.generate import candidates_from_smiles

    result = candidates_from_smiles([repeat_smiles], dp=dp, end_group=EndGroup(end_group))
    if not result.candidates:
        detail = next(
            (r.rejection_detail for r in result.records if r.rejection_detail),
            "it did not pass the polymerisation rules",
        )
        raise ConformerFailed(f"{repeat_smiles} is not a usable repeat unit: {detail}")
    return embed_candidate(result.candidates[0], dp=dp)


def embed_candidate_cached(candidate: PolymerCandidate) -> Conformer3D:
    """:func:`embed_candidate`, memoised. The path an API request should use."""
    if len(candidate.blocks) != 1:
        # A copolymer's depiction depends on the block sequence, which the
        # cache key does not capture, so it is embedded directly.
        return embed_candidate(candidate)
    return _embed_cached(
        candidate.blocks[0].unit.smiles,
        depiction_dp(candidate),
        str(candidate.end_group),
    )


def embed_repeat_unit_cached(repeat_smiles: str, chain_dp: int = 25) -> Conformer3D:
    """Embed a stored candidate's repeat unit, memoised. The API path.

    ``chain_dp`` is the candidate's real degree of polymerisation, which is
    recorded but deliberately not what gets drawn: a DP of 50 is tens of
    thousands of atoms and would neither embed nor be legible. The depicted
    chain is sized by :func:`depiction_dp` and the picture says so.
    """
    from biologix.polymers.generate import candidates_from_smiles

    probe = candidates_from_smiles([repeat_smiles], dp=max(2, min(chain_dp, 25)))
    if not probe.candidates:
        detail = next(
            (r.rejection_detail for r in probe.records if r.rejection_detail),
            "it did not pass the polymerisation rules",
        )
        raise ConformerFailed(f"{repeat_smiles} is not a usable repeat unit: {detail}")
    return embed_candidate_cached(probe.candidates[0])


def embed_repeat_unit(repeat_smiles: str, *, dp: int = DEPICTION_DP) -> Conformer3D:
    """Embed a bare repeat unit, for a caller that has SMILES and no candidate."""
    from biologix.polymers.generate import candidates_from_smiles

    result = candidates_from_smiles([repeat_smiles], dp=dp)
    if not result.candidates:
        detail = next(
            (r.rejection_detail for r in result.records if r.rejection_detail),
            "it did not pass the polymerisation rules",
        )
        raise ConformerFailed(f"{repeat_smiles} is not a usable repeat unit: {detail}")
    return embed_candidate(result.candidates[0], dp=dp)


__all__ = [
    "CAVEAT",
    "embed_candidate_cached",
    "depiction_dp",
    "DEPICTION_DP",
    "MAX_HEAVY_ATOMS",
    "Conformer3D",
    "ConformerFailed",
    "embed_candidate",
    "embed_repeat_unit",
]

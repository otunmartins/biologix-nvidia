"""Cheap chemistry descriptors, computed before anything expensive runs.

Descriptors are computed on a *fixed-length capped oligomer* so that they
compare like with like across candidates whose requested degree of
polymerisation differs, and they are additionally reported per repeat unit.
The oligomer construction rules travel with the result.

These are computed quantities, not predictions. ``logp`` is Crippen's
estimate for the oligomer, not a measured partition coefficient.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors

from biologix.errors import PolymerizationError
from biologix.polymers.models import PolymerCandidate
from biologix.polymers.oligomer import oligomer_for_screening

# Degree of polymerisation used for all screening descriptors. Short enough
# to be fast, long enough that end-group effects do not dominate.
SCREENING_DP = 8


@dataclass
class DescriptorSet:
    """Descriptors for one candidate."""

    candidate_id: str
    oligomer_smiles: str
    screening_dp: int
    values: dict[str, float] = field(default_factory=dict)
    construction_rules: dict[str, object] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "oligomer_smiles": self.oligomer_smiles,
            "screening_dp": self.screening_dp,
            "values": {k: round(v, 6) for k, v in self.values.items()},
            "construction_rules": self.construction_rules,
            "error": self.error,
            "evidence": "computed",
        }


# Descriptor name -> (callable, human description, units).
DESCRIPTOR_DOCS: dict[str, tuple[str, str]] = {
    "oligomer_mw": ("Molecular weight of the screening oligomer", "g/mol"),
    "repeat_mw": ("Mean molecular weight contributed per repeat unit", "g/mol"),
    "logp_oligomer": ("Crippen estimated logP of the screening oligomer", "log units"),
    "logp_per_repeat": ("Crippen logP divided by degree of polymerisation", "log units"),
    "tpsa_oligomer": ("Topological polar surface area of the oligomer", "A^2"),
    "tpsa_per_repeat": ("Polar surface area per repeat unit", "A^2"),
    "polar_fraction": ("TPSA divided by molecular weight: polarity density", "A^2 per g/mol"),
    "hbd": ("Hydrogen-bond donors in the oligomer", "count"),
    "hba": ("Hydrogen-bond acceptors in the oligomer", "count"),
    "hbd_per_repeat": ("Hydrogen-bond donors per repeat unit", "count"),
    "hba_per_repeat": ("Hydrogen-bond acceptors per repeat unit", "count"),
    "rotatable_bonds": ("Rotatable bonds in the oligomer: backbone flexibility", "count"),
    "rotatable_per_repeat": ("Rotatable bonds per repeat unit", "count"),
    "fraction_csp3": ("Fraction of carbons that are sp3 hybridised", "fraction"),
    "aromatic_fraction": ("Fraction of heavy atoms that are aromatic", "fraction"),
    "heavy_atoms": ("Heavy-atom count of the oligomer", "count"),
    "formal_charge": ("Net formal charge of the oligomer", "e"),
    "charge_per_repeat": ("Net formal charge per repeat unit", "e"),
    "molar_refractivity": ("Crippen molar refractivity", "cm^3/mol"),
    "heteroatom_fraction": ("Fraction of heavy atoms that are not carbon", "fraction"),
    "ring_count": ("Number of rings in the oligomer", "count"),
    "oxygen_per_repeat": ("Oxygen atoms per repeat unit", "count"),
    "nitrogen_per_repeat": ("Nitrogen atoms per repeat unit", "count"),
}


def compute_descriptors(
    candidate: PolymerCandidate, *, screening_dp: int = SCREENING_DP
) -> DescriptorSet:
    """Compute every descriptor for one candidate.

    A build failure is returned on the result rather than raised, so a batch
    screen reports which candidates could not be built instead of aborting.
    """
    try:
        build = oligomer_for_screening(candidate, dp_override=screening_dp)
    except PolymerizationError as exc:
        return DescriptorSet(
            candidate_id=candidate.candidate_id,
            oligomer_smiles="",
            screening_dp=screening_dp,
            error=f"oligomer construction failed: {exc}",
        )

    mol = build.mol
    dp = max(build.degree_of_polymerisation, 1)
    heavy = mol.GetNumHeavyAtoms()

    mw = Descriptors.MolWt(mol)
    logp = Crippen.MolLogP(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    hbd = rdMolDescriptors.CalcNumHBD(mol)
    hba = rdMolDescriptors.CalcNumHBA(mol)
    rot = rdMolDescriptors.CalcNumRotatableBonds(mol)
    charge = Chem.GetFormalCharge(mol)

    n_aromatic = sum(1 for a in mol.GetAtoms() if a.GetIsAromatic())
    n_carbon = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 6)
    n_oxygen = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 8)
    n_nitrogen = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 7)

    values = {
        "oligomer_mw": mw,
        "repeat_mw": mw / dp,
        "logp_oligomer": logp,
        "logp_per_repeat": logp / dp,
        "tpsa_oligomer": tpsa,
        "tpsa_per_repeat": tpsa / dp,
        "polar_fraction": tpsa / mw if mw else 0.0,
        "hbd": float(hbd),
        "hba": float(hba),
        "hbd_per_repeat": hbd / dp,
        "hba_per_repeat": hba / dp,
        "rotatable_bonds": float(rot),
        "rotatable_per_repeat": rot / dp,
        "fraction_csp3": rdMolDescriptors.CalcFractionCSP3(mol),
        "aromatic_fraction": n_aromatic / heavy if heavy else 0.0,
        "heavy_atoms": float(heavy),
        "formal_charge": float(charge),
        "charge_per_repeat": charge / dp,
        "molar_refractivity": Crippen.MolMR(mol),
        "heteroatom_fraction": (heavy - n_carbon) / heavy if heavy else 0.0,
        "ring_count": float(rdMolDescriptors.CalcNumRings(mol)),
        "oxygen_per_repeat": n_oxygen / dp,
        "nitrogen_per_repeat": n_nitrogen / dp,
    }

    return DescriptorSet(
        candidate_id=candidate.candidate_id,
        oligomer_smiles=build.smiles,
        screening_dp=build.degree_of_polymerisation,
        values=values,
        construction_rules=build.construction_rules,
    )


def descriptor_names() -> list[str]:
    return list(DESCRIPTOR_DOCS)


def descriptor_matrix(
    sets: list[DescriptorSet], names: list[str] | None = None
) -> tuple[list[str], list[list[float]], list[str]]:
    """Dense matrix of descriptors for the sets that computed successfully.

    Returns ``(candidate_ids, rows, feature_names)``.
    """
    names = names or descriptor_names()
    ids: list[str] = []
    rows: list[list[float]] = []
    for s in sets:
        if not s.ok:
            continue
        ids.append(s.candidate_id)
        rows.append([float(s.values.get(n, 0.0)) for n in names])
    return ids, rows, names

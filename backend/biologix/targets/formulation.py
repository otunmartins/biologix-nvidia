"""What a biologic's sequence implies for the excipient that should stabilise it.

Generation used to be target-blind. Every campaign, against any biologic,
asked the generator for the same chemistry — which made "works on any
biologic" true only in the sense that the pipeline did not crash on one.

This module closes that gap, and it does so from the sequence alone, so it
works on a biologic nobody has seen before: paste a FASTA or give a UniProt
accession and there is a profile. Nothing here needs a structure, a PDB entry
or a curated annotation.

Four properties drive the choice of chemistry, each for a documented
formulation reason:

**Net charge at pH 7.4.** A polyanion co-formulated with a net-positive
protein will complex it; the classic failure is charge-driven aggregation and
loss of potency. A strongly charged target therefore biases towards neutral,
non-ionic repeat units, and specifically away from the sign that would
attract it.

**Hydrophobicity (GRAVY, Kyte-Doolittle).** A hydrophobic target exposes
patches that nucleate aggregation; the excipients that work there are
amphiphilic — a polar backbone with enough non-polar surface to adsorb onto
the patch. Poloxamers stabilising protein formulations is exactly this
mechanism.

**Free cysteines.** A cysteine that is not in a disulfide is a thiol, and a
thiol adds across an activated alkene by Michael addition. Residual acrylate
or acrylamide monomer will therefore modify the drug covalently. When the
target has free thiols, Michael-acceptor chemistry is penalised rather than
merely noted.

**Aromatic content.** Tryptophan and tyrosine stack with aromatic excipient
side chains, which both perturbs the protein and is the single commonest way
a generated candidate becomes an aggregation risk rather than a stabiliser.

Every number here is computed, every threshold is stated, and the profile
records its own reasoning so a run can say *why* it generated the chemistry
it did. This is a prior over chemistry, not a prediction of stability: it
steers generation and is recorded as having done so. It never overrides a
screening constraint, and it never asserts that a candidate works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biologix.targets.models import Biologic

# Kyte & Doolittle (1982) hydropathy. Standard scale, used for GRAVY as the
# mean over residues.
HYDROPATHY: dict[str, float] = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5,
    "Q": -3.5, "E": -3.5, "G": -0.4, "H": -3.2, "I": 4.5,
    "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8, "P": -1.6,
    "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
}

# Side-chain pKa values for the ionisable residues, plus the termini. Used for
# the Henderson-Hasselbalch charge sum; these are the EMBOSS values, which are
# the ones most commonly quoted for sequence-derived pI.
_PKA_ACID = {"D": 3.9, "E": 4.1, "C": 8.5, "Y": 10.1}
_PKA_BASE = {"K": 10.5, "R": 12.5, "H": 6.0}
_PKA_N_TERM = 8.6
_PKA_C_TERM = 3.6

AROMATIC = frozenset("FWY")

# Thresholds. Each is a declared cut on a computed number, not a learned one.
CHARGE_PER_100_STRONG = 4.0
GRAVY_HYDROPHOBIC = -0.2
GRAVY_VERY_HYDROPHOBIC = 0.3
AROMATIC_FRACTION_HIGH = 0.09


def _residue_counts(biologic: Biologic) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chain in biologic.chains:
        for residue in chain.sequence:
            counts[residue] = counts.get(residue, 0) + 1
    return counts


def net_charge_at(biologic: Biologic, ph: float = 7.4) -> float:
    """Net charge from Henderson-Hasselbalch over ionisable side chains.

    Each chain contributes one N- and one C-terminus, so a two-chain insulin
    carries two of each, which is correct for a disulfide-linked heterodimer.
    """
    counts = _residue_counts(biologic)
    n_chains = len(biologic.chains)
    charge = 0.0

    for residue, pka in _PKA_BASE.items():
        n = counts.get(residue, 0)
        if n:
            charge += n * (1.0 / (1.0 + 10.0 ** (ph - pka)))
    charge += n_chains * (1.0 / (1.0 + 10.0 ** (ph - _PKA_N_TERM)))

    for residue, pka in _PKA_ACID.items():
        n = counts.get(residue, 0)
        if n:
            charge -= n * (1.0 / (1.0 + 10.0 ** (pka - ph)))
    charge -= n_chains * (1.0 / (1.0 + 10.0 ** (_PKA_C_TERM - ph)))

    return charge


def isoelectric_point(biologic: Biologic) -> float:
    """pI by bisection on :func:`net_charge_at`. Accurate to 0.01 pH units."""
    low, high = 0.0, 14.0
    for _ in range(60):
        mid = (low + high) / 2.0
        if net_charge_at(biologic, mid) > 0:
            low = mid
        else:
            high = mid
    return round((low + high) / 2.0, 2)


def gravy(biologic: Biologic) -> float:
    """Grand average of hydropathy over every residue in every chain."""
    total = 0.0
    n = 0
    for chain in biologic.chains:
        for residue in chain.sequence:
            if residue in HYDROPATHY:
                total += HYDROPATHY[residue]
                n += 1
    return round(total / n, 3) if n else 0.0


def free_cysteines(biologic: Biologic) -> int:
    """Cysteines not accounted for by a declared disulfide.

    A disulfide consumes two cysteines. If the modifications do not describe
    them, every cysteine is treated as free — the conservative reading, since
    the consequence of being wrong is an unflagged Michael-addition risk.
    """
    total = _residue_counts(biologic).get("C", 0)
    paired = 2 * len(biologic.disulfides)
    return max(0, total - paired)


def aromatic_fraction(biologic: Biologic) -> float:
    counts = _residue_counts(biologic)
    total = sum(counts.values())
    if not total:
        return 0.0
    return round(sum(counts.get(r, 0) for r in AROMATIC) / total, 4)


@dataclass
class FormulationProfile:
    """Sequence-derived formulation context for one biologic."""

    target_name: str
    n_chains: int
    n_residues: int
    net_charge: float
    isoelectric_point: float
    gravy: float
    free_cysteines: int
    aromatic_fraction: float
    # Chemistry families to favour and to penalise, as tags the generator and
    # the ranking both understand.
    favour: list[str] = field(default_factory=list)
    penalise: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    # The repeat-unit logP band this target actually wants, which is the one
    # property that genuinely separates one biologic from another. Ranking on
    # "as polar as possible" gives every target the same answer; a hydrophobic
    # target needs an amphiphile, and that is a *window*, not a minimum. Both
    # bounds stay inside the aqueous-parenteral ceiling of 1.2.
    logp_window: tuple[float, float] = (-3.0, 0.2)

    @property
    def charge_per_100(self) -> float:
        if not self.n_residues:
            return 0.0
        return round(100.0 * self.net_charge / self.n_residues, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_name": self.target_name,
            "n_chains": self.n_chains,
            "n_residues": self.n_residues,
            "net_charge_ph74": round(self.net_charge, 2),
            "charge_per_100_residues": self.charge_per_100,
            "isoelectric_point": self.isoelectric_point,
            "gravy": self.gravy,
            "free_cysteines": self.free_cysteines,
            "aromatic_fraction": self.aromatic_fraction,
            "favour": list(self.favour),
            "penalise": list(self.penalise),
            "logp_window": list(self.logp_window),
            "rationale": list(self.rationale),
            "basis": (
                "Sequence-derived prior over excipient chemistry. Charge by "
                "Henderson-Hasselbalch (EMBOSS pKa), hydrophobicity by "
                "Kyte-Doolittle GRAVY. It steers generation and is recorded as "
                "having done so; it is not a stability prediction and does not "
                "relax any screening constraint."
            ),
        }


def profile_target(biologic: Biologic) -> FormulationProfile:
    """Derive the formulation profile for any biologic, from its sequence."""
    profile = FormulationProfile(
        target_name=biologic.name,
        n_chains=biologic.n_chains,
        n_residues=biologic.total_residues,
        net_charge=net_charge_at(biologic),
        isoelectric_point=isoelectric_point(biologic),
        gravy=gravy(biologic),
        free_cysteines=free_cysteines(biologic),
        aromatic_fraction=aromatic_fraction(biologic),
    )

    per100 = profile.charge_per_100
    if per100 > CHARGE_PER_100_STRONG:
        profile.favour.append("neutral")
        profile.penalise.append("anionic")
        profile.rationale.append(
            f"net charge {profile.net_charge:+.1f} at pH 7.4 ({per100:+.1f} per 100 "
            f"residues, pI {profile.isoelectric_point}) makes the target "
            "net-positive. An anionic repeat unit would form a polyelectrolyte "
            "complex with it, so neutral chemistry is favoured and anionic "
            "penalised."
        )
    elif per100 < -CHARGE_PER_100_STRONG:
        profile.favour.append("neutral")
        profile.penalise.append("cationic")
        profile.rationale.append(
            f"net charge {profile.net_charge:+.1f} at pH 7.4 ({per100:+.1f} per 100 "
            f"residues, pI {profile.isoelectric_point}) makes the target "
            "net-negative. A cationic repeat unit would complex and likely "
            "aggregate it, so neutral chemistry is favoured and cationic "
            "penalised."
        )
    else:
        profile.rationale.append(
            f"net charge {profile.net_charge:+.1f} at pH 7.4 (pI "
            f"{profile.isoelectric_point}) is close to neutral, so charge does "
            "not constrain the choice of repeat unit."
        )

    if profile.gravy > GRAVY_VERY_HYDROPHOBIC:
        profile.favour.extend(["amphiphilic", "polyether"])
        profile.logp_window = (0.2, 1.1)
        profile.rationale.append(
            f"GRAVY {profile.gravy:+.2f} is strongly hydrophobic, so exposed "
            "non-polar patches are the likely aggregation route. Amphiphilic "
            "chemistry — a polar backbone carrying a short non-polar group — is "
            "the mechanism poloxamers use on such patches, so repeat-unit logP "
            "is steered into 0.2 to 1.1 rather than driven as low as possible: "
            "a fully hydrophilic chain has nothing to adsorb onto the patch "
            "with."
        )
    elif profile.gravy > GRAVY_HYDROPHOBIC:
        profile.favour.append("amphiphilic")
        profile.logp_window = (-0.4, 0.8)
        profile.rationale.append(
            f"GRAVY {profile.gravy:+.2f} is mildly hydrophobic; some amphiphilic "
            "character is useful for patch coverage, so repeat-unit logP is "
            "steered into -0.4 to 0.8."
        )
    else:
        profile.favour.append("hydrophilic")
        profile.logp_window = (-3.0, -0.1)
        profile.rationale.append(
            f"GRAVY {profile.gravy:+.2f} is hydrophilic, so the target needs no "
            "amphiphile to cover non-polar patches. Strongly hydrophilic, "
            "non-interacting chemistry is favoured and repeat-unit logP is "
            "steered below -0.1."
        )

    if profile.free_cysteines:
        profile.penalise.append("michael-acceptor")
        profile.rationale.append(
            f"{profile.free_cysteines} cysteine(s) are not accounted for by a "
            "declared disulfide and are treated as free thiols. Residual "
            "acrylate or acrylamide monomer adds across a thiol by Michael "
            "addition, which would modify the drug covalently, so "
            "Michael-acceptor monomers are penalised for this target."
        )
    else:
        profile.rationale.append(
            f"all cysteines are accounted for by {len(biologic.disulfides)} "
            "declared disulfide(s), so no free-thiol Michael-addition risk is "
            "attributed to residual monomer."
        )

    if profile.aromatic_fraction > AROMATIC_FRACTION_HIGH:
        profile.penalise.append("aromatic")
        profile.rationale.append(
            f"aromatic residues are {profile.aromatic_fraction:.1%} of the "
            "sequence, enough that pi-stacking with an aromatic repeat unit is "
            "a real perturbation risk, so aromatic chemistry is penalised "
            "beyond the baseline constraint."
        )

    # Deduplicate while keeping the order the reasons were added in.
    profile.favour = list(dict.fromkeys(profile.favour))
    profile.penalise = list(dict.fromkeys(profile.penalise))
    return profile


__all__ = [
    "AROMATIC",
    "HYDROPATHY",
    "FormulationProfile",
    "aromatic_fraction",
    "free_cysteines",
    "gravy",
    "isoelectric_point",
    "net_charge_at",
    "profile_target",
]

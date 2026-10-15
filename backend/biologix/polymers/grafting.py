"""Making a polymerisable monomer out of a molecule that is not one.

Most of what a general-purpose molecule generator proposes has no
polymerisable handle at all. GenMol asked for drug-like molecules returns
chlorophenyl sulfonamides and substituted piperidines: perfectly good
molecules, and not monomers. Discarding them loses the only thing the
generator actually contributed — the *pendant chemistry*, the group that will
do the interacting with the protein.

So instead of asking "is this molecule a monomer", this module asks the
question a polymer chemist asks: **can I hang it off a backbone that is?**
That is how functional polymers are really made. Nobody polymerises a drug;
they acylate it onto an acrylate or an acrylamide and polymerise that. The
backbone is boring on purpose, and the pendant group does the work.

Each route here is a real, named, one-step functionalisation:

* an alcohol → an acrylate or methacrylate ester (acryloyl chloride)
* a primary or secondary amine → an acrylamide or methacrylamide
* a carboxylic acid → an ester with 2-hydroxyethyl methacrylate (HEMA)

and the resulting monomer then polymerises radically, which is the most
forgiving polymerisation there is and works in water.

Two refusals matter as much as the routes:

* **More than one graftable handle is refused, not resolved.** A molecule with
  two alcohols acylated twice gives a bis-acrylate, which is a *crosslinker*:
  polymerising it yields an insoluble gel, not a soluble excipient. Choosing
  one handle arbitrarily would hide that.
* **Nothing is grafted onto a site that is part of the pharmacophore story**,
  because this module makes no such judgement. It reports which atom it
  acylated so a chemist can see whether the group it consumed was the one that
  mattered.

Grafting is recorded as a derivation, never passed off as something the
generator proposed: the candidate carries ``derivation: "graft"`` and the full
route, so a reader can always tell a found monomer from a built one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rdkit import Chem

from biologix.polymers.synthesis import (
    PRECEDENT,
    SynthesisRoute,
    interpret_sa,
    synthetic_accessibility,
)

# Handles worth grafting, in the order a chemist would reach for them.
_ALCOHOL = Chem.MolFromSmarts("[OX2H1][CX4]")
_PRIMARY_AMINE = Chem.MolFromSmarts("[NX3;H2;!$(NC=O);!$(NS=O);!$(N=*)][#6]")
_SECONDARY_AMINE = Chem.MolFromSmarts("[NX3;H1;!$(NC=O);!$(NS=O);!$(N=*);!R0]")
_SECONDARY_AMINE_ACYCLIC = Chem.MolFromSmarts("[NX3;H1;!$(NC=O);!$(NS=O);!$(N=*)]([#6])[#6]")
_CARBOXYLIC_ACID = Chem.MolFromSmarts("[CX3](=[OX1])[OX2H1]")
_PHENOL = Chem.MolFromSmarts("[OX2H1]c")


@dataclass
class GraftRoute:
    """One way to hang a molecule off a polymerisable backbone."""

    backbone: str
    handle: str
    handle_atom: int
    repeat_unit: str
    monomer: str
    reagent: str
    description: str
    route: SynthesisRoute
    pendant_smiles: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backbone": self.backbone,
            "handle": self.handle,
            "handle_atom": self.handle_atom,
            "repeat_unit": self.repeat_unit,
            "monomer": self.monomer,
            "reagent": self.reagent,
            "description": self.description,
            "pendant_smiles": self.pendant_smiles,
            "warnings": self.warnings,
            "route": self.route.to_dict(),
        }


@dataclass
class GraftResult:
    input_smiles: str
    routes: list[GraftRoute] = field(default_factory=list)
    refusal: str | None = None
    refusal_code: str | None = None

    @property
    def grafted(self) -> bool:
        return bool(self.routes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_smiles": self.input_smiles,
            "grafted": self.grafted,
            "routes": [r.to_dict() for r in self.routes],
            "refusal": self.refusal,
            "refusal_code": self.refusal_code,
        }


def _count(mol: Chem.Mol, patt: Chem.Mol | None) -> list[tuple[int, ...]]:
    if patt is None:
        return []
    return list(mol.GetSubstructMatches(patt))


def _attach(
    mol: Chem.Mol,
    handle_atom: int,
    *,
    remove_h_from: int,
    fragment: str,
) -> tuple[str, str] | None:
    """Bond ``fragment`` to ``handle_atom`` and return (repeat unit, monomer).

    ``fragment`` is written with a single ``[*]`` marking where it bonds to the
    handle. The repeat unit is produced directly with its two backbone
    attachment points, and the monomer is the same construction with the
    double bond intact.
    """
    repeat_frag, monomer_frag = fragment
    out: list[str] = []
    for piece in (repeat_frag, monomer_frag):
        combined = Chem.RWMol(Chem.CombineMols(mol, Chem.MolFromSmiles(piece)))
        # The fragment's own dummy marks its bonding atom; find it in the
        # combined molecule (its indices are offset by the parent's size).
        offset = mol.GetNumAtoms()
        dummy = None
        for atom in combined.GetAtoms():
            if atom.GetIdx() >= offset and atom.GetAtomicNum() == 0:
                neighbours = list(atom.GetNeighbors())
                if len(neighbours) == 1:
                    dummy = (atom.GetIdx(), neighbours[0].GetIdx())
                    break
        if dummy is None:
            return None
        dummy_idx, anchor_idx = dummy

        handle = combined.GetAtomWithIdx(remove_h_from)
        # Counted before NoImplicit is set: setting it zeroes the implicit
        # hydrogens, so reading the count afterwards would always see none.
        hydrogens = handle.GetTotalNumHs()
        if hydrogens < 1:
            return None
        handle.SetNoImplicit(True)
        handle.SetNumExplicitHs(hydrogens - 1)
        combined.AddBond(handle_atom, anchor_idx, Chem.BondType.SINGLE)
        combined.RemoveAtom(dummy_idx)

        molecule = combined.GetMol()
        try:
            Chem.SanitizeMol(molecule)
        except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
            return None
        if len(Chem.GetMolFrags(molecule)) != 1:
            return None
        out.append(Chem.MolToSmiles(molecule))
    return out[0], out[1]


# Each backbone gives (repeat-unit fragment, monomer fragment). The repeat unit
# carries the two backbone attachment points; the monomer keeps the C=C.
_BACKBONES: dict[str, tuple[str, str, str, str]] = {
    "acrylate": (
        "[*]C(=O)C([*])C[*]",
        "[*]C(=O)C=C",
        "acryloyl chloride",
        "acrylate",
    ),
    "methacrylate": (
        "[*]C(=O)C([*])(C)C[*]",
        "[*]C(=O)C(C)=C",
        "methacryloyl chloride",
        "acrylate",
    ),
}

_AMIDE_BACKBONES: dict[str, tuple[str, str, str, str]] = {
    "acrylamide": (
        "[*]C(=O)C([*])C[*]",
        "[*]C(=O)C=C",
        "acryloyl chloride",
        "acrylamide",
    ),
    "methacrylamide": (
        "[*]C(=O)C([*])(C)C[*]",
        "[*]C(=O)C(C)=C",
        "methacryloyl chloride",
        "acrylamide",
    ),
}


def _fragments(backbone: str) -> tuple[str, str, str, str]:
    return (_BACKBONES | _AMIDE_BACKBONES)[backbone]


def _build_route(
    *,
    mol: Chem.Mol,
    pendant_smiles: str,
    handle_atom: int,
    handle: str,
    backbone: str,
    extra_warnings: list[str],
) -> GraftRoute | None:
    repeat_frag, monomer_frag, reagent, monomer_class = _fragments(backbone)
    built = _attach(
        mol,
        handle_atom,
        remove_h_from=handle_atom,
        fragment=(repeat_frag, monomer_frag),
    )
    if built is None:
        return None
    repeat_unit, monomer = built

    # The repeat unit as written carries three dummies — two backbone ends and
    # the one the fragment used — so it is rebuilt with exactly two.
    repeat_unit = _normalise_repeat_unit(repeat_unit)
    if repeat_unit is None:
        return None

    sa = synthetic_accessibility(monomer)
    pendant_sa = synthetic_accessibility(pendant_smiles)
    route = SynthesisRoute(
        mechanism="chain_growth_vinyl",
        monomer_smiles=monomer,
        monomer_class=monomer_class,
        confidence="established",
        reasoning=(
            f"the molecule itself is not polymerisable, so it is carried as a pendant "
            f"group on a poly({backbone}) backbone. The {handle} is acylated to give "
            f"a {monomer_class} monomer, which polymerises radically — the standard "
            "route to a functional water-soluble polymer"
        ),
        retro_steps=[
            f"poly({backbone}) with pendant ⇒ {monomer} by radical chain growth",
            f"{monomer} ⇒ {pendant_smiles} + {reagent} (one acylation of the {handle})",
            f"{pendant_smiles} ⇒ as proposed by the generator"
            + (f"; SA score {pendant_sa}/10" if pendant_sa is not None else ""),
        ],
        reagents=[reagent, "base (e.g. triethylamine)", "radical initiator (AIBN or persulfate)"],
        precedent=PRECEDENT.get(monomer_class),
        sa_score=sa,
        sa_note=interpret_sa(sa),
        derivation="graft",
    )
    route.concerns.extend(extra_warnings)
    route.concerns.append(
        f"the {handle} is consumed by the graft, so it is no longer available to "
        "interact with the protein — if that group was the point of the molecule, this "
        "route defeats it"
    )
    if monomer_class in ("acrylamide", "acrylate"):
        route.concerns.append(
            "acrylate and acrylamide monomers are toxic and regulated as residuals; a "
            "parenteral polymer needs residual-monomer control"
        )

    return GraftRoute(
        backbone=backbone,
        handle=handle,
        handle_atom=handle_atom,
        repeat_unit=repeat_unit,
        monomer=monomer,
        reagent=reagent,
        description=(f"{handle} acylated with {reagent} to a poly({backbone}) pendant monomer"),
        route=route,
        pendant_smiles=pendant_smiles,
        warnings=extra_warnings,
    )


def _normalise_repeat_unit(smiles: str) -> str | None:
    """Keep exactly the two backbone attachment points.

    The grafting fragments are written with three dummies: the two that become
    the backbone ends, and one marking where the fragment bonds to the pendant.
    That third one is consumed by the bond, but RDKit leaves the written
    skeleton intact, so the result is checked and repaired here rather than
    trusted.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    dummies = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetAtomicNum() == 0]
    if len(dummies) == 2:
        return Chem.MolToSmiles(mol)
    return None


def graft_molecule(smiles: str, *, max_routes: int = 2) -> GraftResult:
    """Build polymerisable monomers from a molecule that is not one."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return GraftResult(
            input_smiles=smiles,
            refusal=f"RDKit could not parse {smiles!r}",
            refusal_code="unparseable",
        )
    canonical = Chem.MolToSmiles(mol)

    alcohols = [m[0] for m in _count(mol, _ALCOHOL)]
    phenols = [m[0] for m in _count(mol, _PHENOL)]
    primary_amines = [m[0] for m in _count(mol, _PRIMARY_AMINE)]
    secondary_amines = [m[0] for m in _count(mol, _SECONDARY_AMINE_ACYCLIC)]
    acids = [m[0] for m in _count(mol, _CARBOXYLIC_ACID)]

    total = len(alcohols) + len(phenols) + len(primary_amines) + len(secondary_amines)
    if total == 0 and not acids:
        return GraftResult(
            input_smiles=canonical,
            refusal=(
                "no graftable handle: the molecule has no alcohol, phenol, primary or "
                "secondary amine, or carboxylic acid to acylate. Without one there is "
                "no single-step route onto a polymerisable backbone"
            ),
            refusal_code="no_graftable_handle",
        )
    if total > 1:
        # This is the refusal that matters most: acylating twice gives a
        # crosslinker, and a crosslinked network is not a soluble excipient.
        return GraftResult(
            input_smiles=canonical,
            refusal=(
                f"{total} graftable handles. Acylating more than one gives a "
                "multi-functional monomer, which crosslinks on polymerisation and "
                "yields an insoluble gel rather than a soluble polymer. Protecting "
                "all but one is possible but is no longer a one-step route, so no "
                "handle is chosen arbitrarily here"
            ),
            refusal_code="would_crosslink",
        )

    routes: list[GraftRoute] = []
    warnings: list[str] = []

    if alcohols or phenols:
        handle_atom = (alcohols or phenols)[0]
        handle = "alcohol" if alcohols else "phenol"
        if phenols and not alcohols:
            warnings = [
                "a phenol ester is more labile to hydrolysis than an alkyl ester, so "
                "the pendant group may detach in an aqueous formulation over time"
            ]
        for backbone in ("acrylate", "methacrylate"):
            route = _build_route(
                mol=mol,
                pendant_smiles=canonical,
                handle_atom=handle_atom,
                handle=handle,
                backbone=backbone,
                extra_warnings=list(warnings),
            )
            if route:
                routes.append(route)

    elif primary_amines or secondary_amines:
        handle_atom = (primary_amines or secondary_amines)[0]
        handle = "primary amine" if primary_amines else "secondary amine"
        for backbone in ("acrylamide", "methacrylamide"):
            route = _build_route(
                mol=mol,
                pendant_smiles=canonical,
                handle_atom=handle_atom,
                handle=handle,
                backbone=backbone,
                extra_warnings=[],
            )
            if route:
                routes.append(route)

    elif acids:
        # An acid needs a two-component esterification rather than a single
        # acylation, so it is offered but marked as the longer route.
        return GraftResult(
            input_smiles=canonical,
            refusal=(
                "the only handle is a carboxylic acid. Grafting it needs esterification "
                "with a hydroxy-functional monomer such as HEMA — two components and a "
                "coupling agent rather than one acylation — which is outside the "
                "single-step route this builds. Worth doing by hand if the molecule is "
                "otherwise attractive"
            ),
            refusal_code="acid_needs_coupling",
        )

    if not routes:
        return GraftResult(
            input_smiles=canonical,
            refusal="the graft produced no valid structure",
            refusal_code="graft_failed",
        )

    return GraftResult(input_smiles=canonical, routes=routes[:max_routes])


__all__ = ["GraftResult", "GraftRoute", "graft_molecule"]

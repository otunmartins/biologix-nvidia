"""Can this polymer actually be made, and can the claim be defended?

:mod:`biologix.polymers.monomer_to_repeat` answers a *structural* question: is
there a bond a named mechanism could open into a two-ended repeat unit. That
question is necessary and nowhere near sufficient. It will happily report that
an oxime and an imidic acid are "two hydroxyls" and call the pair a
polyesterification, or that a C16/C23 internal alkene is a vinyl monomer. Both
are structurally true and chemically indefensible.

This module adds the chemistry. For a repeat unit and the mechanism claimed
for it, it asks:

1. **What monomer would it come from?** Stated explicitly as a structure, not
   implied. That monomer is the retrosynthetic answer for a polymer: a chain
   is made from its monomer, so naming the monomer and the reaction *is* the
   route.
2. **Does that monomer polymerise by that mechanism?** With the real
   constraints: 1,2-disubstituted alkenes do not homopolymerise radically;
   an amide inside a fused aromatic ring is not a lactam; a phenol is not an
   aliphatic alcohol.
3. **Is the monomer itself makeable?** Ertl's synthetic accessibility score,
   which ships with RDKit, computed on the *monomer* — the thing a chemist
   would have to buy or synthesise.
4. **Is there precedent?** Named, where the monomer class is one that real
   excipient or biomaterial polymers are made from.

Every route carries a confidence that is a claim about *chemistry*, not about
the molecule's usefulness:

``established``  the monomer class is one that is routinely polymerised this
                 way, and a named precedent polymer is given.
``plausible``    the mechanism is right for the functional group and nothing
                 argues against it, but no specific precedent is claimed.
``speculative``  the mechanism could apply, with a stated reason to doubt it.
``refused``      the mechanism cannot work on this monomer, with the reason.

Nothing here is a reaction prediction and nothing has been run in a flask. A
route is a defensible proposal with its objections attached, which is the most
a computation can honestly offer.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rdkit import Chem
from rdkit.Chem import Descriptors

from biologix.polymers.monomer_to_repeat import Mechanism

ATTACHMENT = Chem.MolFromSmarts("[#0]")

Confidence = str  # established | plausible | speculative | refused

CONFIDENCE_ORDER = {"established": 3, "plausible": 2, "speculative": 1, "refused": 0}


# --------------------------------------------------------------------------
# Synthetic accessibility.
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _sascorer() -> Any | None:
    """Ertl's SA score, which ships in RDKit's contrib tree.

    Imported lazily and tolerantly: it lives outside the importable package
    tree, so a RDKit build without the contrib directory must degrade to "no
    score" rather than breaking screening.
    """
    try:
        from rdkit.Chem import RDConfig

        path = os.path.join(RDConfig.RDContribDir, "SA_Score")
        if path not in sys.path:
            sys.path.append(path)
        import sascorer  # type: ignore[import-not-found]

        return sascorer
    except Exception:  # noqa: BLE001 - absence is a supported configuration
        return None


def synthetic_accessibility(smiles: str) -> float | None:
    """Ertl SA score: 1 (trivial) to 10 (prohibitive). None when unavailable."""
    scorer = _sascorer()
    if scorer is None:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        return round(float(scorer.calculateScore(mol)), 2)
    except Exception:  # noqa: BLE001
        return None


def interpret_sa(score: float | None) -> str:
    """What an SA score does and does not mean."""
    if score is None:
        return "No synthetic accessibility score available in this build."
    if score < 3:
        band = "easy to make by established chemistry"
    elif score < 4.5:
        band = "routine synthesis, nothing unusual"
    elif score < 6:
        band = "demanding: expect a multi-step route"
    else:
        band = "hard: likely bespoke synthesis, and expensive at excipient scale"
    return (
        f"SA score {score}/10 — {band}. Ertl's heuristic, computed from fragment "
        "frequency and structural complexity; it estimates how hard the *monomer* is "
        "to make, and says nothing about whether the polymer performs."
    )


# --------------------------------------------------------------------------
# Monomer recovery: the retrosynthetic step.
# --------------------------------------------------------------------------


def _strip_attachments(mol: Chem.Mol) -> Chem.Mol | None:
    """Remove the ``[*]`` dummies, leaving the backbone fragment."""
    edit = Chem.RWMol(mol)
    for idx in sorted(
        (atom.GetIdx() for atom in mol.GetAtoms() if atom.GetAtomicNum() == 0),
        reverse=True,
    ):
        edit.RemoveAtom(idx)
    out = edit.GetMol()
    try:
        Chem.SanitizeMol(out)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        return None
    return out


def recover_monomer(repeat_smiles: str, mechanism: Mechanism | str) -> str | None:
    """The monomer this repeat unit came from, rebuilt explicitly.

    This inverts what :mod:`monomer_to_repeat` did: a vinyl repeat unit's two
    attachment points become a double bond again, a ring-opened unit is closed
    back up, and a condensation unit gets its leaving groups back. It is
    reconstructed rather than remembered, so the monomer shown is one that
    really does map onto the stored repeat unit.
    """
    mol = Chem.MolFromSmiles(repeat_smiles)
    if mol is None:
        return None
    dummies = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetAtomicNum() == 0]
    if len(dummies) != 2:
        return None

    attached = []
    for idx in dummies:
        neighbours = list(mol.GetAtomWithIdx(idx).GetNeighbors())
        if len(neighbours) != 1:
            return None
        attached.append(neighbours[0].GetIdx())
    a, b = attached
    name = str(mechanism)

    edit = Chem.RWMol(mol)
    for idx in sorted(dummies, reverse=True):
        edit.RemoveAtom(idx)
    # Indices shift as dummies are removed; recompute by walking the map.
    shift = lambda i: i - sum(1 for d in dummies if d < i)  # noqa: E731
    a2, b2 = shift(a), shift(b)

    if name == str(Mechanism.CHAIN_GROWTH_VINYL):
        bond = edit.GetBondBetweenAtoms(a2, b2)
        if bond is None:
            return None
        bond.SetBondType(Chem.BondType.DOUBLE)
    elif name.startswith("ring_opening"):
        if edit.GetBondBetweenAtoms(a2, b2) is not None:
            return None
        edit.AddBond(a2, b2, Chem.BondType.SINGLE)
    elif name == str(Mechanism.STEP_GROWTH_DIOL) or name == str(Mechanism.STEP_GROWTH_DIAMINE):
        # Both attachments must sit on the heteroatom that condensed. If one is
        # on carbon, this unit was not made by condensing two like groups — it
        # is a polyether or polyamide backbone unit — and silently handing the
        # carbon a hydrogen would invent a monomer (ethanol from a PEG unit).
        wanted = 8 if name == str(Mechanism.STEP_GROWTH_DIOL) else 7
        if any(edit.GetAtomWithIdx(idx).GetAtomicNum() != wanted for idx in (a2, b2)):
            return None
        for idx in (a2, b2):
            atom = edit.GetAtomWithIdx(idx)
            atom.SetNoImplicit(False)
            atom.SetNumExplicitHs(0)
    elif name == str(Mechanism.STEP_GROWTH_DIACID):
        # Put the acid -OH back on each capped carbonyl carbon.
        for idx in (a2, b2):
            oxygen = edit.AddAtom(Chem.Atom(8))
            edit.AddBond(idx, oxygen, Chem.BondType.SINGLE)
            edit.GetAtomWithIdx(oxygen).SetNumExplicitHs(1)
            atom = edit.GetAtomWithIdx(idx)
            atom.SetNoImplicit(False)
            atom.SetNumExplicitHs(0)
    else:
        return None

    out = edit.GetMol()
    for idx in (a2, b2):
        try:
            out.GetAtomWithIdx(idx).SetNoImplicit(False)
        except Exception:  # noqa: BLE001
            return None
    try:
        Chem.SanitizeMol(out)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        return None
    if len(Chem.GetMolFrags(out)) != 1:
        return None
    return Chem.MolToSmiles(out)


def attachment_span(repeat_smiles: str) -> int | None:
    """Bonds between the two attachment points of a repeat unit.

    A vinyl homopolymer unit spans one bond: ``-CH2-CH(R)-``. A longer span
    means the unit covers more than one monomer insertion, which changes what
    the unit *is* — and therefore what route could make it.
    """
    mol = Chem.MolFromSmiles(repeat_smiles)
    if mol is None:
        return None
    anchors: list[int] = []
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 0:
            neighbours = list(atom.GetNeighbors())
            if len(neighbours) != 1:
                return None
            anchors.append(neighbours[0].GetIdx())
    if len(anchors) != 2:
        return None
    path = Chem.GetShortestPath(mol, anchors[0], anchors[1])
    return max(len(path) - 1, 0) if path else None


def _ethylene_spaced_monomer(repeat_smiles: str) -> str | None:
    """The vinyl comonomer of an ethylene-spaced unit.

    ``-CH2-CH2-CH(R)-`` is one ethylene insertion plus one vinyl insertion, so
    dropping the ethylene leaves the ordinary ``-CH2-CH(R)-`` unit, whose
    monomer is recovered the usual way.
    """
    mol = Chem.MolFromSmiles(repeat_smiles)
    if mol is None:
        return None
    dummies = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    if len(dummies) != 2:
        return None
    anchors = [list(mol.GetAtomWithIdx(d).GetNeighbors())[0].GetIdx() for d in dummies]
    path = Chem.GetShortestPath(mol, anchors[0], anchors[1])
    if not path or len(path) != 3:
        return None

    # The anchor whose own substituents are only the chain is the ethylene end.
    def heavy_degree(idx: int) -> int:
        atom = mol.GetAtomWithIdx(idx)
        return len([n for n in atom.GetNeighbors() if n.GetAtomicNum() != 0])

    ethylene_end = (
        anchors[0] if heavy_degree(anchors[0]) <= heavy_degree(anchors[1]) else anchors[1]
    )
    ethylene_dummy = dummies[anchors.index(ethylene_end)]
    spacer = path[1]

    edit = Chem.RWMol(mol)
    for idx in sorted((ethylene_dummy, ethylene_end), reverse=True):
        edit.RemoveAtom(idx)
    shifted = spacer - sum(1 for removed in (ethylene_dummy, ethylene_end) if removed < spacer)
    dummy = edit.AddAtom(Chem.Atom(0))
    edit.AddBond(shifted, dummy, Chem.BondType.SINGLE)
    atom = edit.GetAtomWithIdx(shifted)
    atom.SetNoImplicit(False)
    atom.SetNumExplicitHs(0)
    trimmed = edit.GetMol()
    try:
        Chem.SanitizeMol(trimmed)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        return None
    return recover_monomer(Chem.MolToSmiles(trimmed), Mechanism.CHAIN_GROWTH_VINYL)


# --------------------------------------------------------------------------
# Monomer classification: does the mechanism actually apply?
# --------------------------------------------------------------------------

# Activated vinyls — the monomer classes that radical chain growth is built on.
_ACRYLATE = Chem.MolFromSmarts("[CH2X3]=[CX3;H1,H0][CX3](=[OX1])[OX2]")
_ACRYLAMIDE = Chem.MolFromSmarts("[CH2X3]=[CX3;H1,H0][CX3](=[OX1])[NX3]")
_STYRENIC = Chem.MolFromSmarts("[CH2X3]=[CX3;H1,H0][c]")
_VINYL_ETHER = Chem.MolFromSmarts("[CH2X3]=[CX3;H1,H0][OX2]")
# `[#7]` rather than `[NX3]`: the nitrogen of an N-vinyl azole is aromatic, and
# poly(1-vinylimidazole) and poly(vinylpyrrolidone) are both this family.
_N_VINYL = Chem.MolFromSmarts("[CH2X3]=[CX3;H1,H0][#7]")
_VINYL_NITRILE = Chem.MolFromSmarts("[CH2X3]=[CX3;H1][CX2]#[NX1]")
_TERMINAL_ALKENE = Chem.MolFromSmarts("[CH2X3]=[CX3;H1][#6]")

# Aliphatic alcohol: an sp3 carbon bearing -OH. Excludes phenol, enol, oxime,
# imidic acid, carboxylic acid and hydroxamic acid, none of which behave like
# a diol in a polyesterification.
_ALIPHATIC_OH = Chem.MolFromSmarts("[OX2H1][CX4]")
_PRIMARY_ALIPHATIC_AMINE = Chem.MolFromSmarts("[NX3;H2][CX4]")
_CARBOXYLIC_ACID = Chem.MolFromSmarts("[CX3](=[OX1])[OX2H1]")

# Ring-opening monomers, with the ring sizes that actually open.
_EPOXIDE_RING = Chem.MolFromSmarts("[OX2r3][CX4r3][CX4r3]")
_OXETANE_RING = Chem.MolFromSmarts("[OX2r4][CX4r4]")
_LACTONE_RING = Chem.MolFromSmarts("[OX2R][CX3R](=[OX1])")
_LACTAM_RING = Chem.MolFromSmarts("[NX3R][CX3R](=[OX1])")
_CYCLIC_CARBONATE_RING = Chem.MolFromSmarts("[OX2R][CX3R](=[OX1])[OX2R]")

#: Monomer classes with a named precedent polymer. These are the chemistries
#: real water-soluble excipients and biomaterials are made from, which is what
#: makes a route defensible rather than merely drawable.
PRECEDENT = {
    "acrylamide": (
        "poly(acrylamide) and poly(N-isopropylacrylamide) — radical polymerisation "
        "of acrylamides is the standard route to water-soluble, non-ionic chains"
    ),
    "acrylate": (
        "poly(2-hydroxyethyl methacrylate) and poly(PEG acrylate)s — radical "
        "polymerisation of acrylates and methacrylates is how most functional "
        "hydrophilic polymers are made"
    ),
    "n-vinyl amide": (
        "poly(vinylpyrrolidone), a parenteral excipient in current use; N-vinyl "
        "amides and N-vinyl azoles polymerise radically to give highly "
        "water-soluble chains"
    ),
    "isobutylene-type": (
        "polyisobutylene; 1,1-disubstituted alkenes propagate cationically, not "
        "radically, and need dry low-temperature conditions"
    ),
    "ethylene copolymer": (
        "poly(ethylene-co-vinyl alcohol) (EVOH) and poly(ethylene-co-vinyl acetate); "
        "an ethylene-spaced unit is a copolymer repeat unit, not a homopolymer one"
    ),
    "vinyl ether": (
        "poly(vinyl methyl ether) and, after hydrolysis of vinyl esters, poly(vinyl alcohol)"
    ),
    "styrenic": (
        "polystyrene and its sulfonated derivatives; the chemistry is established "
        "though the hydrophobic backbone is a poor starting point for an excipient"
    ),
    "epoxide": (
        "poly(ethylene glycol) from ethylene oxide — the most widely used "
        "parenteral excipient polymer there is"
    ),
    "lactone": (
        "poly(lactic acid), poly(caprolactone) and PLGA; ring-opening of lactones "
        "is established and the products are degradable"
    ),
    "lactam": (
        "poly(2-ethyl-2-oxazoline) and nylon-6 from caprolactam; ring-opening of "
        "strained, non-aromatic lactams"
    ),
    "cyclic carbonate": "poly(trimethylene carbonate), a degradable biomaterial",
    "aliphatic diol": (
        "poly(ethylene glycol) and polyester soft segments; step growth with a "
        "diacid or diisocyanate"
    ),
    "aliphatic diamine": "nylons and poly(amide)s by step growth with a diacid",
    "diacid": "poly(esters) and poly(amides) by step growth with a diol or diamine",
}


@dataclass
class SynthesisRoute:
    """How a candidate would be made, and what argues against it."""

    mechanism: str
    monomer_smiles: str | None
    monomer_class: str | None
    confidence: Confidence
    reasoning: str
    retro_steps: list[str] = field(default_factory=list)
    reagents: list[str] = field(default_factory=list)
    precedent: str | None = None
    sa_score: float | None = None
    sa_note: str = ""
    concerns: list[str] = field(default_factory=list)
    derivation: str = "direct"

    @property
    def defensible(self) -> bool:
        """True when the chemistry stands up, whatever the polymer is worth."""
        return self.confidence in ("established", "plausible")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "monomer_smiles": self.monomer_smiles,
            "monomer_class": self.monomer_class,
            "confidence": self.confidence,
            "defensible": self.defensible,
            "reasoning": self.reasoning,
            "retro_steps": self.retro_steps,
            "reagents": self.reagents,
            "precedent": self.precedent,
            "sa_score": self.sa_score,
            "sa_note": self.sa_note,
            "concerns": self.concerns,
            "derivation": self.derivation,
        }


def _alkene_substitution(mol: Chem.Mol) -> tuple[int, str] | None:
    """Count the carbon substituents on the first non-ring C=C."""
    patt = Chem.MolFromSmarts("[CX3;!R]=[CX3;!R]")
    matches = mol.GetSubstructMatches(patt)
    if not matches:
        return None
    i, j = matches[0]
    substituents = 0
    terminal = False
    for idx in (i, j):
        atom = mol.GetAtomWithIdx(idx)
        heavy = [n for n in atom.GetNeighbors() if n.GetIdx() not in (i, j)]
        substituents += len(heavy)
        if atom.GetTotalNumHs() == 2:
            terminal = True
    pattern = "terminal" if terminal else "internal"
    return substituents, pattern


def _classify_vinyl(monomer: Chem.Mol) -> tuple[str | None, Confidence, str, list[str]]:
    """Which activated-vinyl class this monomer belongs to, if any."""
    concerns: list[str] = []
    checks = [
        (_ACRYLAMIDE, "acrylamide"),
        (_ACRYLATE, "acrylate"),
        (_N_VINYL, "n-vinyl amide"),
        (_VINYL_ETHER, "vinyl ether"),
        (_VINYL_NITRILE, "vinyl nitrile"),
        (_STYRENIC, "styrenic"),
    ]
    # α-substitution (a 1,1-disubstituted alkene) is the dividing line between
    # radical and ionic chemistry for everything except the carbonyl-activated
    # monomers, where methacrylates are radical and routine.
    alpha_substituted = monomer.HasSubstructMatch(Chem.MolFromSmarts("[CH2X3]=[CX3;H0]"))
    for patt, label in checks:
        if patt is not None and monomer.HasSubstructMatch(patt):
            if alpha_substituted and label not in ("acrylate", "acrylamide"):
                return (
                    label,
                    "plausible",
                    (
                        f"an α-substituted {label}. The monomer class is right, but "
                        "α-substitution shifts it out of radical chemistry: "
                        "α-methylstyrene and α-substituted vinyl ethers polymerise "
                        "cationically or anionically, under dry non-aqueous conditions"
                    ),
                    concerns
                    + [
                        "ionic polymerisation needs dry, oxygen-free, often cold "
                        "conditions and cannot be run in water"
                    ],
                )
            # "an N-vinyl amide": the letter is spoken, so the article follows
            # the sound rather than the spelling.
            article = "an" if label[0] in "aeiou" or label.startswith("n-") else "a"
            return (
                label,
                "established",
                f"{article} {label} monomer: CH2=CH- activated by the adjacent group",
                concerns,
            )

    substitution = _alkene_substitution(monomer)
    if substitution is None:
        return None, "refused", "the recovered monomer has no non-ring C=C to polymerise", concerns

    count, pattern = substitution
    if pattern == "internal":
        # This is the case that produced the C16/C23 "monomer".
        return (
            None,
            "refused",
            (
                "the double bond is internal (1,2-disubstituted). Internal alkenes do "
                "not homopolymerise under radical, cationic or anionic conditions — "
                "the propagating centre is too hindered and too unstabilised. Ethylene "
                "and mono-substituted vinyls polymerise; 2-butene does not."
            ),
            concerns,
        )
    if count >= 3:
        return (
            None,
            "refused",
            (
                f"the alkene carries {count} carbon substituents. Tri- and "
                "tetra-substituted alkenes do not propagate: isobutylene is the "
                "practical limit, and it needs cationic conditions."
            ),
            concerns,
        )
    if count == 2 and pattern == "terminal":
        return (
            "isobutylene-type",
            "plausible",
            (
                "a 1,1-disubstituted alkene with no activating group. These propagate "
                "cationically — polyisobutylene is the precedent — which needs dry, "
                "cold, non-aqueous conditions rather than the radical chemistry the "
                "activated monomers here use"
            ),
            concerns
            + [
                "cationic polymerisation cannot be run in water, which is a practical "
                "obstacle for an excipient intended for an aqueous formulation"
            ],
        )
    if monomer.HasSubstructMatch(_TERMINAL_ALKENE):
        concerns.append(
            "an unactivated terminal alkene needs coordination catalysis "
            "(Ziegler-Natta or metallocene), not the radical chemistry the other "
            "vinyl monomers here use"
        )
        return (
            "unactivated alkene",
            "speculative",
            (
                "a mono-substituted alkene with no activating group. Polymerisable in "
                "principle, but only by coordination catalysis, and not in water"
            ),
            concerns,
        )
    return None, "refused", "the alkene is not of a polymerisable substitution pattern", concerns


def _ring_size_of_match(monomer: Chem.Mol, patt: Chem.Mol) -> int | None:
    match = monomer.GetSubstructMatch(patt)
    if not match:
        return None
    info = monomer.GetRingInfo()
    for ring in info.AtomRings():
        if match[0] in ring and match[1] in ring:
            return len(ring)
    return None


def assess_route(
    repeat_smiles: str,
    mechanism: Mechanism | str,
    *,
    monomer_smiles: str | None = None,
    derivation: str = "direct",
) -> SynthesisRoute:
    """Judge whether ``repeat_smiles`` could really be made by ``mechanism``."""
    name = str(mechanism)
    if monomer_smiles is None:
        amide = _amide_backbone_route(repeat_smiles, derivation)
        if amide is not None:
            return amide
    if name == str(Mechanism.CHAIN_GROWTH_VINYL) and monomer_smiles is None:
        span = attachment_span(repeat_smiles)
        if span is not None and span > 1:
            return _assess_spaced_unit(repeat_smiles, span, derivation)
    monomer = monomer_smiles or recover_monomer(repeat_smiles, name)
    if monomer is None:
        return SynthesisRoute(
            mechanism=name,
            monomer_smiles=None,
            monomer_class=None,
            confidence="speculative",
            reasoning=(
                "the monomer could not be reconstructed from this repeat unit, so the "
                "route cannot be checked. Treat the mechanism label as unverified."
            ),
            derivation=derivation,
        )

    mol = Chem.MolFromSmiles(monomer)
    if mol is None:
        return SynthesisRoute(
            mechanism=name,
            monomer_smiles=monomer,
            monomer_class=None,
            confidence="refused",
            reasoning="the reconstructed monomer is not a valid structure",
            derivation=derivation,
        )

    sa = synthetic_accessibility(monomer)
    route = SynthesisRoute(
        mechanism=name,
        monomer_smiles=monomer,
        monomer_class=None,
        confidence="refused",
        reasoning="",
        sa_score=sa,
        sa_note=interpret_sa(sa),
        derivation=derivation,
    )

    if name == str(Mechanism.CHAIN_GROWTH_VINYL):
        label, confidence, reasoning, concerns = _classify_vinyl(mol)
        route.monomer_class = label
        route.confidence = confidence
        route.reasoning = reasoning
        route.concerns.extend(concerns)
        if confidence != "refused":
            route.retro_steps = [
                f"poly(repeat unit) ⇒ {monomer} by radical chain growth",
                f"{monomer} ⇒ commercial monomer or one acylation from its precursor",
            ]
            route.reagents = ["radical initiator (e.g. AIBN or a persulfate in water)"]
        if label in ("acrylamide", "acrylate"):
            route.concerns.append(
                "acrylamide and acrylate monomers are themselves toxic and are "
                "regulated as residuals; a parenteral polymer needs residual-monomer "
                "control, not just a clean polymer"
            )

    elif name.startswith("ring_opening"):
        route.confidence, route.monomer_class, route.reasoning = _assess_ring_opening(mol, name)
        if route.confidence != "refused":
            route.retro_steps = [
                f"poly(repeat unit) ⇒ {monomer} by ring-opening polymerisation",
                f"{monomer} ⇒ the cyclic monomer, commercial or one ring-closure away",
            ]
            route.reagents = ["ROP initiator or catalyst appropriate to the ring"]

    elif name in (str(Mechanism.STEP_GROWTH_DIOL), str(Mechanism.STEP_GROWTH_DIAMINE)):
        route.confidence, route.monomer_class, route.reasoning = _assess_step_growth(mol, name)
        if route.confidence != "refused":
            partner = "a diacid or diacid chloride" if "diol" in name else "a diacid or diester"
            route.retro_steps = [
                f"poly(repeat unit) ⇒ {monomer} + {partner} by step growth",
                f"{monomer} ⇒ commercial difunctional monomer",
            ]
            route.reagents = [partner, "condensation catalyst, with water removal"]
            route.concerns.append(
                "step growth needs both partners at high purity and near-exact "
                "stoichiometry to reach useful chain length; it is far less forgiving "
                "than radical polymerisation"
            )

    elif name == str(Mechanism.STEP_GROWTH_DIACID):
        acids = len(mol.GetSubstructMatches(_CARBOXYLIC_ACID))
        if acids >= 2:
            route.confidence = "established"
            route.monomer_class = "diacid"
            route.reasoning = (
                f"a dicarboxylic acid ({acids} acid groups): step growth with a diol "
                "or diamine is standard polyester and polyamide chemistry"
            )
            route.retro_steps = [
                f"poly(repeat unit) ⇒ {monomer} + a diol or diamine by step growth",
                f"{monomer} ⇒ commercial diacid",
            ]
            route.reagents = ["a diol or diamine", "condensation catalyst"]
        else:
            route.confidence = "refused"
            route.reasoning = (
                f"only {acids} carboxylic acid group(s) on the recovered monomer, so it "
                "cannot step-grow into a chain"
            )

    else:
        route.confidence = "speculative"
        route.reasoning = f"no feasibility check is implemented for {name}"

    if route.monomer_class and route.monomer_class in PRECEDENT:
        route.precedent = PRECEDENT[route.monomer_class]

    if sa is not None and sa >= 6 and route.confidence != "refused":
        route.concerns.append(
            f"the monomer's SA score is {sa}/10, so making it is likely a bespoke "
            "multi-step synthesis — expensive at the tonne scale an excipient needs"
        )

    mw = Descriptors.MolWt(mol)
    if mw > 600 and route.confidence != "refused":
        route.concerns.append(
            f"the monomer is {mw:.0f} Da. A heavy monomer gives a low density of "
            "functional groups per unit mass and is unusual outside macromonomer "
            "chemistry"
        )

    return route


def _amide_backbone_route(repeat_smiles: str, derivation: str) -> SynthesisRoute | None:
    """An ``-NH-R-C(=O)-`` unit is a polyamide of the amino-acid kind.

    Neither "two amines" nor "two acids": one of each, which is the amino-acid
    case. Both routes to it are real and named — step-growth condensation of
    the amino acid, or ring-opening of its N-carboxyanhydride, which is how
    poly(sarcosine) and poly(glutamate)s are actually made.
    """
    mol = Chem.MolFromSmiles(repeat_smiles)
    if mol is None:
        return None
    anchors: list[Any] = []
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 0:
            neighbours = list(atom.GetNeighbors())
            if len(neighbours) != 1:
                return None
            anchors.append(neighbours[0])
    if len(anchors) != 2:
        return None

    nitrogen = next((a for a in anchors if a.GetSymbol() == "N"), None)
    carbonyl = next(
        (
            a
            for a in anchors
            if a.GetSymbol() == "C"
            and any(
                b.GetBondType() is Chem.BondType.DOUBLE and b.GetOtherAtom(a).GetSymbol() == "O"
                for b in a.GetBonds()
            )
        ),
        None,
    )
    if nitrogen is None or carbonyl is None:
        return None

    monomer = None
    edit = Chem.RWMol(mol)
    dummies = sorted((a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0), reverse=True)
    for idx in dummies:
        edit.RemoveAtom(idx)
    shift = lambda i: i - sum(1 for d in dummies if d < i)  # noqa: E731
    n_idx, c_idx = shift(nitrogen.GetIdx()), shift(carbonyl.GetIdx())
    edit.GetAtomWithIdx(n_idx).SetNoImplicit(False)
    edit.GetAtomWithIdx(n_idx).SetNumExplicitHs(0)
    oxygen = edit.AddAtom(Chem.Atom(8))
    edit.AddBond(c_idx, oxygen, Chem.BondType.SINGLE)
    edit.GetAtomWithIdx(oxygen).SetNumExplicitHs(1)
    edit.GetAtomWithIdx(c_idx).SetNoImplicit(False)
    edit.GetAtomWithIdx(c_idx).SetNumExplicitHs(0)
    built = edit.GetMol()
    try:
        Chem.SanitizeMol(built)
        if len(Chem.GetMolFrags(built)) == 1:
            monomer = Chem.MolToSmiles(built)
    except (Chem.AtomValenceException, Chem.KekulizeException, ValueError):
        monomer = None

    sa = synthetic_accessibility(monomer) if monomer else None
    # An aromatic amine is aramid chemistry, not peptide chemistry: the
    # condensation works, but N-carboxyanhydride routes do not apply and the
    # product is rigid and poorly soluble — the opposite of an excipient.
    aromatic_amine = nitrogen.GetIsAromatic() or any(
        n.GetIsAromatic() for n in nitrogen.GetNeighbors()
    )
    concerns = [
        "N-carboxyanhydride chemistry needs phosgene or a substitute and strictly "
        "anhydrous conditions; direct condensation reaches lower chain lengths"
    ]
    if aromatic_amine:
        concerns = [
            "aromatic polyamides are rigid, high-melting and poorly soluble — the "
            "opposite of what an aqueous excipient needs",
            "step growth needs near-exact stoichiometry and high monomer purity to "
            "reach useful chain length",
        ]
    return SynthesisRoute(
        mechanism="step_growth_amino_acid",
        monomer_smiles=monomer,
        monomer_class="aromatic amino acid" if aromatic_amine else "amino acid",
        confidence=("plausible" if aromatic_amine else "established") if monomer else "plausible",
        reasoning=(
            (
                "the unit has an aromatic amine at one end and a carbonyl at the other. "
                "Condensation gives an aramid-type polyamide, which is real chemistry "
                "but not the peptide route: N-carboxyanhydride polymerisation does not "
                "work on an aromatic amine"
            )
            if aromatic_amine
            else (
                "the unit has an amine at one end and a carbonyl at the other, which is "
                "the amino-acid case rather than a diamine or a diacid. Two established "
                "routes give it: step-growth condensation of the amino acid, or "
                "ring-opening polymerisation of its N-carboxyanhydride, which is how "
                "poly(sarcosine) and poly(amino acid)s are made"
            )
        ),
        retro_steps=(
            [
                f"poly(amide) ⇒ {monomer} by condensation"
                + ("" if aromatic_amine else ", or via its N-carboxyanhydride"),
                f"{monomer} ⇒ the amino acid, commercial or one protection-deprotection away",
            ]
            if monomer
            else []
        ),
        reagents=[
            "coupling agent with water removal, or phosgene/triphosgene to form the NCA",
            "an initiating amine for ring-opening",
        ],
        precedent=(
            "aramids such as Kevlar and Nomex; established chemistry, but as "
            "engineering plastics rather than excipients"
            if aromatic_amine
            else (
                "poly(sarcosine) and poly(glutamate)s; polypeptoid and polypeptide "
                "chains are water-soluble, degradable and already used as PEG "
                "alternatives"
            )
        ),
        sa_score=sa,
        sa_note=interpret_sa(sa),
        concerns=concerns,
        derivation=derivation,
    )


def _assess_spaced_unit(repeat_smiles: str, span: int, derivation: str) -> SynthesisRoute:
    """A unit covering more than one insertion is a copolymer unit.

    ``-CH2-CH2-CH(R)-`` is not a vinyl homopolymer repeat unit: it is one
    ethylene plus one vinyl insertion, which is EVOH-type chemistry. Calling it
    a homopolymer unit and recovering "the monomer" would invent a molecule
    that cannot exist, so this is reported as what it is.
    """
    if span != 2:
        return SynthesisRoute(
            mechanism=str(Mechanism.CHAIN_GROWTH_VINYL),
            monomer_smiles=None,
            monomer_class=None,
            confidence="speculative",
            reasoning=(
                f"the two attachment points are {span} bonds apart, so this unit spans "
                "several monomer insertions. It is not a single-monomer repeat unit, "
                "and no single monomer would give it"
            ),
            derivation=derivation,
        )

    comonomer = _ethylene_spaced_monomer(repeat_smiles)
    sa = synthetic_accessibility(comonomer) if comonomer else None
    route = SynthesisRoute(
        mechanism=str(Mechanism.CHAIN_GROWTH_VINYL),
        monomer_smiles=comonomer,
        monomer_class="ethylene copolymer",
        confidence="plausible" if comonomer else "speculative",
        reasoning=(
            "an ethylene-spaced unit: one ethylene insertion per vinyl insertion. This "
            "is a *copolymer* repeat unit, so the route is copolymerisation of "
            + (f"{comonomer} with ethylene" if comonomer else "a vinyl monomer with ethylene")
            + ", as in EVOH — not homopolymerisation of a single monomer"
        ),
        retro_steps=(
            [
                f"poly(ethylene-co-vinyl) ⇒ {comonomer} + ethylene by radical copolymerisation",
                f"{comonomer} ⇒ commercial vinyl monomer",
            ]
            if comonomer
            else []
        ),
        reagents=["ethylene", "radical initiator, under pressure"],
        precedent=PRECEDENT.get("ethylene copolymer"),
        sa_score=sa,
        sa_note=interpret_sa(sa),
        concerns=[
            "an alternating ethylene-spaced sequence is an idealisation: real radical "
            "copolymerisation gives a statistical distribution set by the monomer feed "
            "and reactivity ratios, so the stored unit describes an average rather than "
            "a sequence anyone can make exactly",
            "ethylene copolymerisation needs pressure equipment, not the aqueous "
            "radical chemistry the other routes here use",
        ],
        derivation=derivation,
    )
    return route


def _assess_ring_opening(mol: Chem.Mol, name: str) -> tuple[Confidence, str | None, str]:
    """Ring-opening needs the right ring, at a size that actually opens."""
    if name == str(Mechanism.RING_OPENING_EPOXIDE):
        if mol.HasSubstructMatch(_EPOXIDE_RING):
            return (
                "established",
                "epoxide",
                "a three-membered epoxide: ring strain drives anionic or cationic "
                "ring-opening, the route to poly(ethylene glycol)",
            )
        if _OXETANE_RING is not None and mol.HasSubstructMatch(_OXETANE_RING):
            return (
                "plausible",
                "oxetane",
                "a four-membered oxetane: cationic ring-opening works, though it is "
                "much less common than epoxide chemistry",
            )
        return ("refused", None, "no epoxide or oxetane ring in the recovered monomer")

    if name == str(Mechanism.RING_OPENING_LACTONE):
        size = _ring_size_of_match(mol, _LACTONE_RING)
        if size is None:
            return ("refused", None, "no lactone ring in the recovered monomer")
        if size in (4, 5, 6, 7):
            return (
                "established",
                "lactone",
                f"a {size}-membered lactone: ring-opening polymerisation is the route "
                "to PLA, PCL and PLGA",
            )
        return (
            "speculative",
            "lactone",
            f"a {size}-membered lactone. Macrolactones do polymerise, but slowly and "
            "with enzyme or metal catalysis; this is not routine chemistry",
        )

    if name == str(Mechanism.RING_OPENING_LACTAM):
        size = _ring_size_of_match(mol, _LACTAM_RING)
        if size is None:
            return ("refused", None, "no lactam ring in the recovered monomer")
        match = mol.GetSubstructMatch(_LACTAM_RING)
        if match and any(mol.GetAtomWithIdx(i).GetIsAromatic() for i in match):
            return (
                "refused",
                None,
                "the amide is part of an aromatic ring. Aromatic amides are stabilised "
                "by the ring and do not ring-open: this is not a lactam in the "
                "polymerisable sense",
            )
        ring_info = mol.GetRingInfo()
        fused = any(ring_info.NumAtomRings(i) > 1 for i in match)
        if fused:
            return (
                "speculative",
                "lactam",
                "the amide sits in a fused ring system, where ring-opening would have "
                "to break a bond shared with another ring — possible to draw, not "
                "established chemistry",
            )
        if size in (4, 6, 7, 8):
            return (
                "established",
                "lactam",
                f"a {size}-membered non-aromatic lactam: ring-opening gives nylon-type "
                "polyamides, and 2-oxazolines give poly(2-oxazoline) excipients",
            )
        if size == 5:
            return (
                "speculative",
                "lactam",
                "a five-membered lactam (γ-lactam). These are notoriously reluctant to "
                "ring-open: 2-pyrrolidone polymerises only under forcing conditions, "
                "which is why poly(vinylpyrrolidone) is made from the N-vinyl monomer "
                "instead",
            )
        return ("speculative", "lactam", f"a {size}-membered lactam, outside the usual range")

    if name == str(Mechanism.RING_OPENING_CYCLIC_CARBONATE):
        if mol.HasSubstructMatch(_CYCLIC_CARBONATE_RING):
            return (
                "established",
                "cyclic carbonate",
                "a cyclic carbonate: ring-opening gives degradable polycarbonates such "
                "as poly(trimethylene carbonate)",
            )
        return ("refused", None, "no cyclic carbonate ring in the recovered monomer")

    return ("speculative", None, f"no ring-opening check implemented for {name}")


def _assess_step_growth(mol: Chem.Mol, name: str) -> tuple[Confidence, str | None, str]:
    """Step growth needs two genuine, like functional groups."""
    if name == str(Mechanism.STEP_GROWTH_DIOL):
        aliphatic = len(mol.GetSubstructMatches(_ALIPHATIC_OH))
        total_oh = len(mol.GetSubstructMatches(Chem.MolFromSmarts("[OX2H1]")))
        if aliphatic >= 2:
            return (
                "established",
                "aliphatic diol",
                f"{aliphatic} aliphatic hydroxyl groups on sp3 carbon: these esterify "
                "and carbamoylate, which is standard polyester and polyurethane "
                "chemistry",
            )
        if total_oh >= 2:
            # The case that produced the oxime/imidic-acid "diol".
            return (
                "refused",
                None,
                (
                    f"the monomer has {total_oh} O–H groups but only {aliphatic} of them "
                    "are aliphatic alcohols. Phenols, enols, oximes, imidic acids and "
                    "carboxylic acids all present an O–H, and none of them behaves as a "
                    "diol in a polyesterification — treating them as one is the most "
                    "common way to draw a polymer that cannot be made"
                ),
            )
        return (
            "refused",
            None,
            f"only {aliphatic} aliphatic hydroxyl group(s); step growth needs two",
        )

    amines = len(mol.GetSubstructMatches(_PRIMARY_ALIPHATIC_AMINE))
    if amines >= 2:
        return (
            "established",
            "aliphatic diamine",
            f"{amines} primary aliphatic amines: step growth with a diacid gives polyamides",
        )
    aromatic_amines = len(mol.GetSubstructMatches(Chem.MolFromSmarts("[NX3;H2]c")))
    if aromatic_amines >= 2:
        return (
            "plausible",
            "aromatic diamine",
            "two aromatic amines: these do polymerise to aramids and polyimides, but "
            "the products are rigid and insoluble, which is the opposite of what an "
            "excipient needs",
        )
    return (
        "refused",
        None,
        f"only {amines} primary aliphatic amine(s); step growth needs two",
    )


__all__ = [
    "CONFIDENCE_ORDER",
    "PRECEDENT",
    "SynthesisRoute",
    "assess_route",
    "interpret_sa",
    "recover_monomer",
    "synthetic_accessibility",
]

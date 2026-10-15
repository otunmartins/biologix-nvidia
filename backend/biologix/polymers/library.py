"""Seed library of repeat units relevant to biologic formulation.

Family labels are load-bearing: grouped evaluation splits
(:mod:`biologix.surrogate.splits`) hold out whole families, so a model is
never tested on a chemistry it was trained on.

Nothing here is an approval claim. Several of these backbones appear in
marketed excipients, which is a reason to *consider* them, not evidence that a
novel polymer built from them is safe. See
:func:`regulatory_context` for how that distinction is carried through.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LibraryEntry:
    """A curated repeat unit with provenance for why it is in the library."""

    smiles: str
    name: str
    family: str
    # Why a formulation scientist might try this chemistry. A rationale, not
    # a prediction.
    rationale: str
    # Whether a *related* chemistry appears in marketed parenteral products.
    # Deliberately named to prevent it reading as an approval of this polymer.
    related_excipient_precedent: bool = False
    precedent_note: str | None = None


# Backbone chemistries. Attachment points are [*]; exactly two per unit.
SEED_LIBRARY: tuple[LibraryEntry, ...] = (
    LibraryEntry(
        smiles="[*]CCO[*]",
        name="ethylene oxide",
        family="polyether",
        rationale="Highly hydrated, flexible, low protein affinity; the basis of PEG.",
        related_excipient_precedent=True,
        precedent_note="PEG of various molecular weights is used in parenteral products.",
    ),
    LibraryEntry(
        smiles="[*]CC(C)O[*]",
        name="propylene oxide",
        family="polyether",
        rationale="More hydrophobic polyether; the central block of poloxamers.",
        related_excipient_precedent=True,
        precedent_note="Poloxamer 188 contains poly(propylene oxide) blocks.",
    ),
    LibraryEntry(
        smiles="[*]CC(O)CO[*]",
        name="glycerol",
        family="polyether",
        rationale="Hydroxyl-rich polyether; hyperbranched polyglycerol analogue.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])N1CCCC1=O",
        name="vinylpyrrolidone",
        family="polyvinyl-amide",
        rationale="Strong hydrogen-bond acceptor; povidone is a known stabiliser.",
        related_excipient_precedent=True,
        precedent_note="Povidone (PVP) K12-K17 appears in some injectables.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])O",
        name="vinyl alcohol",
        family="polyvinyl-alcohol",
        rationale="Dense hydroxyl display; interfacial activity.",
        related_excipient_precedent=True,
        precedent_note="PVA is used in ophthalmic and some parenteral formulations.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(N)=O",
        name="acrylamide",
        family="polyacrylamide",
        rationale="Amide hydrogen bonding without charge.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(=O)NC(C)C",
        name="N-isopropylacrylamide",
        family="polyacrylamide",
        rationale="Thermoresponsive; probes hydrophobic-hydrophilic balance.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(=O)N(C)C",
        name="N,N-dimethylacrylamide",
        family="polyacrylamide",
        rationale="Non-donating amide; isolates acceptor-only hydrogen bonding.",
    ),
    LibraryEntry(
        smiles="[*]NCC(=O)[*]",
        name="glycine (polypeptoid backbone)",
        family="polyamide",
        rationale="Peptide-like backbone; biodegradable.",
    ),
    LibraryEntry(
        smiles="[*]N(C)CC(=O)[*]",
        name="sarcosine",
        family="polypeptoid",
        rationale="Polysarcosine is studied as a PEG alternative with low immunogenicity.",
    ),
    LibraryEntry(
        smiles="[*]OCC(=O)[*]",
        name="glycolide",
        family="polyester",
        rationale="Hydrolysable polyester; PLGA component.",
        related_excipient_precedent=True,
        precedent_note="PLGA is used in depot formulations, not as a stabiliser in solution.",
    ),
    LibraryEntry(
        smiles="[*]OC(C)C(=O)[*]",
        name="lactide",
        family="polyester",
        rationale="Hydrolysable, more hydrophobic polyester.",
        related_excipient_precedent=True,
        precedent_note="Poly(lactic acid) blocks appear in approved depot products.",
    ),
    LibraryEntry(
        smiles="[*]CCN(C)C(=O)[*]",
        name="2-methyl-2-oxazoline",
        family="polyoxazoline",
        rationale="Poly(2-oxazoline)s are a well-studied PEG alternative.",
    ),
    LibraryEntry(
        smiles="[*]CCN(CC)C(=O)[*]",
        name="2-ethyl-2-oxazoline",
        family="polyoxazoline",
        rationale="Slightly more hydrophobic oxazoline for balance tuning.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(=O)OCCO",
        name="hydroxyethyl methacrylate-like",
        family="polyacrylate",
        rationale="Hydroxyl-terminated ester side chain; hydrogel chemistry.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(=O)OCCN(C)C",
        name="dimethylaminoethyl acrylate",
        family="polyacrylate-cationic",
        rationale="Weakly basic side chain; probes electrostatic interaction with acidic patches.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(=O)[O-]",
        name="acrylate (anionic)",
        family="polyacrylate-anionic",
        rationale="Anionic charge; probes electrostatic complementarity.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])CO",
        name="allyl alcohol unit",
        family="polyvinyl-alcohol",
        rationale="Primary hydroxyl on a short spacer.",
    ),
    LibraryEntry(
        smiles="[*]CCOCCO[*]",
        name="diethylene glycol",
        family="polyether",
        rationale="Longer ether spacer; tests spacer-length effects at fixed chemistry.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(=O)NCCO",
        name="hydroxyethyl acrylamide",
        family="polyacrylamide",
        rationale="Amide plus hydroxyl: donor and acceptor on one unit.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])S(=O)(=O)C",
        name="vinyl methyl sulfone unit",
        family="polysulfone",
        rationale="Strong dipole, non-ionic; unusual polarity for contrast.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])n1ccnc1",
        name="vinylimidazole",
        family="polyvinyl-azole",
        rationale="pH-responsive near physiological pH; metal coordination.",
    ),
    LibraryEntry(
        smiles="[*]CC([*])C(=O)N1CCOCC1",
        name="acryloylmorpholine",
        family="polyacrylamide",
        rationale="Cyclic tertiary amide; poly(acryloylmorpholine) is a studied PEG alternative.",
    ),
    LibraryEntry(
        smiles="[*]C(=O)c1ccc([*])cc1",
        name="aromatic ketone linkage",
        family="aromatic",
        rationale="Rigid aromatic contrast case; expected to be a poor excipient.",
    ),
)


def library_by_family() -> dict[str, list[LibraryEntry]]:
    out: dict[str, list[LibraryEntry]] = {}
    for entry in SEED_LIBRARY:
        out.setdefault(entry.family, []).append(entry)
    return out


def families() -> list[str]:
    return sorted({e.family for e in SEED_LIBRARY})


def find(smiles: str) -> LibraryEntry | None:
    for entry in SEED_LIBRARY:
        if entry.smiles == smiles:
            return entry
    return None


def regulatory_context(smiles: str) -> dict[str, object]:
    """What can and cannot be said about this chemistry's regulatory status.

    This never returns an approval status for a polymer. The most it reports
    is that a *related* chemistry has precedent, together with an explicit
    statement that this does not transfer.
    """
    entry = find(smiles)
    precedent = bool(entry and entry.related_excipient_precedent)
    return {
        "related_excipient_precedent": precedent,
        "precedent_note": entry.precedent_note if entry else None,
        "approval_status": "not applicable — this is a novel polymer specification",
        "caveat": (
            "Precedent for a related chemistry is not precedent for this polymer. "
            "Excipient acceptability depends on molecular weight distribution, "
            "end groups, residual monomer and catalyst, route of administration "
            "and dose. None of these are established here."
        ),
    }

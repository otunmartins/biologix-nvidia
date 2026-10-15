"""Trimming a UniProt precursor to the molecule that is actually formulated.

A UniProt entry is the precursor. Importing it whole gave preproinsulin — 110
residues, one chain, a third of it propeptide, no disulfides and therefore six
free thiols — in place of the 51-residue two-chain hormone. Every sequence
property the formulation profile computes was then computed over the wrong
molecule.

These tests run offline against the real feature payloads, so they pin the
mapping arithmetic rather than UniProt's uptime.
"""

from __future__ import annotations

from typing import Any

import pytest

from biologix.targets.imports import (
    _assembly_warnings,
    _disulfides_from_features,
    _mature_chains_from_features,
)
from biologix.targets.models import Chain

# P01308, exactly as the REST API returns it.
PREPROINSULIN = (
    "MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGG"
    "GPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN"
)

INSULIN_FEATURES: list[dict[str, Any]] = [
    {"type": "Signal", "location": {"start": {"value": 1}, "end": {"value": 24}}},
    {
        "type": "Peptide",
        "description": "Insulin B chain",
        "location": {"start": {"value": 25}, "end": {"value": 54}},
    },
    {
        "type": "Propeptide",
        "description": "C peptide",
        "location": {"start": {"value": 57}, "end": {"value": 87}},
    },
    {
        "type": "Peptide",
        "description": "Insulin A chain",
        "location": {"start": {"value": 90}, "end": {"value": 110}},
    },
    {
        "type": "Disulfide bond",
        "description": "Interchain (between B and A chains)",
        "location": {"start": {"value": 31}, "end": {"value": 96}},
    },
    {
        "type": "Disulfide bond",
        "description": "Interchain (between B and A chains)",
        "location": {"start": {"value": 43}, "end": {"value": 109}},
    },
    {
        "type": "Disulfide bond",
        "location": {"start": {"value": 95}, "end": {"value": 100}},
    },
]


def _insulin() -> tuple[list[Chain], list[tuple[int, int, str]]]:
    chains, spans, _ = _mature_chains_from_features(PREPROINSULIN, INSULIN_FEATURES)
    return chains, spans


def test_the_precursor_is_trimmed_to_the_two_mature_chains() -> None:
    chains, _ = _insulin()
    assert [(c.chain_id, c.length) for c in chains] == [("B", 30), ("A", 21)]
    assert sum(c.length for c in chains) == 51, "the hormone is 51 residues, not 110"


def test_the_mature_sequences_match_the_curated_registry_exactly() -> None:
    """The strongest check available: hand-curated insulin, reproduced.

    The registry entry was written from the literature, independently of the
    UniProt annotations, so agreement means the trimming arithmetic is right.
    """
    from biologix.targets.registry import HUMAN_INSULIN

    chains, _ = _insulin()
    by_id = {c.chain_id: c.sequence for c in chains}
    for curated in HUMAN_INSULIN.chains:
        assert by_id[curated.chain_id] == curated.sequence


def test_chain_ids_come_from_uniprots_description_not_from_order() -> None:
    """The B chain precedes the A chain in the precursor.

    Numbering in sequence order would label the B chain "A", and every
    published reference to A6-A11 would then point at the wrong chain.
    """
    chains, _ = _insulin()
    assert chains[0].chain_id == "B", "the first mature chain is the B chain"
    assert chains[0].length == 30
    assert chains[1].chain_id == "A"
    assert chains[1].length == 21


def test_disulfides_are_renumbered_onto_the_mature_chains() -> None:
    """Precursor 31-96, 43-109 and 95-100 are B7-A7, B19-A20 and A6-A11."""
    chains, spans = _insulin()
    modifications, warnings = _disulfides_from_features(INSULIN_FEATURES, spans, chains)

    assert warnings == []
    sites = {tuple(sorted(m.sites)) for m in modifications}
    assert sites == {
        (("A", 7), ("B", 7)),
        (("A", 20), ("B", 19)),
        (("A", 6), ("A", 11)),
    }


def test_the_renumbered_disulfides_match_the_curated_registry() -> None:
    from biologix.targets.registry import HUMAN_INSULIN

    chains, spans = _insulin()
    modifications, _ = _disulfides_from_features(INSULIN_FEATURES, spans, chains)
    assert {tuple(sorted(m.sites)) for m in modifications} == {
        tuple(sorted(m.sites)) for m in HUMAN_INSULIN.modifications
    }


def test_the_disulfide_keeps_its_precursor_positions_for_audit() -> None:
    chains, spans = _insulin()
    modifications, _ = _disulfides_from_features(INSULIN_FEATURES, spans, chains)
    assert any("UniProt 31-96" in (m.description or "") for m in modifications)


def test_trimming_leaves_no_free_thiol_for_insulin() -> None:
    """The consequence that matters: six cysteines, three disulfides, none free.

    On the untrimmed precursor all six counted as free thiols, which made the
    formulation profile penalise Michael-acceptor chemistry for a molecule that
    has no free thiol at all.
    """
    from biologix.targets.formulation import free_cysteines
    from biologix.targets.models import Biologic, BiologicClass

    chains, spans = _insulin()
    modifications, _ = _disulfides_from_features(INSULIN_FEATURES, spans, chains)
    biologic = Biologic(
        name="insulin",
        biologic_class=BiologicClass.PEPTIDE,
        chains=tuple(chains),
        modifications=tuple(modifications),
    )
    assert free_cysteines(biologic) == 0


def test_a_disulfide_outside_the_mature_chains_is_dropped_with_a_warning() -> None:
    """A bond whose partner was cleaved away is not bent to fit."""
    chains, spans = _insulin()
    features = [
        {
            "type": "Disulfide bond",
            # 60 falls in the C peptide, which maturation removes.
            "location": {"start": {"value": 60}, "end": {"value": 96}},
        }
    ]
    modifications, warnings = _disulfides_from_features(features, spans, chains)
    assert modifications == []
    assert warnings and "outside the mature chains" in warnings[0]


def test_an_entry_with_no_chain_annotation_yields_no_chains() -> None:
    """Then the caller keeps the precursor rather than guessing at one."""
    chains, spans, warnings = _mature_chains_from_features(
        PREPROINSULIN,
        [{"type": "Signal", "location": {"start": {"value": 1}, "end": {"value": 24}}}],
    )
    assert chains == []
    assert spans == []


def test_an_unbounded_feature_is_skipped_rather_than_guessed_at() -> None:
    chains, _, _ = _mature_chains_from_features(
        PREPROINSULIN,
        [
            {
                "type": "Peptide",
                "description": "unknown end",
                "location": {"start": {"value": 25}, "end": {}},
            }
        ],
    )
    assert chains == []


def test_a_feature_beyond_the_sequence_is_skipped() -> None:
    chains, _, _ = _mature_chains_from_features(
        PREPROINSULIN,
        [
            {
                "type": "Peptide",
                "location": {"start": {"value": 1}, "end": {"value": 9999}},
            }
        ],
    )
    assert chains == []


# -- is it really one molecule? -------------------------------------------


def test_disulfide_linked_chains_raise_no_assembly_warning() -> None:
    """Insulin's chains are covalently one hormone, so nothing is flagged."""
    chains, spans = _insulin()
    modifications, _ = _disulfides_from_features(INSULIN_FEATURES, spans, chains)
    assert _assembly_warnings(chains, modifications, "Insulin") == []


def test_unlinked_mature_chains_are_flagged_as_possibly_separate_products() -> None:
    """Proglucagon's peptides are different drugs sharing a precursor.

    Stitching them together silently invents a 296-residue molecule that does
    not exist, so the import has to say so.
    """
    chains = [
        Chain(chain_id="A", sequence="HSQGTFTSDYSKYLDSRRAQDFVQWLMNT", description="Glucagon"),
        Chain(chain_id="B", sequence="HAEGTFTSDVSSYLEGQAAKEFIAWLVKGRG", description="GLP-1"),
    ]
    warnings = _assembly_warnings(chains, [], "Pro-glucagon")
    assert warnings
    assert "different biologics" in warnings[0]
    assert "none of them are" in warnings[0]


def test_a_single_mature_chain_is_never_flagged() -> None:
    chains = [Chain(chain_id="A", sequence="GIVEQCCTSICSLYQLENYCN")]
    assert _assembly_warnings(chains, [], "something") == []


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Insulin A chain", "A"),
        ("Insulin B chain", "B"),
        ("chain C", "C"),
        ("", "A"),
        ("Glucagon", "A"),
    ],
)
def test_chain_ids_are_read_from_the_description_where_one_is_given(
    description: str, expected: str
) -> None:
    from biologix.targets.imports import _chain_id_for

    assert _chain_id_for(description, 0, set()) == expected


def test_a_taken_chain_id_falls_through_to_a_free_letter() -> None:
    from biologix.targets.imports import _chain_id_for

    assert _chain_id_for("Insulin A chain", 1, {"A"}) == "B"

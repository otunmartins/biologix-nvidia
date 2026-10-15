"""Biologix-NVIDIA: polymer excipient discovery for biologic formulations.

This is a *research prototype*. Nothing it produces is a validated prediction
of drug stability, shelf life or clinical performance. Three categories of
output are kept strictly distinct throughout the codebase and in every report:

``hypothesis``
    Generated or proposed, not computed. Candidate structures from a
    generator fall here.
``computed``
    The result of running software on inputs: descriptors, filters,
    simulation observables, model outputs. Reproducible given the recorded
    manifest; not a measurement of reality.
``measured``
    Imported from a laboratory assay, with provenance.

See :mod:`biologix.provenance` for how this is recorded.
"""

__version__ = "0.1.0"

# Evidence tiers, used by reports and the API so the distinction cannot be
# lost in transit.
EVIDENCE_HYPOTHESIS = "hypothesis"
EVIDENCE_COMPUTED = "computed"
EVIDENCE_MEASURED = "measured"

EVIDENCE_TIERS = (EVIDENCE_HYPOTHESIS, EVIDENCE_COMPUTED, EVIDENCE_MEASURED)

__all__ = [
    "__version__",
    "EVIDENCE_HYPOTHESIS",
    "EVIDENCE_COMPUTED",
    "EVIDENCE_MEASURED",
    "EVIDENCE_TIERS",
]

"""Generate-and-screen, executed for real against a temporary database.

These run the baseline generator and RDKit, so they are real work rather than
mocks — that is the point, since what is being tested is that a run's stored
record matches what the pipeline actually did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biologix.errors import AdapterUnavailable
from biologix.jobs.pipeline import cohort_distances, execute_run
from biologix.settings import Settings
from biologix.store import create_run, get_run
from biologix.store.database import reset_engine_cache


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    reset_engine_cache()
    yield Settings(
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        BIOLOGIX_DATABASE_URL=f"sqlite+pysqlite:///{(tmp_path / 'p.db').as_posix()}",
    )
    reset_engine_cache()


def _queued(settings: Settings, generator: str = "baseline") -> str:
    return create_run(
        target_key="human-insulin",
        target_name="human insulin",
        generator=generator,
        n_requested=6,
        constraint_set="aqueous-parenteral-default",
        seed=11,
        url=settings.database_url,
    )


def test_a_baseline_run_stores_candidates_discards_and_manifest(settings: Settings) -> None:
    run_id = _queued(settings)
    execute_run(run_id, generator="baseline", n=6, constraint_set=None, seed=11, settings=settings)

    run = get_run(run_id, url=settings.database_url)
    assert run is not None
    assert run["status"] == "succeeded", run["error"]
    assert run["n_candidates"] > 0
    assert run["manifest"]["biologix_version"]
    assert run["statistics"]["n_proposed"] >= run["n_candidates"]
    # Discards carry a stage and codes, so a user can see why each was dropped.
    for discard in run["discards"]:
        assert discard["stage"] in {"polymerisation_rules", "duplicate"}
        assert discard["codes"] or discard["duplicate_of"]


def test_every_candidate_carries_the_oligomer_its_descriptors_describe(
    settings: Settings,
) -> None:
    run_id = _queued(settings)
    execute_run(run_id, generator="baseline", n=6, constraint_set=None, seed=11, settings=settings)

    run = get_run(run_id, url=settings.database_url)
    assert run is not None
    for candidate in run["candidates"]:
        if candidate["descriptor_error"]:
            continue
        assert candidate["oligomer_smiles"]
        assert candidate["screening_dp"]
        assert candidate["descriptors"]


def test_the_run_records_which_backend_actually_ran(settings: Settings) -> None:
    """nvMolKit is requested; the record must say what really did the work."""
    run_id = _queued(settings)
    execute_run(run_id, generator="baseline", n=6, constraint_set=None, seed=11, settings=settings)

    run = get_run(run_id, url=settings.database_url)
    assert run is not None
    backend = run["diversity"]["cohort_distance_backend"]
    assert backend["requested"] == "gpu"
    # On a machine without nvMolKit this is rdkit-cpu with the reason attached,
    # which is the honest answer rather than a claim of GPU acceleration.
    assert backend["actual"] in {"rdkit-cpu", "nvmolkit-gpu"}
    if backend["actual"] == "rdkit-cpu":
        assert backend["fell_back"] is True
        assert backend["notes"]


def test_progress_is_reported_in_order(settings: Settings) -> None:
    run_id = _queued(settings)
    seen: list[tuple[str, float]] = []
    execute_run(
        run_id,
        generator="baseline",
        n=4,
        constraint_set=None,
        seed=11,
        settings=settings,
        progress=lambda stage, fraction: seen.append((stage, fraction)),
    )

    stages = [stage for stage, _ in seen]
    assert stages[0] == "generating"
    assert "screening" in stages
    assert stages[-1] == "complete"
    fractions = [fraction for _, fraction in seen]
    assert fractions == sorted(fractions)


def test_an_unknown_constraint_set_fails_the_run_with_a_reason(settings: Settings) -> None:
    run_id = _queued(settings)
    execute_run(
        run_id, generator="baseline", n=4, constraint_set="nope", seed=11, settings=settings
    )

    run = get_run(run_id, url=settings.database_url)
    assert run is not None
    assert run["status"] == "failed"
    assert "nope" in run["error"]
    # The available names are in the message, so the user can fix it.
    assert "aqueous-parenteral-default" in run["error"]


def test_an_unavailable_adapter_fails_rather_than_falling_back(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run labelled genmol that the baseline produced would be a false record."""
    run_id = _queued(settings, generator="genmol")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AdapterUnavailable(
            "bionemo-genmol", "no API key and no local container", remedy="set NVIDIA_API_KEY"
        )

    monkeypatch.setattr("biologix.jobs.pipeline._generate", refuse)
    execute_run(run_id, generator="genmol", n=4, constraint_set=None, seed=11, settings=settings)

    run = get_run(run_id, url=settings.database_url)
    assert run is not None
    assert run["status"] == "failed"
    assert "bionemo-genmol" in run["error"]
    assert "NVIDIA_API_KEY" in run["error"]
    assert run["n_candidates"] == 0


def test_an_unexpected_error_is_recorded_not_swallowed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker thread that raises would otherwise leave the run queued forever."""
    run_id = _queued(settings)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the GPU fell over")

    monkeypatch.setattr("biologix.jobs.pipeline._generate", explode)
    execute_run(run_id, generator="baseline", n=4, constraint_set=None, seed=11, settings=settings)

    run = get_run(run_id, url=settings.database_url)
    assert run is not None
    assert run["status"] == "failed"
    assert "RuntimeError" in run["error"]


def test_cohort_distance_needs_a_cohort() -> None:
    """One candidate has no distance from anything; it must not be given 0.0."""
    distances, backend = cohort_distances({"a": "CCO"}, prefer_gpu=False)
    assert distances == {}
    assert backend["actual"] == "none"


def test_cohort_distance_is_symmetric_and_bounded() -> None:
    distances, _ = cohort_distances({"a": "CCO", "b": "CCCCCCCCN", "c": "CCO"}, prefer_gpu=False)
    assert set(distances) == {"a", "b", "c"}
    assert all(0.0 <= v <= 1.0 for v in distances.values())
    # Identical molecules sit at the same distance from the cohort.
    assert distances["a"] == distances["c"]
    # And the odd one out is further from the cohort than the pair.
    assert distances["b"] > distances["a"]

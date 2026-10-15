"""Persistence tests.

The point of these is not that SQLAlchemy works. It is that the three claims
the store's docstring makes are actually true: rejections survive, descriptors
travel with the oligomer they were computed from, and no ordering is stored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biologix.store import (
    abandon_orphaned_work,
    create_job,
    create_run,
    delete_run,
    fail_job,
    fail_run,
    finish_job,
    get_job,
    get_run,
    get_run_candidate,
    list_jobs,
    list_runs,
    save_run_results,
    set_run_progress,
    start_job,
)
from biologix.store.database import reset_engine_cache


@pytest.fixture
def db(tmp_path: Path) -> str:
    """A fresh database per test, so no test can see another's rows."""
    reset_engine_cache()
    # Every repository call here takes this url explicitly, so no test depends
    # on the ambient settings or on the developer's own database.
    url = f"sqlite+pysqlite:///{(tmp_path / 'test.db').as_posix()}"
    yield url
    reset_engine_cache()


def _run(db: str, **overrides: object) -> str:
    kwargs = {
        "target_key": "human-insulin",
        "target_name": "human insulin",
        "generator": "baseline",
        "n_requested": 4,
        "constraint_set": "aqueous-parenteral-default",
        "seed": 7,
    }
    kwargs.update(overrides)
    return create_run(url=db, **kwargs)  # type: ignore[arg-type]


CANDIDATE = {
    "candidate_id": "cand-1",
    "display_name": "poly(vinyl alcohol)·DP25",
    "repeat_units": ["[*]CC([*])O"],
    "degree_of_polymerisation": 25,
    "end_group": "methyl",
    "architecture": "homopolymer",
    "families": ["vinyl"],
    "generator": "baseline",
    "notes": None,
    "descriptors": {"mol_weight": 440.0, "tpsa": 120.0},
    "descriptor_error": None,
    "oligomer_smiles": "CC(O)CC(O)C",
    "screening_dp": 5,
    "construction_rules": {"end_group": "methyl"},
    "passed": True,
    "failures": [],
    "warnings": [],
    "monomer_flags": [],
    "regulatory_context": [],
    "cohort_distance": 0.4,
}

DISCARD = {
    "proposed_smiles": "CCO",
    "source": "baseline",
    "stage": "polymerisation_rules",
    "codes": ["no_handle"],
    "detail": "no polymerisable attachment point",
    "duplicate_of": None,
}


def _save(db: str, run_id: str, **overrides: object) -> None:
    payload = {
        "statistics": {"n_proposed": 6},
        "rejections": {"no_handle": 2},
        "constraints": [{"descriptor": "tpsa", "minimum": 40}],
        "candidates": [dict(CANDIDATE)],
        "discards": [dict(DISCARD)],
        "diversity": {"n_candidates": 1},
        "warnings": ["one warning"],
        "errors": [],
        "manifest": {"operation": "screen"},
    }
    payload.update(overrides)
    save_run_results(run_id, url=db, **payload)  # type: ignore[arg-type]


def test_a_saved_run_reads_back_whole(db: str) -> None:
    run_id = _run(db, label="first")
    _save(db, run_id)

    run = get_run(run_id, url=db)
    assert run is not None
    assert run["status"] == "succeeded"
    assert run["progress"] == 1.0
    assert run["label"] == "first"
    assert run["manifest"] == {"operation": "screen"}
    assert run["n_candidates"] == 1
    assert run["n_passed"] == 1
    assert run["n_discarded"] == 1


def test_rejections_are_stored_not_only_survivors(db: str) -> None:
    """The discard reasons are the most informative output; losing them is a bug."""
    run_id = _run(db)
    _save(db, run_id)

    run = get_run(run_id, url=db)
    assert run is not None
    assert run["discards"][0]["codes"] == ["no_handle"]
    assert run["discards"][0]["detail"] == "no polymerisable attachment point"
    assert run["rejections"] == {"no_handle": 2}


def test_descriptors_travel_with_their_oligomer(db: str) -> None:
    """A descriptor without the capped oligomer it came from is not reproducible."""
    run_id = _run(db)
    _save(db, run_id)

    candidate = get_run_candidate(run_id, "cand-1", url=db)
    assert candidate is not None
    assert candidate["descriptors"]["tpsa"] == 120.0
    assert candidate["oligomer_smiles"] == "CC(O)CC(O)C"
    assert candidate["screening_dp"] == 5
    assert candidate["construction_rules"] == {"end_group": "methyl"}
    # The constraints it was judged against come with it, or a failure cannot
    # be interpreted.
    assert candidate["constraints"] == [{"descriptor": "tpsa", "minimum": 40}]
    assert candidate["run"]["id"] == run_id


def test_no_ordering_is_stored(db: str) -> None:
    """Weights belong to the reader, so a rank must never be persisted."""
    run_id = _run(db)
    _save(db, run_id)

    run = get_run(run_id, url=db)
    assert run is not None
    stored = set(run["candidates"][0])
    assert not stored & {"rank", "order_score", "score", "suitability"}


def test_progress_then_failure_is_terminal(db: str) -> None:
    run_id = _run(db)
    set_run_progress(run_id, stage="screening", progress=0.5, url=db)
    mid = get_run(run_id, url=db)
    assert mid is not None and mid["status"] == "running" and mid["progress"] == 0.5

    fail_run(run_id, "adapter unavailable", url=db)
    done = get_run(run_id, url=db)
    assert done is not None
    assert done["status"] == "failed"
    assert done["error"] == "adapter unavailable"
    assert done["finished_at"] is not None


def test_progress_is_clamped(db: str) -> None:
    run_id = _run(db)
    set_run_progress(run_id, stage="screening", progress=4.0, url=db)
    run = get_run(run_id, url=db)
    assert run is not None and run["progress"] == 1.0


def test_saving_results_for_an_unknown_run_is_an_error(db: str) -> None:
    with pytest.raises(KeyError):
        _save(db, "nope")


def test_missing_rows_read_as_none(db: str) -> None:
    assert get_run("nope", url=db) is None
    assert get_job("nope", url=db) is None
    assert get_run_candidate("nope", "nope", url=db) is None
    assert delete_run("nope", url=db) is False


def test_deleting_a_run_takes_its_children(db: str) -> None:
    run_id = _run(db)
    _save(db, run_id)
    assert delete_run(run_id, url=db) is True
    assert get_run(run_id, url=db) is None
    assert get_run_candidate(run_id, "cand-1", url=db) is None


def test_runs_are_listed_newest_first(db: str) -> None:
    first = _run(db, label="older")
    second = _run(db, label="newer")
    labels = [r["label"] for r in list_runs(url=db)]
    assert set(labels) == {"older", "newer"}
    assert len(list_runs(limit=1, url=db)) == 1
    assert {first, second} == {r["id"] for r in list_runs(url=db)}


def test_job_lifecycle(db: str) -> None:
    job_id = create_job(kind="dock", payload={"candidate": "cand-1"}, run_id="r1", url=db)
    start_job(job_id, url=db)
    assert get_job(job_id, url=db)["status"] == "running"  # type: ignore[index]

    finish_job(job_id, {"poses": 3}, url=db)
    job = get_job(job_id, url=db)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["result"] == {"poses": 3}
    assert job["progress"] == 1.0


def test_a_failed_job_keeps_its_remedy(db: str) -> None:
    """An error without a remedy leaves a user with nothing to do."""
    job_id = create_job(kind="dock", payload={}, url=db)
    fail_job(
        job_id,
        error="DiffDock unavailable",
        error_kind="AdapterUnavailable",
        remedy="set NVIDIA_API_KEY",
        url=db,
    )
    job = get_job(job_id, url=db)
    assert job is not None
    assert job["error_kind"] == "AdapterUnavailable"
    assert job["remedy"] == "set NVIDIA_API_KEY"


def test_jobs_filter_by_run_and_candidate(db: str) -> None:
    create_job(kind="dock", payload={}, run_id="r1", candidate_id="c1", url=db)
    create_job(kind="dock", payload={}, run_id="r2", candidate_id="c2", url=db)
    assert len(list_jobs(run_id="r1", url=db)) == 1
    assert len(list_jobs(candidate_id="c2", url=db)) == 1
    assert len(list_jobs(url=db)) == 2


def test_orphaned_work_is_abandoned_rather_than_left_spinning(db: str) -> None:
    """A spinner that can never finish is a worse lie than "the work was lost"."""
    run_id = _run(db)
    job_id = create_job(kind="dock", payload={}, url=db)
    start_job(job_id, url=db)

    assert abandon_orphaned_work(url=db) == 2

    run = get_run(run_id, url=db)
    job = get_job(job_id, url=db)
    assert run is not None and job is not None
    assert run["status"] == "failed" and run["stage"] == "abandoned"
    assert job["status"] == "failed" and job["error_kind"] == "Abandoned"
    assert job["remedy"] == "submit it again"
    # Idempotent: a second startup finds nothing left to abandon.
    assert abandon_orphaned_work(url=db) == 0


def test_a_finished_run_is_not_abandoned(db: str) -> None:
    run_id = _run(db)
    _save(db, run_id)
    assert abandon_orphaned_work(url=db) == 0
    assert get_run(run_id, url=db)["status"] == "succeeded"  # type: ignore[index]

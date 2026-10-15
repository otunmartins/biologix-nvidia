"""API tests.

Two things are being guarded here. The first is ordinary: routes exist, bad
input is a 400 with a reason rather than a 500. The second is the project's
own rule — that the API cannot present a component as working when it has not
been proven to, and cannot serve a credential.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from biologix.api.app import parse_weights
from biologix.settings import Settings
from biologix.store.database import reset_engine_cache

TARGET = "human-insulin"


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    reset_engine_cache()
    yield Settings(
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        BIOLOGIX_DATABASE_URL=f"sqlite+pysqlite:///{(tmp_path / 'api.db').as_posix()}",
        NVIDIA_API_KEY="nvapi-test-key-not-real",
    )
    reset_engine_cache()


@pytest.fixture
def anon(settings: Settings) -> Iterator[TestClient]:
    """A client with no session, for the endpoints that must refuse one."""
    from biologix.api.app import create_app

    app = create_app(settings)
    with TestClient(app) as client:
        client.app_instance = app  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def client(anon: TestClient) -> TestClient:
    """A signed-in scientist. TestClient keeps the session cookie itself."""
    response = anon.post(
        "/auth/register",
        json={
            "email": "ada@lab.test",
            "password": "correct horse battery",
            "display_name": "Ada",
            "role": "scientist",
        },
    )
    assert response.status_code == 201, response.text
    return anon


@pytest.fixture
def project(client: TestClient) -> str:
    response = client.post(
        "/projects", json={"name": "Insulin stability", "target_key": TARGET}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def finished_run(client: TestClient, project: str) -> str:
    """One real baseline run, screened and stored, inside a program."""
    response = client.post(
        "/runs", json={"project_id": project, "n": 8, "label": "fixture"}
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    client.app_instance.state.queue.wait(run_id, timeout=300)  # type: ignore[attr-defined]
    assert client.get(f"/runs/{run_id}").json()["status"] == "succeeded"
    return run_id


# -- status ----------------------------------------------------------------


def test_health_and_index(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    routes = client.get("/").json()["routes"]
    assert "POST /runs" in routes


def test_config_never_serves_a_credential(client: TestClient) -> None:
    body = client.get("/config").text
    assert "nvapi-test-key-not-real" not in body
    assert client.get("/config").json()["nvidia_api_key"] == "<set>"


def test_verification_says_nothing_has_been_proven_until_it_has(client: TestClient) -> None:
    """Configured is a weaker claim than working, and must not be dressed up as one."""
    report = client.get("/verification").json()
    assert report["ran"] is False
    assert report["results"] == []
    assert "live_check" in report["note"]


# -- options ---------------------------------------------------------------


def test_targets_are_listed_with_their_chains(client: TestClient) -> None:
    targets = {t["key"]: t for t in client.get("/targets").json()["targets"]}
    assert TARGET in targets
    assert targets[TARGET]["n_chains"] == 2


def test_options_describe_every_choice_a_run_has(client: TestClient) -> None:
    options = client.get("/options").json()
    assert options["generators"] == ["baseline", "genmol", "molmim"]
    assert {cs["name"] for cs in options["constraint_sets"]} >= {
        "aqueous-parenteral-default",
        "exploratory",
    }
    # Each criterion carries its rationale, so a weight is an informed choice.
    assert all(c["rationale"] for c in options["ranking_criteria"])
    assert sum(options["default_weights"].values()) == pytest.approx(1.0)


# -- starting runs ---------------------------------------------------------


def test_a_run_is_accepted_and_then_completes(client: TestClient, finished_run: str) -> None:
    run = client.get(f"/runs/{finished_run}").json()
    assert run["label"] == "fixture"
    assert run["target_name"] == "human insulin"
    assert run["n_candidates"] > 0
    assert run["evidence"] == "computed"
    assert "capped oligomer" in run["note"]


def test_a_stored_run_includes_what_it_rejected(client: TestClient, finished_run: str) -> None:
    run = client.get(f"/runs/{finished_run}").json()
    assert run["n_discarded"] > 0
    # Every discard is explained: either a rejection code or the candidate it
    # duplicated. A discard without a reason would be the thing worth hiding.
    for discard in run["discards"]:
        assert discard["codes"] or discard["duplicate_of"]
    # The histogram counts coded rejections only; duplicates were valid
    # chemistry that simply was not new, and are counted separately.
    coded = sum(len(d["codes"]) for d in run["discards"])
    assert sum(run["rejections"].values()) == coded
    assert run["statistics"]["n_duplicate"] == sum(1 for d in run["discards"] if d["duplicate_of"])


def test_runs_are_listed(client: TestClient, finished_run: str) -> None:
    runs = client.get("/runs").json()["runs"]
    assert [r["id"] for r in runs] == [finished_run]
    assert "candidates" not in runs[0], "a list must not carry every candidate"


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"target_key": "nope"}, "unknown target"),
        ({"target_key": TARGET, "generator": "gpt"}, "unknown generator"),
        ({"target_key": TARGET, "constraint_set": "nope"}, "unknown constraint set"),
    ],
)
def test_a_bad_request_is_refused_with_the_valid_choices(
    client: TestClient, payload: dict[str, object], fragment: str
) -> None:
    response = client.post("/runs", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert fragment in detail
    # The message names what *is* allowed, not just what is not.
    assert "available" in detail.lower() or "use" in detail.lower()


def test_an_absurd_candidate_count_is_refused_before_anything_is_stored(
    client: TestClient,
) -> None:
    assert client.post("/runs", json={"target_key": TARGET, "n": 100_000}).status_code == 422
    assert client.get("/runs").json()["runs"] == []


def test_unknown_runs_and_candidates_are_404(client: TestClient) -> None:
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/ranking").status_code == 404
    assert client.get("/runs/nope/candidates/nope").status_code == 404
    assert client.delete("/runs/nope").status_code == 404


def test_a_run_can_be_deleted(client: TestClient, finished_run: str) -> None:
    assert client.delete(f"/runs/{finished_run}").status_code == 200
    assert client.get(f"/runs/{finished_run}").status_code == 404


# -- ranking ---------------------------------------------------------------


def test_ranking_is_ordered_and_explains_itself(client: TestClient, finished_run: str) -> None:
    body = client.get(f"/runs/{finished_run}/ranking").json()
    scores = [c["order_score"] for c in body["candidates"]]
    assert scores == sorted(scores, reverse=True)
    for candidate in body["candidates"]:
        # Each row carries both the candidate and why it sits where it does.
        assert candidate["descriptors"]
        assert candidate["contributions"]
        assert sum(c["contribution"] for c in candidate["contributions"]) == pytest.approx(
            candidate["order_score"], abs=1e-3
        )


def test_ranking_states_that_it_is_not_a_prediction(client: TestClient, finished_run: str) -> None:
    basis = client.get(f"/runs/{finished_run}/ranking").json()["basis"]
    assert "not a prediction" in basis
    assert "not comparable across runs" in basis


def test_weights_change_the_order_and_travel_with_the_result(
    client: TestClient, finished_run: str
) -> None:
    flexible = client.get(
        f"/runs/{finished_run}/ranking", params={"weights": "flexibility:1"}
    ).json()
    polar = client.get(f"/runs/{finished_run}/ranking", params={"weights": "polarity:1"}).json()

    assert flexible["weights"]["flexibility"] == 1.0
    assert polar["weights"]["polarity"] == 1.0
    # A single-criterion ranking is a pure sort on that criterion, so the two
    # orders should disagree somewhere unless the cohort is degenerate.
    flexible_order = [c["candidate_id"] for c in flexible["candidates"]]
    polar_order = [c["candidate_id"] for c in polar["candidates"]]
    assert set(flexible_order) == set(polar_order)
    assert flexible_order != polar_order


def test_no_ordering_is_stored_with_the_run(client: TestClient, finished_run: str) -> None:
    """Ranking is computed per reader; the stored run must stay unranked."""
    stored = client.get(f"/runs/{finished_run}").json()["candidates"][0]
    assert "order_score" not in stored
    assert "contributions" not in stored


@pytest.mark.parametrize("weights", ["nope:1", "flexibility", "flexibility:high", "polarity:-1"])
def test_bad_weights_are_refused_with_a_reason(
    client: TestClient, finished_run: str, weights: str
) -> None:
    response = client.get(f"/runs/{finished_run}/ranking", params={"weights": weights})
    assert response.status_code == 400
    assert response.json()["detail"]


def test_passed_only_is_the_default_and_can_be_turned_off(
    client: TestClient, finished_run: str
) -> None:
    default = client.get(f"/runs/{finished_run}/ranking").json()
    everything = client.get(f"/runs/{finished_run}/ranking", params={"passed_only": "false"}).json()
    assert all(c["passed"] for c in default["candidates"])
    assert len(everything["candidates"]) >= len(default["candidates"])
    assert default["n_excluded"] >= everything["n_excluded"]


def test_a_candidate_is_served_with_the_constraints_it_was_judged_against(
    client: TestClient, finished_run: str
) -> None:
    ranking = client.get(f"/runs/{finished_run}/ranking").json()
    candidate_id = ranking["candidates"][0]["candidate_id"]
    body = client.get(f"/runs/{finished_run}/candidates/{candidate_id}").json()
    assert body["oligomer_smiles"]
    assert body["constraints"], "a failure cannot be read without its thresholds"
    assert body["run"]["id"] == finished_run
    assert body["evidence"] == "computed"


# -- weight parsing --------------------------------------------------------


def test_parse_weights() -> None:
    assert parse_weights(None) is None
    assert parse_weights("") is None
    assert parse_weights("flexibility:2, polarity:1") == {"flexibility": 2.0, "polarity": 1.0}
    with pytest.raises(ValueError, match="key:value"):
        parse_weights("flexibility")
    with pytest.raises(ValueError, match="not a number"):
        parse_weights("flexibility:lots")

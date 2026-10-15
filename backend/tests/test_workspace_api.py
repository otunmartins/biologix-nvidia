"""The journeys a person actually walks through this app.

Each test below is one step of one journey, exercised through HTTP exactly as
the console does it: sign in, open a program, launch a campaign, triage with a
reason, ask for the NVIDIA work, read the audit trail.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from biologix.settings import Settings
from biologix.store.database import reset_engine_cache

TARGET = "human-insulin"


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    reset_engine_cache()
    yield Settings(
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        BIOLOGIX_DATABASE_URL=f"sqlite+pysqlite:///{(tmp_path / 'ws.db').as_posix()}",
    )
    reset_engine_cache()


@pytest.fixture
def app_and_client(settings: Settings) -> Iterator[tuple[object, TestClient]]:
    from biologix.api.app import create_app

    app = create_app(settings)
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def anon(app_and_client: tuple[object, TestClient]) -> TestClient:
    return app_and_client[1]


@pytest.fixture
def app(app_and_client: tuple[object, TestClient]) -> object:
    return app_and_client[0]


def sign_up(
    client: TestClient,
    email: str = "ada@lab.test",
    name: str = "Ada",
    role: str = "scientist",
) -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery",
            "display_name": name,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


@pytest.fixture
def scientist(anon: TestClient) -> dict[str, object]:
    return sign_up(anon)


@pytest.fixture
def client(anon: TestClient, scientist: dict[str, object]) -> TestClient:
    return anon


@pytest.fixture
def program(client: TestClient) -> str:
    response = client.post(
        "/projects",
        json={
            "name": "Insulin aspart stability",
            "target_key": TARGET,
            "description": "Looking for a surfactant-free stabiliser.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def campaign(app: object, client: TestClient, program: str) -> str:
    response = client.post("/runs", json={"project_id": program, "n": 8})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    app.state.queue.wait(run_id, timeout=300)  # type: ignore[attr-defined]
    assert client.get(f"/runs/{run_id}").json()["status"] == "succeeded"
    return run_id


# -- J1: getting in --------------------------------------------------------


def test_the_app_is_closed_without_a_session(anon: TestClient) -> None:
    for path in ("/projects", "/runs", "/activity", "/availability", "/config", "/targets"):
        response = anon.get(path)
        assert response.status_code == 401, f"{path} answered {response.status_code}"
        assert response.json()["detail"] == "sign in to continue"


def test_health_and_index_stay_open(anon: TestClient) -> None:
    """A liveness probe must not need credentials."""
    assert anon.get("/health").json() == {"status": "ok"}
    index = anon.get("/").json()
    assert index["signed_in_as"] is None


def test_sign_in_sign_out_round_trip(anon: TestClient) -> None:
    user = sign_up(anon)
    assert anon.get("/auth/me").json()["user"]["email"] == "ada@lab.test"
    assert anon.get("/").json()["signed_in_as"] == "ada@lab.test"

    assert anon.post("/auth/logout").status_code == 200
    assert anon.get("/auth/me").status_code == 401

    again = anon.post(
        "/auth/login", json={"email": user["email"], "password": "correct horse battery"}
    )
    assert again.status_code == 200
    assert anon.get("/auth/me").json()["user"]["id"] == user["id"]


def test_the_session_cookie_is_not_readable_by_scripts(anon: TestClient) -> None:
    response = anon.post(
        "/auth/register",
        json={
            "email": "grace@lab.test",
            "password": "correct horse battery",
            "display_name": "Grace",
        },
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_a_bad_password_is_a_401_that_reveals_nothing(anon: TestClient) -> None:
    sign_up(anon)
    response = anon.post("/auth/login", json={"email": "ada@lab.test", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "those credentials do not match an account"


# -- J1: opening a program -------------------------------------------------


def test_a_program_is_created_against_a_real_target(client: TestClient, program: str) -> None:
    detail = client.get(f"/projects/{program}").json()
    assert detail["target_name"] == "human insulin"
    assert detail["is_owner"] is True
    # The target's chains travel with the program, because a 2-chain biologic
    # is a different problem from a 1-chain one.
    assert detail["target"]["n_chains"] == 2
    assert detail["campaigns"] == []
    assert detail["shortlist"] == []


def test_a_program_cannot_be_opened_against_an_unknown_target(client: TestClient) -> None:
    response = client.post("/projects", json={"name": "Nope", "target_key": "xyz"})
    assert response.status_code == 400
    assert "unknown target" in response.json()["detail"]


def test_programs_carry_the_counts_a_card_needs(
    client: TestClient, program: str, campaign: str
) -> None:
    listed = client.get("/projects").json()["projects"]
    assert len(listed) == 1
    card = listed[0]
    assert card["n_campaigns"] == 1
    assert card["n_candidates"] > 0
    assert card["n_shortlisted"] == 0


def test_only_the_owner_can_archive_a_program(
    anon: TestClient, client: TestClient, program: str
) -> None:
    client.post("/auth/logout")
    sign_up(anon, email="reviewer@lab.test", name="Rae", role="reviewer")

    # A reviewer can read any program — that is the point of the role...
    assert anon.get(f"/projects/{program}").status_code == 200
    assert anon.get(f"/projects/{program}").json()["is_owner"] is False
    # ...but cannot archive someone else's.
    refused = anon.post(f"/projects/{program}/archive")
    assert refused.status_code == 403
    assert "owner" in refused.json()["detail"]


# -- J2: running a campaign ------------------------------------------------


def test_a_campaign_inherits_its_program_target(
    client: TestClient, program: str, campaign: str
) -> None:
    run = client.get(f"/runs/{campaign}").json()
    assert run["target_key"] == TARGET
    assert run["project_id"] == program
    assert run["created_by_name"] == "Ada"


def test_a_campaign_cannot_change_the_programs_target(client: TestClient, program: str) -> None:
    """Candidates screened against two different proteins cannot share a history."""
    response = client.post("/runs", json={"project_id": program, "target_key": "glp1-7-37", "n": 4})
    assert response.status_code == 400
    assert "cannot use a different target" in response.json()["detail"]


def test_a_campaign_needs_either_a_program_or_a_target(client: TestClient) -> None:
    response = client.post("/runs", json={"n": 4})
    assert response.status_code == 400
    assert "program or a target" in response.json()["detail"]


def test_a_campaign_is_listed_under_its_program(
    client: TestClient, program: str, campaign: str
) -> None:
    detail = client.get(f"/projects/{program}").json()
    assert [c["id"] for c in detail["campaigns"]] == [campaign]


# -- J3: triage ------------------------------------------------------------


def _first_candidate(client: TestClient, run_id: str) -> str:
    ranking = client.get(f"/runs/{run_id}/ranking").json()
    return ranking["candidates"][0]["candidate_id"]


def test_shortlisting_requires_a_reason(client: TestClient, campaign: str) -> None:
    candidate = _first_candidate(client, campaign)
    blank = client.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={"verdict": "shortlisted", "rationale": ""},
    )
    assert blank.status_code == 422  # pydantic's min_length catches an empty string

    whitespace = client.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={"verdict": "shortlisted", "rationale": "   "},
    )
    assert whitespace.status_code == 400
    assert "needs a reason" in whitespace.json()["detail"]
    assert client.get(f"/runs/{campaign}/decisions").json()["decisions"] == []


def test_an_unknown_verdict_is_refused(client: TestClient, campaign: str) -> None:
    candidate = _first_candidate(client, campaign)
    response = client.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={"verdict": "maybe", "rationale": "undecided"},
    )
    assert response.status_code == 400
    assert "unknown verdict" in response.json()["detail"]


def test_a_shortlisted_candidate_appears_with_its_reason(
    client: TestClient, program: str, campaign: str
) -> None:
    candidate = _first_candidate(client, campaign)
    decision = client.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={
            "verdict": "shortlisted",
            "rationale": "Highest flexibility in the cohort and no monomer flags.",
            "weights": {"flexibility": 0.6, "polarity": 0.4},
            "order_score": 0.91,
        },
    )
    assert decision.status_code == 200, decision.text

    shortlist = client.get(f"/projects/{program}/shortlist").json()["shortlist"]
    assert len(shortlist) == 1
    entry = shortlist[0]
    assert entry["candidate"]["candidate_id"] == candidate
    assert "Highest flexibility" in entry["decision"]["rationale"]
    # The ordering the decider was looking at is kept, so the call can be read
    # back in its original context rather than in today's ordering.
    assert entry["decision"]["weights_at_decision"] == {"flexibility": 0.6, "polarity": 0.4}
    assert entry["decision"]["order_score_at_decision"] == 0.91


def test_a_second_verdict_replaces_the_first(
    client: TestClient, program: str, campaign: str
) -> None:
    """One person cannot hold two contradictory verdicts on one candidate."""
    candidate = _first_candidate(client, campaign)
    for verdict, reason in (
        ("shortlisted", "worth a closer look"),
        ("rejected", "TPSA too low on reflection"),
    ):
        response = client.put(
            f"/runs/{campaign}/candidates/{candidate}/decision",
            json={"verdict": verdict, "rationale": reason},
        )
        assert response.status_code == 200

    decisions = client.get(f"/runs/{campaign}/decisions").json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["verdict"] == "rejected"
    assert decisions[0]["updated_at"] is not None
    assert client.get(f"/projects/{program}/shortlist").json()["shortlist"] == []


def test_two_people_can_disagree_about_one_candidate(
    anon: TestClient, client: TestClient, campaign: str
) -> None:
    candidate = _first_candidate(client, campaign)
    client.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={"verdict": "shortlisted", "rationale": "good flexibility"},
    )
    client.post("/auth/logout")
    sign_up(anon, email="reviewer@lab.test", name="Rae", role="reviewer")
    anon.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={"verdict": "rejected", "rationale": "residual monomer concern"},
    )

    decisions = anon.get(f"/runs/{campaign}/decisions").json()["decisions"]
    assert {d["verdict"] for d in decisions} == {"shortlisted", "rejected"}
    # A ranking shows the reader their own verdict, not a merged one.
    mine = anon.get(f"/runs/{campaign}/ranking").json()["candidates"]
    row = next(c for c in mine if c["candidate_id"] == candidate)
    assert row["my_decision"]["verdict"] == "rejected"


def test_a_decision_can_be_withdrawn(client: TestClient, campaign: str) -> None:
    candidate = _first_candidate(client, campaign)
    client.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={"verdict": "shortlisted", "rationale": "provisional"},
    )
    assert client.delete(f"/runs/{campaign}/candidates/{candidate}/decision").status_code == 200
    assert client.get(f"/runs/{campaign}/decisions").json()["decisions"] == []
    assert client.delete(f"/runs/{campaign}/candidates/{candidate}/decision").status_code == 404


def test_a_decision_on_a_candidate_that_does_not_exist_is_404(
    client: TestClient, campaign: str
) -> None:
    response = client.put(
        f"/runs/{campaign}/candidates/nope/decision",
        json={"verdict": "shortlisted", "rationale": "x"},
    )
    assert response.status_code == 404


# -- J4: the expensive NVIDIA work ----------------------------------------


def test_an_analysis_is_accepted_and_tracked(
    app: object, client: TestClient, campaign: str
) -> None:
    """Whether DiffDock is reachable or not, the job must leave a record."""
    candidate = _first_candidate(client, campaign)
    response = client.post(
        f"/runs/{campaign}/candidates/{candidate}/analyses",
        json={"kind": "dock", "degree_of_polymerisation": 5, "num_poses": 1},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    queued = client.get(f"/jobs/{job_id}").json()
    assert queued["kind"] == "dock"
    assert queued["candidate_id"] == candidate
    assert queued["evidence"] == "computed"
    assert queued["created_by_name"] == "Ada"

    app.state.queue.wait(job_id, timeout=600)  # type: ignore[attr-defined]
    done = client.get(f"/jobs/{job_id}").json()
    assert done["status"] in {"succeeded", "failed"}
    if done["status"] == "succeeded":
        # The caveat travels with the result, never alongside it.
        assert "not how strongly anything binds" in done["result"]["caveat"]
    else:
        # A refusal keeps the adapter's own reason, and something to do next.
        assert done["error"]
        assert done["error_kind"]


def test_an_unknown_analysis_is_refused(client: TestClient, campaign: str) -> None:
    candidate = _first_candidate(client, campaign)
    response = client.post(
        f"/runs/{campaign}/candidates/{candidate}/analyses",
        json={"kind": "telepathy"},
    )
    assert response.status_code == 400
    assert "unknown analysis" in response.json()["detail"]


def test_analyses_are_listed_against_their_candidate(client: TestClient, campaign: str) -> None:
    candidate = _first_candidate(client, campaign)
    client.post(
        f"/runs/{campaign}/candidates/{candidate}/analyses",
        json={"kind": "affinity", "degree_of_polymerisation": 4},
    )
    detail = client.get(f"/runs/{campaign}/candidates/{candidate}").json()
    assert [j["kind"] for j in detail["jobs"]] == ["affinity"]
    ranked = client.get(f"/runs/{campaign}/ranking").json()["candidates"]
    row = next(c for c in ranked if c["candidate_id"] == candidate)
    assert row["jobs"]


# -- J5: the audit trail ---------------------------------------------------


def test_the_audit_trail_names_who_did_what(
    client: TestClient, program: str, campaign: str
) -> None:
    candidate = _first_candidate(client, campaign)
    client.put(
        f"/runs/{campaign}/candidates/{candidate}/decision",
        json={"verdict": "shortlisted", "rationale": "best in cohort"},
    )

    activity = client.get("/activity", params={"project_id": program}).json()["activity"]
    kinds = [a["kind"] for a in activity]
    assert "program.created" in kinds
    assert "campaign.started" in kinds
    assert "candidate.shortlisted" in kinds
    assert all(a["actor_name"] == "Ada" for a in activity)
    # Newest first, so a feed reads top-down.
    assert activity == sorted(activity, key=lambda a: a["created_at"], reverse=True)


def test_the_trail_can_be_narrowed_to_one_person(
    anon: TestClient, client: TestClient, program: str
) -> None:
    client.post("/auth/logout")
    sign_up(anon, email="reviewer@lab.test", name="Rae", role="reviewer")
    anon.post("/projects", json={"name": "Rae's program", "target_key": TARGET})

    mine = anon.get("/activity", params={"mine": True}).json()["activity"]
    assert mine
    assert all(a["actor_name"] == "Rae" for a in mine)


# -- J6: platform state ----------------------------------------------------


def test_the_platform_state_is_visible_to_a_signed_in_user(client: TestClient) -> None:
    availability = client.get("/availability").json()
    assert "adapters" in availability
    assert "unverified_adapters" in availability["summary"]
    # Configured is never dressed up as verified.
    verification = client.get("/verification").json()
    assert "ran" in verification


def test_config_still_never_serves_a_credential(client: TestClient) -> None:
    body = client.get("/config").text
    assert "nvapi-" not in body

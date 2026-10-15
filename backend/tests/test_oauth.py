"""Google sign-in.

The flow's value is entirely in what it refuses, so most of these are
refusals: a replayed callback, a state nobody issued, an ID token minted for
another client or another attempt, an unverified address, a domain outside the
allow-list, and an open redirect dressed up as a ``next`` parameter.

No test reaches Google. The token endpoint is mocked with respx, and the ID
token is assembled here, because the point is to exercise *our* validation
rather than Google's availability.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from biologix import oauth
from biologix.accounts import AuthError, authenticate, sign_in_with_identity
from biologix.settings import Settings
from biologix.store.database import reset_engine_cache

CLIENT_ID = "123456789-abcdef.apps.googleusercontent.com"
REDIRECT = "http://localhost:3000/api/bx/auth/google/callback"


def _b64(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def id_token(**overrides: Any) -> str:
    """An ID token with the claims we validate. The signature is never read."""
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "107384726159263548271",
        "email": "ada@lab.test",
        "email_verified": True,
        "name": "Ada Lovelace",
        "exp": int(time.time()) + 600,
    }
    claims.update(overrides)
    return f"{_b64({'alg': 'RS256'})}.{_b64(claims)}.not-a-real-signature"


# _env_file=None on every Settings below: without it these read the developer's
# real .env, so the suite would pass or fail depending on whether that machine
# happens to have Google credentials and a domain restriction configured.
@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    reset_engine_cache()
    yield Settings(
        _env_file=None,
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        BIOLOGIX_DATABASE_URL=f"sqlite+pysqlite:///{(tmp_path / 'oauth.db').as_posix()}",
        GOOGLE_CLIENT_ID=CLIENT_ID,
        GOOGLE_CLIENT_SECRET="not-a-real-secret",
        GOOGLE_REDIRECT_URI=REDIRECT,
    )
    reset_engine_cache()


@pytest.fixture
def unconfigured(tmp_path: Path) -> Iterator[Settings]:
    reset_engine_cache()
    yield Settings(
        _env_file=None,
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        BIOLOGIX_DATABASE_URL=f"sqlite+pysqlite:///{(tmp_path / 'plain.db').as_posix()}",
    )
    reset_engine_cache()


def make_client(settings: Settings) -> TestClient:
    from biologix.api.app import create_app

    # Redirects are the subject here, so they are inspected, not followed.
    return TestClient(create_app(settings), follow_redirects=False)


class TestConfiguration:
    def test_unconfigured_deployment_reports_google_as_unavailable(
        self, unconfigured: Settings
    ) -> None:
        with make_client(unconfigured) as client:
            body = client.get("/auth/providers").json()
        assert body["password"] is True
        assert body["google"]["configured"] is False
        assert body["google"]["start_path"] is None
        assert "GOOGLE_CLIENT_ID" in body["google"]["reason"]

    def test_configured_deployment_advertises_the_start_path(self, settings: Settings) -> None:
        with make_client(settings) as client:
            body = client.get("/auth/providers").json()
        assert body["google"]["configured"] is True
        assert body["google"]["start_path"] == "/api/bx/auth/google/start"

    def test_starting_without_credentials_bounces_rather_than_crashing(
        self, unconfigured: Settings
    ) -> None:
        with make_client(unconfigured) as client:
            response = client.get("/auth/google/start")
        assert response.status_code == 303
        location = unquote_plus(response.headers["location"])
        assert "/login?error=" in location
        assert "not configured" in location


class TestAuthorisationRequest:
    def test_start_redirects_to_google_with_pkce_and_state(self, settings: Settings) -> None:
        with make_client(settings) as client:
            response = client.get("/auth/google/start")
        assert response.status_code == 303
        target = response.headers["location"]
        assert target.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        for required in (
            "code_challenge_method=S256",
            "code_challenge=",
            "state=",
            "nonce=",
            "response_type=code",
        ):
            assert required in target, required
        # The verifier itself must never be sent to the browser.
        assert "code_verifier" not in target

    def test_the_code_challenge_is_the_sha256_of_the_stored_verifier(
        self, settings: Settings
    ) -> None:
        import hashlib
        from urllib.parse import parse_qs, urlparse

        flow = oauth.start_flow(settings)
        sent = parse_qs(urlparse(flow.authorisation_url).query)["code_challenge"][0]
        digest = hashlib.sha256(flow.code_verifier.encode()).digest()
        assert sent == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def test_a_single_allowed_domain_is_hinted_to_google(self, tmp_path: Path) -> None:
        settings = Settings(
            _env_file=None,
            BIOLOGIX_DATA_DIR=tmp_path / "var",
            GOOGLE_CLIENT_ID=CLIENT_ID,
            GOOGLE_CLIENT_SECRET="x",
            GOOGLE_ALLOWED_DOMAINS="lab.test",
        )
        assert "hd=lab.test" in oauth.start_flow(settings).authorisation_url


class TestIdTokenValidation:
    def test_a_token_not_from_the_token_endpoint_is_refused(self, settings: Settings) -> None:
        """The skipped signature check is only sound for the direct exchange."""
        with pytest.raises(AuthError, match="signature"):
            oauth.validate_id_token(
                settings, id_token(nonce="n"), expected_nonce="n", from_token_endpoint=False
            )

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"iss": "https://evil.example"}, "issuer is not Google"),
            ({"aud": "someone-elses-client.apps.googleusercontent.com"}, "different client"),
            ({"exp": int(time.time()) - 3600}, "expired"),
            ({"sub": ""}, "no subject"),
        ],
    )
    def test_bad_claims_are_refused(
        self, settings: Settings, overrides: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(AuthError, match=message):
            oauth.validate_id_token(
                settings,
                id_token(nonce="expected", **overrides),
                expected_nonce="expected",
                from_token_endpoint=True,
            )

    def test_a_nonce_from_another_attempt_is_refused(self, settings: Settings) -> None:
        with pytest.raises(AuthError, match="nonce"):
            oauth.validate_id_token(
                settings,
                id_token(nonce="issued-for-another-sign-in"),
                expected_nonce="this-attempt",
                from_token_endpoint=True,
            )

    def test_an_unverified_email_cannot_sign_in(self, settings: Settings) -> None:
        claims = oauth.decode_id_token(id_token(email_verified=False))
        with pytest.raises(AuthError, match="has not verified"):
            oauth.profile_from_claims(settings, claims)

    def test_a_domain_outside_the_allow_list_is_refused(self, tmp_path: Path) -> None:
        settings = Settings(
            _env_file=None,
            BIOLOGIX_DATA_DIR=tmp_path / "var",
            GOOGLE_CLIENT_ID=CLIENT_ID,
            GOOGLE_CLIENT_SECRET="x",
            GOOGLE_ALLOWED_DOMAINS="lab.test, partner.test",
        )
        claims = oauth.decode_id_token(id_token(email="outsider@gmail.com"))
        with pytest.raises(AuthError, match="not in an allowed domain"):
            oauth.profile_from_claims(settings, claims)
        # The allowed one still works.
        assert (
            oauth.profile_from_claims(
                settings, oauth.decode_id_token(id_token(email="ada@partner.test"))
            ).email
            == "ada@partner.test"
        )

    def test_a_missing_display_name_falls_back_to_the_local_part(self, settings: Settings) -> None:
        claims = oauth.decode_id_token(id_token(name=""))
        assert oauth.profile_from_claims(settings, claims).display_name == "ada"


class TestOpenRedirect:
    @pytest.mark.parametrize(
        "candidate",
        ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", None],
    )
    def test_anything_leaving_the_app_is_replaced_with_the_default(
        self, candidate: str | None
    ) -> None:
        assert oauth.safe_next_path(candidate) == "/"

    def test_a_relative_path_survives(self) -> None:
        assert oauth.safe_next_path("/programs/abc") == "/programs/abc"


class TestCallback:
    def _complete(
        self, client: TestClient, settings: Settings, *, token: str | None = None
    ) -> httpx.Response:
        """Walk start -> Google -> callback, with the token endpoint mocked."""
        from urllib.parse import parse_qs, urlparse

        started = client.get("/auth/google/start")
        query = parse_qs(urlparse(started.headers["location"]).query)
        state, nonce = query["state"][0], query["nonce"][0]

        with respx.mock:
            respx.post(oauth.TOKEN_URL).mock(
                return_value=httpx.Response(200, json={"id_token": token or id_token(nonce=nonce)})
            )
            return client.get(f"/auth/google/callback?code=auth-code&state={state}")

    def test_a_first_sign_in_creates_an_account_and_a_session(self, settings: Settings) -> None:
        with make_client(settings) as client:
            response = self._complete(client, settings)
            assert response.status_code == 303
            assert response.headers["location"] == "http://localhost:3000/"
            assert "biologix_session=" in response.headers.get("set-cookie", "")

            me = client.get("/auth/me").json()
        assert me["user"]["email"] == "ada@lab.test"
        assert me["user"]["display_name"] == "Ada Lovelace"

    def test_the_same_state_cannot_be_used_twice(self, settings: Settings) -> None:
        """A replayed callback must not mint a second session."""
        from urllib.parse import parse_qs, urlparse

        with make_client(settings) as client:
            started = client.get("/auth/google/start")
            query = parse_qs(urlparse(started.headers["location"]).query)
            state, nonce = query["state"][0], query["nonce"][0]

            with respx.mock:
                respx.post(oauth.TOKEN_URL).mock(
                    return_value=httpx.Response(200, json={"id_token": id_token(nonce=nonce)})
                )
                first = client.get(f"/auth/google/callback?code=c1&state={state}")
                second = client.get(f"/auth/google/callback?code=c1&state={state}")

        assert first.status_code == 303
        assert "already been used" in unquote_plus(second.headers["location"])

    def test_a_state_nobody_issued_is_refused(self, settings: Settings) -> None:
        with make_client(settings) as client:
            response = client.get("/auth/google/callback?code=c&state=invented-by-an-attacker")
        assert "not recognised" in unquote_plus(response.headers["location"])

    def test_a_cancelled_sign_in_says_so_without_alarm(self, settings: Settings) -> None:
        with make_client(settings) as client:
            response = client.get("/auth/google/callback?error=access_denied")
        assert "cancelled" in unquote_plus(response.headers["location"])

    def test_googles_refusal_reaches_the_sign_in_page(self, settings: Settings) -> None:
        from urllib.parse import parse_qs, urlparse

        with make_client(settings) as client:
            started = client.get("/auth/google/start")
            state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
            with respx.mock:
                respx.post(oauth.TOKEN_URL).mock(
                    return_value=httpx.Response(
                        400,
                        json={
                            "error": "redirect_uri_mismatch",
                            "error_description": "Bad Request",
                        },
                    )
                )
                response = client.get(f"/auth/google/callback?code=c&state={state}")
        assert "redirect_uri_mismatch" in unquote_plus(response.headers["location"])


class TestAccountLinking:
    def test_the_same_subject_signs_into_the_same_account(self, settings: Settings) -> None:
        url = settings.database_url
        first, created_first = sign_in_with_identity(
            provider="google",
            subject="sub-1",
            email="ada@lab.test",
            display_name="Ada",
            url=url,
        )
        again, created_again = sign_in_with_identity(
            provider="google",
            subject="sub-1",
            # Google may report a changed address for the same person.
            email="ada.lovelace@lab.test",
            display_name="Ada Lovelace",
            url=url,
        )
        assert created_first is True
        assert created_again is False
        assert again["id"] == first["id"]

    def test_a_verified_email_links_to_an_existing_password_account(
        self, settings: Settings
    ) -> None:
        """Signing in with Google must not create a duplicate of yourself."""
        from biologix.accounts import register

        url = settings.database_url
        existing = register(
            email="ada@lab.test",
            password="correct horse battery",
            display_name="Ada",
            url=url,
        )
        linked, created = sign_in_with_identity(
            provider="google",
            subject="sub-9",
            email="ada@lab.test",
            display_name="Ada Lovelace",
            url=url,
        )
        assert created is False
        assert linked["id"] == existing["id"]
        # Linking must not disturb the password that was already there.
        assert (
            authenticate(email="ada@lab.test", password="correct horse battery", url=url)["id"]
            == existing["id"]
        )

    def test_a_google_only_account_cannot_be_signed_into_with_a_password(
        self, settings: Settings
    ) -> None:
        """A null password hash must refuse, not crash, and must not leak."""
        url = settings.database_url
        sign_in_with_identity(
            provider="google",
            subject="sub-2",
            email="grace@lab.test",
            display_name="Grace",
            url=url,
        )
        with pytest.raises(AuthError, match="do not match an account"):
            authenticate(email="grace@lab.test", password="anything at all", url=url)

    def test_a_different_subject_on_a_new_address_is_a_different_person(
        self, settings: Settings
    ) -> None:
        url = settings.database_url
        one, _ = sign_in_with_identity(
            provider="google", subject="sub-a", email="a@lab.test", display_name="A", url=url
        )
        two, _ = sign_in_with_identity(
            provider="google", subject="sub-b", email="b@lab.test", display_name="B", url=url
        )
        assert one["id"] != two["id"]

"""Google sign-in, as an authorisation code flow with PKCE.

This mints the *same* session as a password sign-in. Google is a way to prove
who someone is; it is not a second notion of a session, a second user table or
a second audit identity. :func:`biologix.accounts.start_session` issues the
cookie either way, so everything downstream — the audit trail, the role on a
decision, ``/auth/me`` — is unchanged and unaware.

What is deliberately handled rather than hoped for:

**PKCE** (RFC 7636, S256). The code verifier never leaves this process, so an
authorisation code intercepted in transit cannot be redeemed by anyone else.
Google permits PKCE on confidential clients and there is no reason to decline
it.

**A single-use ``state``.** Stored as a row, checked, and consumed. Without it,
an attacker can complete a flow *they* started in a victim's browser and leave
the victim signed in as the attacker — the login CSRF that turns "sign in with
Google" into a way to feed someone else's data into an account you control.

**A ``nonce`` bound to that state**, so an ID token minted for a different
authorisation cannot be replayed into this one.

**Signature verification is skipped, on purpose, and only here.** The ID token
is read from the response body of a request *this server* made directly to
Google's token endpoint over TLS. OpenID Connect Core §3.1.3.7 permits
skipping signature checks in exactly that case: the TLS channel already
authenticates the issuer, and a JWT library would be verifying a signature on
bytes it just received from the signer. The claims are still validated below.
If an ID token ever reaches this code from a *browser* rather than from the
token endpoint, that reasoning collapses and signature verification becomes
mandatory — hence :func:`validate_id_token` refusing to be called without the
``from_token_endpoint`` flag.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from biologix.accounts import AuthError
from biologix.settings import Settings

AUTHORISE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Google accepts both spellings of its own issuer.
VALID_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

#: Only what is needed to identify someone. No Drive, no Calendar, no offline
#: access: this is a sign-in, so there is nothing to do with a refresh token.
SCOPES = ("openid", "email", "profile")

#: An authorisation a person does not finish within this is abandoned.
FLOW_TTL = timedelta(minutes=10)

#: Clock skew allowed when checking ``exp``.
LEEWAY = timedelta(seconds=30)

PROVIDER = "google"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass(frozen=True)
class StartedFlow:
    """Everything the caller must persist, plus where to send the browser."""

    state: str
    code_verifier: str
    nonce: str
    authorisation_url: str
    expires_at: datetime


def start_flow(settings: Settings, *, next_path: str = "/") -> StartedFlow:
    """Build the redirect to Google and the secrets that will verify its reply."""
    require_configured(settings)
    state = _b64url(secrets.token_bytes(32))
    nonce = _b64url(secrets.token_bytes(24))
    # RFC 7636 wants 43..128 characters from the unreserved set; 64 random
    # bytes base64url-encoded lands inside that and is all unreserved.
    code_verifier = _b64url(secrets.token_bytes(64))[:128]
    challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Ask for an account every time rather than silently reusing whichever
        # Google session the browser happens to hold.
        "prompt": "select_account",
    }
    if domains := settings.google_allowed_domain_list:
        # A hint only — Google does not enforce it, so the claim is checked
        # again in profile_from_claims. Sending one domain is meaningful;
        # sending several is not, so only a single domain is hinted.
        if len(domains) == 1:
            params["hd"] = domains[0]

    return StartedFlow(
        state=state,
        code_verifier=code_verifier,
        nonce=nonce,
        authorisation_url=f"{AUTHORISE_URL}?{urlencode(params)}",
        expires_at=datetime.now(UTC) + FLOW_TTL,
    )


def require_configured(settings: Settings) -> None:
    if not settings.google_oauth_configured:
        raise AuthError(
            "Google sign-in is not configured on this deployment: "
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are unset"
        )


def exchange_code(
    settings: Settings, *, code: str, code_verifier: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Redeem the authorisation code. Returns Google's token response."""
    require_configured(settings)
    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    owned = client is None
    http = client or httpx.Client(timeout=15.0)
    try:
        response = http.post(TOKEN_URL, data=payload)
    except httpx.HTTPError as exc:
        raise AuthError(f"could not reach Google to complete sign-in: {exc}") from None
    finally:
        if owned:
            http.close()

    if response.status_code != 200:
        # Google's error body names the cause (redirect_uri_mismatch,
        # invalid_client); surfacing it saves a long afternoon.
        detail = _error_detail(response)
        raise AuthError(f"Google refused the authorisation code: {detail}")
    try:
        body = response.json()
    except ValueError:
        raise AuthError("Google's token response was not JSON") from None
    if "id_token" not in body:
        raise AuthError("Google's token response carried no id_token")
    return body


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = body.get("error") or f"HTTP {response.status_code}"
    description = body.get("error_description")
    return f"{error}: {description}" if description else str(error)


def decode_id_token(id_token: str) -> dict[str, Any]:
    """Read a JWT's claims without verifying its signature.

    Private to the token-endpoint path on purpose — see the module docstring.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise AuthError("Google's id_token is not a JWT")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        raise AuthError("Google's id_token payload could not be read") from None


def validate_id_token(
    settings: Settings,
    id_token: str,
    *,
    expected_nonce: str,
    from_token_endpoint: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the claims of an ID token received from the token endpoint."""
    if not from_token_endpoint:
        raise AuthError(
            "refusing to accept an id_token that did not come from Google's token "
            "endpoint: its signature would have to be verified first"
        )
    claims = decode_id_token(id_token)
    moment = now or datetime.now(UTC)

    if claims.get("iss") not in VALID_ISSUERS:
        raise AuthError(f"id_token issuer is not Google: {claims.get('iss')!r}")
    if claims.get("aud") != settings.google_client_id:
        raise AuthError("id_token was issued for a different client")
    try:
        expires = datetime.fromtimestamp(float(claims["exp"]), tz=UTC)
    except (KeyError, TypeError, ValueError):
        raise AuthError("id_token has no readable expiry") from None
    if expires + LEEWAY < moment:
        raise AuthError("id_token has expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        raise AuthError("id_token nonce does not match this sign-in attempt")
    if not claims.get("sub"):
        raise AuthError("id_token carries no subject")
    return claims


@dataclass(frozen=True)
class GoogleProfile:
    subject: str
    email: str
    display_name: str


def profile_from_claims(settings: Settings, claims: dict[str, Any]) -> GoogleProfile:
    """The identity to sign in, or a refusal explaining why not."""
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise AuthError("Google did not return an email address for this account")
    # An unverified address is an address someone typed, not one they own.
    if claims.get("email_verified") not in (True, "true"):
        raise AuthError(f"Google has not verified {email}, so it cannot be used to sign in")

    if allowed := settings.google_allowed_domain_list:
        domain = email.rpartition("@")[2]
        if domain not in allowed:
            raise AuthError(
                f"{email} is not in an allowed domain for this deployment ({', '.join(allowed)})"
            )

    name = str(claims.get("name") or "").strip()
    return GoogleProfile(
        subject=str(claims["sub"]),
        email=email,
        # Falling back to the local part beats storing an empty display name.
        display_name=name or email.partition("@")[0],
    )


def safe_next_path(candidate: str | None, *, default: str = "/") -> str:
    """Keep a post-sign-in redirect inside this app.

    An open redirect on a sign-in endpoint is a phishing primitive: the link
    genuinely is your domain, right up until it is not.
    """
    if not candidate or not candidate.startswith("/"):
        return default
    # "//evil.example" and "/\evil.example" are both absolute to a browser.
    if candidate.startswith(("//", "/\\")):
        return default
    return candidate

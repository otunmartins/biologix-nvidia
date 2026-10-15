"""Shared endpoint plumbing for NIM adapters.

Three questions recur for every NIM, hosted or self-hosted, and getting any
of them wrong produces a confident-looking failure rather than an honest one:

1. *Is this endpoint local?* A self-hosted container needs no credential; a
   hosted one does. Deciding this from the URL rather than a flag means a
   developer who repoints ``BIOLOGIX_*_BASE_URL`` at localhost does not also
   have to remember to unset a key.
2. *Is the service actually reachable?* ``GET /v1/health/ready`` is the
   documented readiness probe across the BioNeMo NIM containers. The hosted
   endpoints do not expose it, so it is only probed for local ones — asking a
   hosted URL for a container health path and reporting the inevitable 404 as
   "unavailable" would be a false negative.
3. *Did the call fail, or was it never possible?* :class:`AdapterUnavailable`
   and :class:`AdapterCallFailed` keep those apart; the helpers here only ever
   raise the former.
"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx

from biologix.errors import AdapterCallFailed, UpstreamServiceError

# Hostnames that mean "a container I am running". Matched as whole hostnames,
# never as prefixes: ``localhost.attacker.example`` is a public host that
# merely starts with "localhost", and treating it as local would waive the
# API-key requirement and send unauthenticated requests off this machine.
_LOCAL_HOSTS = frozenset({"localhost", "host.docker.internal"})

HEALTH_PATH = "/v1/health/ready"


def is_local(base_url: str) -> bool:
    """Whether a base URL points at a container on this host.

    Loopback and unspecified addresses count, as does ``localhost`` and
    Docker's host alias. Everything else — including a private-range address
    on another machine — is treated as remote and therefore as needing
    credentials.
    """
    host = (urlsplit(base_url.strip()).hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTS:
        return True
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_unspecified


def join(base_url: str, path: str) -> str:
    """Join a configured base and path without doubling or dropping slashes."""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def auth_headers(api_key: str | None) -> dict[str, str]:
    """JSON headers, with a bearer token only when there is one to send."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def probe_health(base_url: str, api_key: str | None, *, timeout: float = 5.0) -> dict[str, Any]:
    """Probe a self-hosted container's readiness endpoint.

    Returns ``{"probed": False}`` for a hosted endpoint rather than inventing a
    result: not probing is a different state from probing and failing, and the
    availability report shows which one happened.
    """
    if not is_local(base_url):
        return {
            "probed": False,
            "reachable": None,
            "detail": "hosted endpoint; container health path not applicable",
        }
    url = join(base_url, HEALTH_PATH)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=auth_headers(api_key))
        return {
            "probed": True,
            "reachable": resp.status_code < 400,
            "detail": f"HTTP {resp.status_code}",
            "url": url,
        }
    except httpx.HTTPError as exc:
        return {
            "probed": True,
            "reachable": False,
            "detail": f"{type(exc).__name__}: {exc}",
            "url": url,
        }


def credential_state(base_url: str, api_key: str | None) -> dict[str, Any]:
    """Whether this endpoint needs a key and whether one is present."""
    local = is_local(base_url)
    return {
        "local": local,
        "requires_api_key": not local,
        "api_key_present": bool(api_key),
        "satisfied": local or bool(api_key),
    }


# Substrings that identify a failure as the service's own, whatever status it
# arrived with. Observed live on 2026-10-13: the hosted Boltz-2 endpoint
# answers HTTP 422 — nominally "unprocessable entity", i.e. the caller's fault
# — for a CUDA illegal-memory-access inside the model, for every payload
# tried including NVIDIA's own documented defaults. Reporting that as a bad
# request sends the caller hunting for a mistake in their payload that is not
# there, so the body decides, not the status code.
UPSTREAM_MARKERS: tuple[str, ...] = (
    "infrastructureerror",
    "acceleratorerror",
    "cuda error",
    "cuda out of memory",
    "internal server error",
    "cudaerrorillegaladdress",
)


def failure_for(adapter: str, status: int, body: str) -> AdapterCallFailed:
    """The exception that honestly describes a >=400 response.

    Returns rather than raises, so the caller keeps the ``raise`` visible at
    the call site.
    """
    excerpt = body.strip()[:400]
    lowered = body.lower()

    if status == 429:
        return UpstreamServiceError(
            adapter,
            f"HTTP 429: rate-limited by the service, request not processed. {excerpt}",
            status=status,
            retryable=True,
        )
    if status >= 500 or any(marker in lowered for marker in UPSTREAM_MARKERS):
        return UpstreamServiceError(
            adapter,
            f"HTTP {status}: the service failed internally, not a problem with the "
            f"request. {excerpt}",
            status=status,
            # A GPU fault may or may not recur; a retry is worth one attempt but
            # is not promised to help, so nothing here retries automatically.
            retryable=status >= 500 or "cuda" in lowered,
        )
    return AdapterCallFailed(adapter, f"HTTP {status}: {excerpt}", status=status)


def missing_credential_reason(adapter: str, env_var: str = "NVIDIA_API_KEY") -> str:
    return (
        f"{env_var} is not set and the configured {adapter} endpoint is not local. "
        f"Hosted BioNeMo NIM calls require an API key from build.nvidia.com; "
        f"alternatively point the base URL at a self-hosted container."
    )


__all__ = [
    "HEALTH_PATH",
    "UPSTREAM_MARKERS",
    "auth_headers",
    "credential_state",
    "failure_for",
    "is_local",
    "join",
    "missing_credential_reason",
    "probe_health",
]

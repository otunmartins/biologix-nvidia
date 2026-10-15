"""Shared endpoint plumbing.

The distinction these tests protect: *not probed* is not the same as *probed
and failed*. A hosted endpoint has no container health path, so reporting it
as unreachable would be a false negative that makes a working deployment look
broken.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from biologix.errors import AdapterCallFailed, UpstreamServiceError
from biologix.nvidia.endpoints import (
    auth_headers,
    credential_state,
    failure_for,
    is_local,
    join,
    probe_health,
)

# Verbatim from the hosted Boltz-2 endpoint on 2026-10-13. The status is 422,
# which by itself would say "your request is unprocessable"; the body says the
# model crashed on the GPU. Whose fault a failure was must be read from the
# body, or every caller goes looking for a payload bug that does not exist.
BOLTZ2_CUDA_422 = (
    '{"error": "Error processing request: InfrastructureError: Infrastructure error '
    "during structure prediction: Structure processor failed: AcceleratorError: CUDA "
    'error: an illegal memory access was encountered"}'
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8000", True),
        ("http://localhost:8000/", True),
        ("http://0.0.0.0:8000", True),
        ("http://host.docker.internal:8000", True),
        ("http://[::1]:8000", True),
        ("https://health.api.nvidia.com/v1/biology", False),
        ("https://integrate.api.nvidia.com", False),
        # A host that merely *starts with* a local name is remote. Treating it
        # as local would waive the API-key requirement and send
        # unauthenticated requests off this machine.
        ("http://localhost.attacker.example.com", False),
        ("http://127.0.0.1.attacker.example.com", False),
        # A private address on another machine still needs credentials.
        ("http://192.168.1.50:8000", False),
        ("http://10.0.0.5:8000", False),
        ("", False),
        ("not a url", False),
    ],
)
def test_is_local(url, expected):
    assert is_local(url) is expected


@pytest.mark.parametrize(
    ("base", "path", "expected"),
    [
        ("http://x:1", "/generate", "http://x:1/generate"),
        ("http://x:1/", "/generate", "http://x:1/generate"),
        ("http://x:1/", "generate", "http://x:1/generate"),
        ("http://x:1", "generate", "http://x:1/generate"),
        ("http://x:1/v1/biology/", "/mit/boltz2/predict", "http://x:1/v1/biology/mit/boltz2/predict"),
    ],
)
def test_join_never_doubles_or_drops_slashes(base, path, expected):
    assert join(base, path) == expected


def test_auth_headers_omit_the_token_when_there_is_none():
    assert "Authorization" not in auth_headers(None)
    assert "Authorization" not in auth_headers("")
    assert auth_headers("k")["Authorization"] == "Bearer k"
    assert auth_headers(None)["Content-Type"] == "application/json"


def test_local_endpoint_needs_no_key():
    state = credential_state("http://127.0.0.1:8000", None)
    assert state == {
        "local": True,
        "requires_api_key": False,
        "api_key_present": False,
        "satisfied": True,
    }


def test_hosted_endpoint_needs_a_key():
    assert credential_state("https://health.api.nvidia.com", None)["satisfied"] is False
    assert credential_state("https://health.api.nvidia.com", "k")["satisfied"] is True


def test_hosted_health_is_not_probed():
    result = probe_health("https://health.api.nvidia.com/v1/biology", "k")
    assert result["probed"] is False
    assert result["reachable"] is None
    assert "not applicable" in result["detail"]


@respx.mock
def test_local_health_success():
    respx.get("http://127.0.0.1:9999/v1/health/ready").mock(return_value=httpx.Response(200))
    result = probe_health("http://127.0.0.1:9999", None)
    assert result == {
        "probed": True,
        "reachable": True,
        "detail": "HTTP 200",
        "url": "http://127.0.0.1:9999/v1/health/ready",
    }


@respx.mock
def test_local_health_http_error_is_unreachable():
    respx.get("http://127.0.0.1:9999/v1/health/ready").mock(return_value=httpx.Response(503))
    result = probe_health("http://127.0.0.1:9999", None)
    assert result["reachable"] is False
    assert result["detail"] == "HTTP 503"


@respx.mock
def test_local_health_transport_error_names_the_exception():
    respx.get("http://127.0.0.1:9999/v1/health/ready").mock(
        side_effect=httpx.ConnectError("refused")
    )
    result = probe_health("http://127.0.0.1:9999", None)
    assert result["probed"] is True
    assert result["reachable"] is False
    assert "ConnectError" in result["detail"]


# -- blaming the right side for a failure ----------------------------------


def test_a_gpu_crash_behind_a_422_is_not_blamed_on_the_request():
    error = failure_for("bionemo-boltz2", 422, BOLTZ2_CUDA_422)
    assert isinstance(error, UpstreamServiceError)
    assert error.status == 422
    assert "not a problem with the request" in error.reason
    # The service's own words are kept, so the diagnosis is checkable.
    assert "illegal memory access" in error.reason


def test_a_genuine_client_error_stays_a_client_error():
    error = failure_for("bionemo-boltz2", 422, '{"detail": "sequence must be uppercase"}')
    assert isinstance(error, AdapterCallFailed)
    assert not isinstance(error, UpstreamServiceError)
    assert "sequence must be uppercase" in error.reason


def test_rate_limiting_says_the_request_was_never_processed():
    error = failure_for("bionemo-molmim", 429, '{"title": "Too Many Requests"}')
    assert isinstance(error, UpstreamServiceError)
    assert error.retryable is True
    assert "not processed" in error.reason


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_always_upstream(status):
    error = failure_for("bionemo-esm2", status, "gateway is unwell")
    assert isinstance(error, UpstreamServiceError)
    assert error.retryable is True


def test_a_404_is_a_client_error_because_the_path_is_ours_to_fix():
    error = failure_for("bionemo-esm2", 404, "404 page not found")
    assert not isinstance(error, UpstreamServiceError)


def test_an_upstream_error_is_still_catchable_as_a_call_failure():
    """Existing handlers catch AdapterCallFailed; the new type must not
    escape them."""
    with pytest.raises(AdapterCallFailed):
        raise failure_for("a", 500, "boom")

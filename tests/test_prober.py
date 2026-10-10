import dataclasses
import json
import socket
import ssl
from datetime import UTC, datetime

import httpx
import pytest

from apipulse.prober import USER_AGENT, Outcome, ProbeResult, probe, to_json

URL = "https://api.example.com/v1/ping"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_200_response_is_responded_with_status_and_timing():
    client = client_for(lambda request: httpx.Response(200, text="ok"))
    result = probe(URL, client=client)
    assert result.outcome == Outcome.responded
    assert result.status == 200
    assert isinstance(result.elapsed_ms, int) and result.elapsed_ms >= 0
    assert result.final_url == URL
    assert result.error is None


@pytest.mark.parametrize("status", [401, 403, 404, 500, 429])
def test_any_http_status_is_responded(status):
    client = client_for(lambda request: httpx.Response(status))
    result = probe(URL, client=client)
    assert result.outcome == Outcome.responded
    assert result.status == status


def test_ratelimit_headers_are_captured_lowercased():
    headers = {
        "Retry-After": "30",
        "X-RateLimit-Limit": "60",
        "X-RateLimit-Remaining": "0",
        "Content-Type": "text/plain",
    }
    client = client_for(lambda request: httpx.Response(429, headers=headers))
    result = probe(URL, client=client)
    assert result.ratelimit_headers == {
        "retry-after": "30",
        "x-ratelimit-limit": "60",
        "x-ratelimit-remaining": "0",
    }


def test_no_ratelimit_headers_gives_empty_dict():
    client = client_for(lambda request: httpx.Response(200))
    assert probe(URL, client=client).ratelimit_headers == {}


def redirect_chain(request: httpx.Request) -> httpx.Response:
    nxt = {"/a": "/b", "/b": "/c"}.get(request.url.path)
    if nxt:
        return httpx.Response(302, headers={"Location": nxt})
    return httpx.Response(200)


def test_redirects_are_followed_up_to_the_limit():
    result = probe("https://api.example.com/a", client=client_for(redirect_chain))
    assert result.outcome == Outcome.responded
    assert result.final_url.endswith("/c")


def test_too_many_redirects_is_other_error_without_raising():
    result = probe("https://api.example.com/a", client=client_for(redirect_chain), max_redirects=1)
    assert result.outcome == Outcome.other_error
    assert result.status is None


def raising(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize("exc_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_timeouts_are_timeout_outcome(exc_type):
    result = probe(URL, client=client_for(raising(exc_type("timed out"))))
    assert result.outcome == Outcome.timeout
    assert result.error


def connect_error(message: str, cause: BaseException | None = None) -> httpx.ConnectError:
    error = httpx.ConnectError(message)
    error.__cause__ = cause
    return error


def test_dns_failure_is_classified_and_tls_ok_is_none():
    exc = connect_error("lookup failed", socket.gaierror(8, "nodename nor servname provided"))
    result = probe(URL, client=client_for(raising(exc)))
    assert result.outcome == Outcome.dns_failure
    assert result.tls_ok is None


def test_tls_failure_sets_tls_ok_false():
    exc = connect_error("handshake failed", ssl.SSLError("CERTIFICATE_VERIFY_FAILED"))
    result = probe(URL, client=client_for(raising(exc)))
    assert result.outcome == Outcome.tls_failure
    assert result.tls_ok is False


def test_connection_refused_is_classified():
    exc = connect_error("All connection attempts failed", ConnectionRefusedError(61, "refused"))
    result = probe(URL, client=client_for(raising(exc)))
    assert result.outcome == Outcome.connection_refused


def test_request_is_a_bare_get_with_our_user_agent():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    probe(URL, client=client_for(handler))
    request = seen[0]
    assert request.method == "GET"
    assert request.content == b""
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers
    assert request.headers["user-agent"] == USER_AGENT


def test_tls_ok_true_for_https_and_none_for_http():
    client = client_for(lambda request: httpx.Response(200))
    assert probe("https://api.example.com/x", client=client).tls_ok is True
    assert probe("http://api.example.com/x", client=client).tls_ok is None


def test_to_json_round_trips_every_field():
    when = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    client = client_for(lambda request: httpx.Response(200))
    result = probe(URL, client=client, now=lambda: when)
    data = json.loads(to_json(result))
    assert set(data) == {f.name for f in dataclasses.fields(ProbeResult)}
    assert data["probed_at"].endswith(("+00:00", "Z"))
    assert type(data["outcome"]) is str
    assert data["outcome"] == "responded"

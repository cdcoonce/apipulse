import dataclasses
import json
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import httpx

from apipulse import __version__

USER_AGENT = f"apipulse/{__version__} (+https://github.com/cdcoonce/apipulse#project-page)"

_DNS_MARKERS = ("nodename nor servname", "Name or service not known", "getaddrinfo")


class Outcome(StrEnum):
    responded = "responded"
    timeout = "timeout"
    dns_failure = "dns_failure"
    tls_failure = "tls_failure"
    connection_refused = "connection_refused"
    other_error = "other_error"


@dataclass(frozen=True)
class ProbeResult:
    url: str
    probed_at: datetime
    outcome: Outcome
    status: int | None
    elapsed_ms: int | None
    tls_ok: bool | None
    final_url: str | None
    ratelimit_headers: dict[str, str]
    error: str | None


def _ratelimit_headers(response: httpx.Response) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, value in response.headers.items():
        key = name.lower()
        if key.startswith(("x-ratelimit-", "ratelimit-")) or key == "retry-after":
            found[key] = value
    return found


def probe(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout_s: float = 10.0,
    max_redirects: int = 3,
    now: Callable[[], datetime] | None = None,
) -> ProbeResult:
    probed_at = (now or (lambda: datetime.now(UTC)))()
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout_s)
    previous_max = http.max_redirects
    http.max_redirects = max_redirects
    try:
        start = time.perf_counter()
        response = http.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        elapsed_ms = round((time.perf_counter() - start) * 1000)
    except httpx.HTTPError as exc:
        return _failure(url, probed_at, _classify(exc), exc)
    except Exception as exc:  # noqa: BLE001 - contract: probe never raises
        return _failure(url, probed_at, Outcome.other_error, exc)
    finally:
        http.max_redirects = previous_max
        if owns_client:
            http.close()
    return ProbeResult(
        url=url,
        probed_at=probed_at,
        outcome=Outcome.responded,
        status=response.status_code,
        elapsed_ms=elapsed_ms,
        tls_ok=True if url.lower().startswith("https://") else None,
        final_url=str(response.url),
        ratelimit_headers=_ratelimit_headers(response),
        error=None,
    )


def _classify(exc: httpx.HTTPError) -> Outcome:
    if isinstance(exc, httpx.TimeoutException):
        return Outcome.timeout
    if isinstance(exc, httpx.ConnectError):
        chain = _cause_chain(exc)
        message = str(exc)
        if any(isinstance(e, socket.gaierror) for e in chain) or any(
            marker in message for marker in _DNS_MARKERS
        ):
            return Outcome.dns_failure
        if any(isinstance(e, ssl.SSLError) for e in chain) or any(
            marker in message for marker in ("SSL", "CERTIFICATE")
        ):
            return Outcome.tls_failure
        if any(isinstance(e, ConnectionRefusedError) for e in chain) or (
            "Connection refused" in message
        ):
            return Outcome.connection_refused
    return Outcome.other_error


def _cause_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or any(current is seen for seen in chain):
            continue
        chain.append(current)
        pending.extend((current.__cause__, current.__context__))
    return chain


def _failure(url: str, probed_at: datetime, outcome: Outcome, exc: Exception) -> ProbeResult:
    return ProbeResult(
        url=url,
        probed_at=probed_at,
        outcome=outcome,
        status=None,
        elapsed_ms=None,
        tls_ok=False if outcome is Outcome.tls_failure else None,
        final_url=None,
        ratelimit_headers={},
        error=str(exc)[:200] or type(exc).__name__,
    )


def to_json(result: ProbeResult) -> str:
    data = dataclasses.asdict(result)
    data["probed_at"] = result.probed_at.isoformat()
    data["outcome"] = str(result.outcome.value)
    return json.dumps(data, sort_keys=True)

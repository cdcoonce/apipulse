import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from apipulse.config import Settings
from apipulse.prober import Outcome, ProbeResult, probe
from apipulse.store import append_probes, connect, load_apis


@dataclass(frozen=True)
class RunSummary:
    probed: int
    responded: int
    working: int
    by_outcome: dict[str, int]
    errors: int
    wall_ms: int
    parquet_path: Path


def run_probes(
    settings: Settings,
    *,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    timeout_s: float = 10.0,
) -> RunSummary:
    started = time.monotonic()
    moment = now if now is not None else datetime.now(UTC)
    conn = connect(settings)
    try:
        apis = load_apis(conn)
    finally:
        conn.close()
    owns_client = client is None
    http = httpx.Client(timeout=timeout_s) if client is None else client
    results: list[tuple[str, ProbeResult]] = []
    errors = 0
    try:
        for api in apis:
            try:
                result = probe(api.base_url, client=http, timeout_s=timeout_s)
            except Exception as exc:
                errors += 1
                result = ProbeResult(
                    url=api.base_url,
                    probed_at=datetime.now(UTC),
                    outcome=Outcome.other_error,
                    status=None,
                    elapsed_ms=None,
                    tls_ok=None,
                    final_url=None,
                    ratelimit_headers={},
                    error=str(exc)[:200],
                )
            results.append((api.id, result))
    finally:
        if owns_client:
            http.close()
    by_outcome: dict[str, int] = {}
    for _, result in results:
        by_outcome[result.outcome.value] = by_outcome.get(result.outcome.value, 0) + 1
    responded = [r for _, r in results if r.outcome == Outcome.responded]
    working = [r for r in responded if r.status is not None and 200 <= r.status < 300]
    path = append_probes(settings, results, now=moment)
    return RunSummary(
        probed=len(results),
        responded=len(responded),
        working=len(working),
        by_outcome=by_outcome,
        errors=errors,
        wall_ms=int((time.monotonic() - started) * 1000),
        parquet_path=path,
    )


def format_summary(summary: RunSummary) -> str:
    outcomes = "".join(
        f"{outcome.value}={summary.by_outcome[outcome.value]}, "
        for outcome in Outcome
        if outcome != Outcome.responded and summary.by_outcome.get(outcome.value, 0)
    )
    return (
        f"probe: {summary.probed} probed, {summary.responded} responded, "
        f"{summary.working} working, {outcomes}{summary.errors} errors, "
        f"{summary.wall_ms} ms -> {summary.parquet_path}"
    )

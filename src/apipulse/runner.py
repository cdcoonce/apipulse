import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from apipulse.config import Settings
from apipulse.prober import Outcome, ProbeResult, probe
from apipulse.rollup import compute_rollup, empty_rollup
from apipulse.store import ProbeRow, append_probes, connect, load_apis, read_probes, upsert_rollups


@dataclass(frozen=True)
class RunSummary:
    probed: int
    responded: int
    working: int
    by_outcome: dict[str, int]
    errors: int
    wall_ms: int
    parquet_path: Path
    rollups: int


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
    probe_clock = (lambda: moment) if now is not None else None
    owns_client = client is None
    http = httpx.Client(timeout=timeout_s) if client is None else client
    results: list[tuple[str, ProbeResult]] = []
    errors = 0
    try:
        for api in apis:
            try:
                result = probe(api.base_url, client=http, timeout_s=timeout_s, now=probe_clock)
            except Exception as exc:
                errors += 1
                result = ProbeResult(
                    url=api.base_url,
                    probed_at=moment,
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
    history = read_probes(settings, since=moment - timedelta(days=31))
    by_api: dict[str, list[ProbeRow]] = {}
    for row in history:
        by_api.setdefault(row.api_id, []).append(row)
    # Probes fire after `moment` in real runs; the window must include them.
    rollup_now = max([moment, *(r.probed_at for _, r in results)])
    rollups = [
        compute_rollup(api.id, by_api[api.id], now=rollup_now)
        if api.id in by_api
        else empty_rollup(api.id)
        for api in apis
    ]
    conn = connect(settings)
    try:
        upsert_rollups(conn, rollups, computed_at=moment)
    finally:
        conn.close()
    return RunSummary(
        probed=len(results),
        responded=len(responded),
        working=len(working),
        by_outcome=by_outcome,
        errors=errors,
        wall_ms=int((time.monotonic() - started) * 1000),
        parquet_path=path,
        rollups=len(rollups),
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
        f"{summary.wall_ms} ms -> {summary.parquet_path}, "
        f"rollups: {summary.rollups}"
    )

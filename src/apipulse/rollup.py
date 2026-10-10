from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apipulse.store import ProbeRow

WINDOW_DAYS = 30
AUTH_KEYLESS = "keyless"
AUTH_KEY_REQUIRED = "key-required"
AUTH_UNKNOWN = "unknown"
_AUTH_SAMPLE = 3


@dataclass(frozen=True)
class Rollup:
    api_id: str
    reachable_30d: int
    working_30d: int
    days_observed_30d: int
    p50_ms: int | None
    p95_ms: int | None
    last_probe_at: datetime | None
    last_outcome: str | None
    last_status: int | None
    probe_target: str | None
    auth: str = AUTH_UNKNOWN
    ratelimit_headers_seen: tuple[str, ...] = ()
    saw_429_30d: bool = False


def _nearest_rank(sorted_values: list[int], percent: int) -> int | None:
    if not sorted_values:
        return None
    rank = max(1, math.ceil(percent / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def classify_auth(statuses: Sequence[int]) -> str:
    """Classify from the newest-first statuses of responded probes."""
    sample = list(statuses[:_AUTH_SAMPLE])
    if len(sample) < _AUTH_SAMPLE:
        return AUTH_UNKNOWN
    if all(200 <= s < 300 for s in sample):
        return AUTH_KEYLESS
    if all(s in (401, 403) for s in sample):
        return AUTH_KEY_REQUIRED
    return AUTH_UNKNOWN


def _header_names(raw: str) -> set[str]:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return set()
    return set(parsed) if isinstance(parsed, dict) else set()


def compute_rollup(api_id: str, rows: Sequence[ProbeRow], *, now: datetime) -> Rollup:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    days: dict[date, list[ProbeRow]] = {}
    latencies: list[int] = []
    in_window: list[ProbeRow] = []
    for r in rows:
        if not cutoff <= r.probed_at <= now:
            continue
        in_window.append(r)
        days.setdefault(r.probed_at.astimezone(UTC).date(), []).append(r)
        if r.outcome == "responded" and r.elapsed_ms is not None:
            latencies.append(r.elapsed_ms)
    latencies.sort()
    reachable = sum(1 for d in days.values() if any(r.outcome == "responded" for r in d))
    working = sum(
        1 for d in days.values() if any(r.status is not None and 200 <= r.status < 300 for r in d)
    )
    responded = sorted(
        (r for r in in_window if r.outcome == "responded" and r.status is not None),
        key=lambda r: r.probed_at,
        reverse=True,
    )
    auth = classify_auth([r.status for r in responded if r.status is not None])
    header_names: set[str] = set()
    for r in in_window:
        header_names |= _header_names(r.ratelimit_headers_json)
    last = max(rows, key=lambda r: r.probed_at) if rows else None
    return Rollup(
        api_id,
        reachable,
        working,
        len(days),
        _nearest_rank(latencies, 50),
        _nearest_rank(latencies, 95),
        last.probed_at if last else None,
        last.outcome if last else None,
        last.status if last else None,
        last.url if last else None,
        auth,
        tuple(sorted(header_names)),
        any(r.status == 429 for r in in_window),
    )


def empty_rollup(api_id: str) -> Rollup:
    return Rollup(api_id, 0, 0, 0, None, None, None, None, None, None)

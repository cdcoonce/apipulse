from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from apipulse.store import ProbeRow

WINDOW_DAYS = 30


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


def _nearest_rank(sorted_values: list[int], percent: int) -> int | None:
    if not sorted_values:
        return None
    rank = max(1, math.ceil(percent / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def compute_rollup(api_id: str, rows: Sequence[ProbeRow], *, now: datetime) -> Rollup:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    days: dict[date, list[ProbeRow]] = {}
    latencies: list[int] = []
    for r in rows:
        if not cutoff <= r.probed_at <= now:
            continue
        days.setdefault(r.probed_at.astimezone(UTC).date(), []).append(r)
        if r.outcome == "responded" and r.elapsed_ms is not None:
            latencies.append(r.elapsed_ms)
    latencies.sort()
    reachable = sum(1 for d in days.values() if any(r.outcome == "responded" for r in d))
    working = sum(
        1 for d in days.values() if any(r.status is not None and 200 <= r.status < 300 for r in d)
    )
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
    )


def empty_rollup(api_id: str) -> Rollup:
    return Rollup(api_id, 0, 0, 0, None, None, None, None, None, None)

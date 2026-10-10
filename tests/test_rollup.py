from dataclasses import replace
from datetime import UTC, datetime, timedelta

from apipulse.rollup import WINDOW_DAYS, classify_auth, compute_rollup, empty_rollup
from apipulse.store import ProbeRow

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
URL = "https://a.example.com"


def _row(day_offset, status=200, outcome="responded", elapsed=100, url=URL, hour=1) -> ProbeRow:
    return ProbeRow(
        api_id="a",
        url=url,
        probed_at=NOW - timedelta(days=day_offset, hours=hour),
        outcome=outcome,
        status=status,
        elapsed_ms=elapsed,
        tls_ok=True,
        final_url=url,
        ratelimit_headers_json="{}",
        error=None,
    )


def test_thirty_days_of_200s_count_every_day():
    rows = [_row(d) for d in range(30)]
    r = compute_rollup("a", rows, now=NOW)
    assert r.reachable_30d == r.working_30d == r.days_observed_30d == 30


def test_responded_401s_are_reachable_but_not_working():
    rows = [_row(d, status=401) for d in range(30)]
    r = compute_rollup("a", rows, now=NOW)
    assert (r.reachable_30d, r.working_30d) == (30, 0)


def test_timeouts_count_as_observed_days_but_not_reachable():
    rows = [_row(d) for d in range(17)]
    rows += [_row(d, status=None, outcome="timeout", elapsed=None) for d in (17, 18, 19)]
    r = compute_rollup("a", rows, now=NOW)
    assert (r.days_observed_30d, r.reachable_30d, r.working_30d) == (20, 17, 17)


def test_two_rows_on_one_day_collapse_to_one_day():
    # Two probes on the same UTC day, hours apart, must collapse to one observed day.
    r = compute_rollup("a", [_row(3, status=500, hour=1), _row(3, status=200, hour=9)], now=NOW)
    assert (r.reachable_30d, r.working_30d, r.days_observed_30d) == (1, 1, 1)


def test_row_outside_window_is_ignored_but_still_the_last_probe():
    old = _row(45)
    r = compute_rollup("a", [old], now=NOW)
    assert (r.reachable_30d, r.working_30d, r.days_observed_30d) == (0, 0, 0)
    assert r.p50_ms is None
    assert r.last_probe_at == old.probed_at
    assert (r.last_status, r.last_outcome, r.probe_target) == (200, "responded", old.url)


def test_percentiles_use_nearest_rank_over_responded_latencies():
    elapsed = [70, 10, 100, 40, 20, 90, 50, 30, 80, 60]
    rows = [_row(0, elapsed=e) for e in elapsed]
    r = compute_rollup("a", rows, now=NOW)
    assert (r.p50_ms, r.p95_ms) == (50, 100)


def test_percentiles_are_none_without_responded_rows():
    rows = [_row(d, status=None, outcome="timeout", elapsed=None) for d in range(10)]
    r = compute_rollup("a", rows, now=NOW)
    assert (r.p50_ms, r.p95_ms) == (None, None)


def test_window_is_inclusive_at_the_thirty_day_edge():
    edge = _row(0)
    on = ProbeRow(**{**edge.__dict__, "probed_at": NOW - timedelta(days=WINDOW_DAYS)})
    off = ProbeRow(**{**edge.__dict__, "probed_at": NOW - timedelta(days=WINDOW_DAYS, seconds=1)})
    assert compute_rollup("a", [on], now=NOW).days_observed_30d == 1
    assert compute_rollup("a", [off], now=NOW).days_observed_30d == 0


def test_empty_rollup_is_zeros_and_nones_and_matches_no_rows():
    r = empty_rollup("x")
    assert (r.api_id, r.reachable_30d, r.working_30d, r.days_observed_30d) == ("x", 0, 0, 0)
    assert (r.p50_ms, r.p95_ms, r.last_probe_at) == (None, None, None)
    assert (r.last_outcome, r.last_status, r.probe_target) == (None, None, None)
    assert compute_rollup("x", [], now=NOW) == r


def test_classify_auth_uses_first_three_statuses_only():
    assert classify_auth([200, 200, 204]) == "keyless"
    assert classify_auth([401, 403, 401]) == "key-required"
    assert classify_auth([200, 401, 200]) == "unknown"
    assert classify_auth([200, 200]) == "unknown"
    assert classify_auth([200, 200, 200, 401]) == "keyless"


def test_auth_classification_takes_the_newest_three_responded_rows():
    # day_offset 0..4 => newest first; oldest two are 200, newest three are 401/403/401.
    rows = [
        _row(4, status=200),
        _row(0, status=401),
        _row(3, status=200),
        _row(2, status=401),
        _row(1, status=403),
    ]
    assert compute_rollup("a", rows, now=NOW).auth == "key-required"


def test_auth_classification_respects_the_window():
    # A 200 newer than `now` is outside the window; unfiltered it would break the 401 streak.
    future = _row(0, status=200, hour=-5)
    in_window = [_row(1, status=401), _row(2, status=401), _row(3, status=401)]
    assert compute_rollup("a", [future, *in_window], now=NOW).auth == "key-required"
    # A stale 401 beyond the window must not make up a third sample.
    stale = _row(WINDOW_DAYS + 5, status=401)
    assert compute_rollup("a", [stale, *in_window[:2]], now=NOW).auth == "unknown"


def _with_headers(row: ProbeRow, headers: str) -> ProbeRow:
    return replace(row, ratelimit_headers_json=headers)


def test_ratelimit_headers_seen_is_sorted_deduplicated_and_windowed():
    rows = [
        _with_headers(_row(1), '{"x-ratelimit-limit":"60"}'),
        _with_headers(_row(2, status=500), '{"retry-after":"30","x-ratelimit-limit":"60"}'),
        _with_headers(_row(3), "{}"),
        _with_headers(_row(4), "not json"),
        _with_headers(_row(WINDOW_DAYS + 5), '{"ratelimit-remaining":"1"}'),
    ]
    assert compute_rollup("a", rows, now=NOW).ratelimit_headers_seen == (
        "retry-after",
        "x-ratelimit-limit",
    )


def test_saw_429_only_counts_in_window_rows():
    assert compute_rollup("a", [_row(1, status=429)], now=NOW).saw_429_30d is True
    assert compute_rollup("a", [_row(WINDOW_DAYS + 5, status=429)], now=NOW).saw_429_30d is False
    assert compute_rollup("a", [_row(1, status=200)], now=NOW).saw_429_30d is False


def test_empty_rollup_has_signal_defaults():
    r = empty_rollup("x")
    assert (r.auth, r.ratelimit_headers_seen, r.saw_429_30d) == ("unknown", (), False)

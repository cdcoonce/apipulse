from datetime import UTC, datetime, timedelta, timezone

import pytest

from apipulse.rollup import WINDOW_DAYS, compute_rollup, day_strip, probe_depth
from apipulse.store import ProbeRow

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
URL = "https://a.example.com"


def _probe(at: datetime, status: int | None = 200, outcome: str = "responded") -> ProbeRow:
    return ProbeRow(
        api_id="a",
        url=URL,
        probed_at=at,
        outcome=outcome,
        status=status,
        elapsed_ms=100 if outcome == "responded" else None,
        tls_ok=True,
        final_url=URL,
        ratelimit_headers_json="{}",
        error=None,
    )


def _on(day_offset: int, hour: int = 3, **kw) -> ProbeRow:
    day = (NOW - timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0)
    return _probe(day, **kw)


CUTOFF = NOW - timedelta(days=WINDOW_DAYS)


def test_probe_depth_none_when_never_probed():
    assert probe_depth(None, URL) is None


def test_probe_depth_site_on_exact_equality():
    assert probe_depth(URL, URL) == "site"


def test_probe_depth_endpoint_otherwise():
    assert probe_depth(URL + "/v1/ping", URL) == "endpoint"


@pytest.mark.parametrize("target", [URL + "/", URL.upper(), "http://a.example.com", URL + " "])
def test_probe_depth_does_no_normalisation(target):
    assert probe_depth(target, URL) == "endpoint"


def test_probe_depth_empty_target_is_not_never_probed():
    assert probe_depth("", URL) == "endpoint"


def test_empty_rows_give_31_dots():
    assert day_strip([], now=NOW) == "." * 31


def test_strip_is_31_chars_dated_from_cutoff_day_to_now_day():
    strip = day_strip([_on(30), _on(0, status=500), _on(15, status=404)], now=NOW)
    assert len(strip) == 31
    assert strip[0] == "W"  # 2026-09-10
    assert strip[30] == "R"  # 2026-10-10
    assert strip[15] == "R"  # 2026-09-25
    assert strip.count(".") == 28


def test_priority_working_beats_responded_beats_down():
    rows = [
        _on(5, hour=1, status=500),
        _on(5, hour=2, status=200),
        _on(5, hour=3, status=404),
        _on(6, hour=1, status=None, outcome="timeout"),
        _on(6, hour=2, status=503),
        _on(7, hour=1, status=None, outcome="timeout"),
        _on(7, hour=2, status=None, outcome="dns_failure"),
    ]
    strip = day_strip(rows, now=NOW)
    assert strip[30 - 5] == "W"
    assert strip[30 - 6] == "R"
    assert strip[30 - 7] == "D"


def test_status_300_and_199_are_not_working_and_299_is():
    rows = [_on(1, status=300), _on(2, status=299), _on(3, status=199)]
    strip = day_strip(rows, now=NOW)
    assert (strip[29], strip[28], strip[27]) == ("R", "W", "R")


def test_probe_exactly_at_the_cutoff_counts_and_one_second_before_does_not():
    at_cutoff = day_strip([_probe(CUTOFF)], now=NOW)
    assert at_cutoff[0] == "W" and at_cutoff.count(".") == 30
    before = day_strip([_probe(CUTOFF - timedelta(seconds=1))], now=NOW)
    assert before == "." * 31


def test_probe_in_the_future_of_now_counts_on_no_date():
    assert day_strip([_probe(NOW + timedelta(seconds=1))], now=NOW) == "." * 31
    assert day_strip([_probe(NOW)], now=NOW)[30] == "W"


def test_dates_are_utc_even_for_offset_aware_probe_times():
    plus_nine = timezone(timedelta(hours=9))
    # 2026-10-10 02:00+09:00 is 2026-10-09 17:00 UTC, so it lands on index 29, not 30.
    strip = day_strip([_probe(datetime(2026, 10, 10, 2, 0, tzinfo=plus_nine))], now=NOW)
    assert strip == "." * 29 + "W."


def test_the_window_edge_day_is_shared_by_a_dropped_and_a_kept_probe():
    # Same UTC date as the cutoff; only the in-window probe may colour it.
    early = _probe(CUTOFF - timedelta(hours=1), status=200)
    late = _probe(CUTOFF + timedelta(hours=1), status=500)
    assert day_strip([early, late], now=NOW)[0] == "R"


ROW_SETS = {
    "empty": [],
    "all_working": [_on(d) for d in range(31)],
    "all_401": [_on(d, status=401) for d in range(0, 31, 2)],
    "mixed": [
        _on(0, status=200),
        _on(0, hour=5, status=500),
        _on(1, status=None, outcome="timeout"),
        _on(2, status=403),
        _on(2, hour=9, status=None, outcome="dns_failure"),
        _on(9, status=204),
        _on(12, status=None, outcome="tls_failure"),
        _on(29, status=302),
        _on(40, status=200),
    ],
    "boundary": [
        _probe(CUTOFF),
        _probe(CUTOFF - timedelta(seconds=1)),
        _probe(CUTOFF + timedelta(seconds=1), status=500),
        _probe(NOW),
        _probe(NOW + timedelta(seconds=1)),
    ],
    "same_day_collapse": [_on(4, hour=h, status=s) for h, s in ((1, 500), (2, 200), (3, 404))],
    "only_outside": [_on(31), _on(45), _probe(NOW + timedelta(days=2))],
}


@pytest.mark.parametrize("name", sorted(ROW_SETS))
def test_strip_agrees_with_compute_rollup(name):
    rows = ROW_SETS[name]
    strip = day_strip(rows, now=NOW)
    rollup = compute_rollup("a", rows, now=NOW)
    assert len(strip) == 31
    assert set(strip) <= set("WRD.")
    assert strip.count("W") == rollup.working_30d
    assert strip.count("W") + strip.count("R") == rollup.reachable_30d
    assert 31 - strip.count(".") == rollup.days_observed_30d


def test_strip_invariants_hold_for_a_shifted_now_too():
    later = NOW + timedelta(hours=7, minutes=13)
    for rows in ROW_SETS.values():
        strip = day_strip(rows, now=later)
        rollup = compute_rollup("a", rows, now=later)
        assert strip.count("W") == rollup.working_30d
        assert strip.count("W") + strip.count("R") == rollup.reachable_30d
        assert 31 - strip.count(".") == rollup.days_observed_30d

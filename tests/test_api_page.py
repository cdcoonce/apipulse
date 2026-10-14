import re
from datetime import UTC, datetime, timedelta

import pytest

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import Outcome, ProbeResult
from apipulse.render import render_site
from apipulse.rollup import compute_rollup
from apipulse.store import (
    append_probes,
    connect,
    read_probes,
    upsert_apis,
    upsert_rollups,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
BASE = "https://a.example.com"
ENDPOINT = BASE + "/v1/ping"


def _rec(api_id: str = "a", **overrides) -> ApiRecord:
    fields = {
        "id": api_id,
        "name": f"Name of {api_id}",
        "description": "",
        "tags": (),
        "base_url": BASE,
        "spec_url": None,
        "declared_auth": None,
        "signup_url": None,
        "source": "seed",
    }
    fields.update(overrides)
    return ApiRecord(**fields)


def _result(url: str, at: datetime, outcome: Outcome, status: int | None, ms: int | None):
    return ProbeResult(url, at, outcome, status, ms, None, None, {}, None)


def W(days_ago: int, ms: int | None = 100, url: str = ENDPOINT):  # noqa: N802
    return (days_ago, Outcome.responded, 200, ms, url)


def R(days_ago: int, ms: int | None = 100, url: str = ENDPOINT):  # noqa: N802
    return (days_ago, Outcome.responded, 500, ms, url)


def D(days_ago: int, url: str = ENDPOINT):  # noqa: N802
    return (days_ago, Outcome.timeout, None, None, url)


def _render(tmp_path, record: ApiRecord, spec=(), *, active: bool = True) -> str:
    """Seed one API with probes (days_ago, outcome, status, ms, url), render, return its page."""
    settings = Settings(data_dir=tmp_path)
    conn = connect(settings)
    try:
        upsert_apis(conn, [record], seen_at=NOW)
        if not active:
            conn.execute("UPDATE apis SET active = 0 WHERE id = ?", (record.id,))
            conn.commit()
    finally:
        conn.close()
    if spec:
        results = [
            (
                record.id,
                _result(url, (NOW - timedelta(days=ago)).replace(hour=3), outcome, status, ms),
            )
            for ago, outcome, status, ms, url in spec
        ]
        append_probes(settings, results, now=NOW)
        rows = read_probes(settings, since=NOW - timedelta(days=31))
        conn = connect(settings)
        try:
            upsert_rollups(conn, [compute_rollup(record.id, rows, now=NOW)], computed_at=NOW)
        finally:
            conn.close()
    render_site(settings)
    return (tmp_path / "site" / "apis" / f"{record.id}.html").read_text(encoding="utf-8")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def _fact(html: str, name: str) -> str:
    match = re.search(rf'<article class="fact" id="fact-{name}">(.*?)</article>', html, re.S)
    assert match, f"fact card {name} not found"
    return _text(match[1])


def _pill(html: str) -> str:
    match = re.search(r'<span class="pill [^"]*" id="status-pill">([^<]*)</span>', html)
    assert match, "status pill not found"
    return match[1]


def _titles(html: str) -> list[str]:
    match = re.search(r'<span class="strip big"[^>]*>(.*?)</span>', html, re.S)
    assert match, "large strip not found"
    return re.findall(r'<i[^>]*title="([^"]*)"', match[1])


# --- header -------------------------------------------------------------------------------


def test_header_has_the_name_h1_and_the_monospace_id(tmp_path):
    html = _render(tmp_path, _rec("a", name="Alpha API"), [W(0)])
    assert "<h1>Alpha API</h1>" in html
    assert '<p class="api-id">a</p>' in html


def test_header_description_is_plain_text_limited_to_400(tmp_path):
    long = "word " * 200
    html = _render(
        tmp_path,
        _rec("a", description=f"<b>Bold</b> [a link](http://x.example.com) <i>text</i> {long}"),
    )
    desc = re.search(r'<p class="api-desc">(.*?)</p>', html, re.S)[1]
    assert desc.startswith("Bold a link text word")
    assert "<" not in desc and "http://x.example.com" not in desc
    assert len(desc) <= 400 and desc.endswith("…")


def test_header_chips_are_labelled_deduplicated_and_sorted(tmp_path):
    tags = ("open_data", "Open Data", "Financial", "finance", "weather")
    html = _render(tmp_path, _rec("a", tags=tags))
    chips = re.findall(r'<span class="chip">([^<]*)</span>', html)
    assert chips == ["finance", "open data", "weather"]


def test_header_has_no_chips_without_categories(tmp_path):
    assert 'class="chip"' not in _render(tmp_path, _rec("a"))


@pytest.mark.parametrize(
    ("spec", "text"),
    [
        ([W(1), W(0)], "Worked on last probe"),
        ([W(1), R(0)], "Answered, not OK"),
        ([W(1), D(0)], "No answer"),
        ([D(2), W(1)], "Worked on last probe"),
        ([], "Not probed yet"),
    ],
)
def test_status_pill_follows_the_last_probed_day_of_the_strip(tmp_path, spec, text):
    assert _pill(_render(tmp_path, _rec("a"), spec)) == text


def test_status_pill_ignores_trailing_unprobed_days(tmp_path):
    # The strip ends in "D.." style gaps whenever recent days were not probed; the last
    # observed day decides.
    html = _render(tmp_path, _rec("a"), [W(5), D(3), W(0)])
    assert _pill(html) == "Worked on last probe"
    html = _render(tmp_path / "x", _rec("a"), [W(5), D(3)])
    assert _pill(html) == "No answer"


def test_inactive_api_shows_inactive_instead_of_any_status(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0)], active=False)
    assert _pill(html) == "Inactive"
    for other in ("Worked on last probe", "Answered, not OK", "No answer", "Not probed yet"):
        assert f">{other}<" not in html


# --- key facts ----------------------------------------------------------------------------


def test_reliability_says_worked_on_w_of_n_probed_days(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0), R(1), D(2), W(3)])
    assert "Worked on 2 of 4 probed days" in _fact(html, "reliability")


def test_reliability_for_an_unprobed_api_does_not_claim_zero_of_zero(tmp_path):
    fact = _fact(_render(tmp_path, _rec("a")), "reliability")
    assert "Not probed yet" in fact and "0 of 0" not in fact


def test_latency_shows_p50_and_p95(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0, 100), W(1, 200), W(2, 300), W(3, 400)])
    assert "p50 200 ms · p95 400 ms" in _fact(html, "latency")


def test_latency_uses_a_dash_for_missing_values(tmp_path):
    fact = _fact(_render(tmp_path, _rec("a"), [D(0)]), "latency")
    assert "p50 — · p95 —" in fact and "None" not in fact


def test_last_probe_shows_utc_time_outcome_and_status(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(1), R(0)])
    fact = _fact(html, "last-probe")
    assert (
        "2026-10-10 03:00 UTC" in fact
        and "T03:00" not in fact
        and "responded" in fact
        and "500" in fact
    )


def test_last_probe_is_a_dash_when_never_probed(tmp_path):
    assert "—" in _fact(_render(tmp_path, _rec("a")), "last-probe")


def test_auth_card_for_an_endpoint_probe_that_answered_without_a_key(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0), W(1), W(2)])
    fact = _fact(html, "auth")
    assert "No key needed" in fact
    assert "Measured at an endpoint taken from the API's OpenAPI spec." in fact
    assert "Not measured" not in fact


def test_auth_card_for_an_endpoint_probe_that_needs_a_key(tmp_path):
    spec = [(i, Outcome.responded, 401, 50, ENDPOINT) for i in range(3)]
    fact = _fact(_render(tmp_path, _rec("a"), spec), "auth")
    assert "Key required" in fact and "No key needed" not in fact


def test_auth_card_for_an_endpoint_probe_with_unclear_auth(tmp_path):
    fact = _fact(_render(tmp_path, _rec("a"), [W(0), R(1)]), "auth")
    assert "Unclear" in fact and "Measured at an endpoint" in fact


def test_auth_card_for_a_site_probe_says_not_measured_and_names_the_declaration(tmp_path):
    spec = [W(i, url=BASE) for i in range(3)]
    html = _render(tmp_path, _rec("a", declared_auth="OAuth"), spec)
    fact = _fact(html, "auth")
    assert "Not measured" in fact
    assert (
        "Only the API's website was probed, so auth was not measured. "
        "The source list declares: OAuth." in fact
    )
    assert "No key needed" not in fact and "Key required" not in fact


def test_site_probe_declaration_is_the_label_never_the_raw_value(tmp_path):
    raw = "<img src=x onerror=alert(1)>"
    html = _render(tmp_path, _rec("a", declared_auth=raw), [W(0, url=BASE)])
    assert "The source list declares: unclear." in _fact(html, "auth")
    assert "onerror" not in html


def test_auth_card_when_never_probed(tmp_path):
    fact = _fact(_render(tmp_path, _rec("a")), "auth")
    assert "Not probed yet" in fact and "Not measured" not in fact


def test_auth_card_signup_link_only_for_http_urls(tmp_path):
    good = _render(tmp_path, _rec("a", signup_url="https://a.example.com/signup"), [W(0)])
    assert '<a href="https://a.example.com/signup"' in good
    bad = _render(tmp_path / "b", _rec("a", signup_url="javascript:alert(1)"), [W(0)])
    assert 'href="javascript' not in bad and "javascript:alert(1)" in bad


# --- strip --------------------------------------------------------------------------------


def test_strip_titles_run_oldest_to_newest_and_end_at_the_strip_now_date(tmp_path):
    titles = _titles(_render(tmp_path, _rec("a"), [W(0)]))
    assert len(titles) == 31
    assert titles[0] == "2026-09-10: not probed"
    assert titles[-1] == "2026-10-10: worked"


def test_strip_titles_name_each_of_the_four_states(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0), R(1), D(2)])
    titles = _titles(html)
    assert titles[-3:] == [
        "2026-10-08: no answer",
        "2026-10-09: answered, not OK",
        "2026-10-10: worked",
    ]
    assert titles[0].endswith(": not probed")


def test_strip_cells_carry_the_state_class_used_by_the_index_strip(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0), R(1), D(2)])
    strip = re.search(r'<span class="strip big"[^>]*>(.*?)</span>', html, re.S)[1]
    assert re.findall(r'<i class="([WRD])"', strip) == ["D", "R", "W"]


def test_strip_is_labelled_with_its_start_and_end_dates(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0)])
    labels = re.search(r'<div class="strip-dates">(.*?)</div>', html, re.S)[1]
    assert _text(labels) == "2026-09-10 2026-10-10"


def test_seen_but_never_probed_api_renders_without_none_placeholders(tmp_path):
    settings = Settings(data_dir=tmp_path)
    conn = connect(settings)
    try:
        upsert_apis(conn, [_rec("a")], seen_at=NOW)
    finally:
        conn.close()
    render_site(settings)
    html = (tmp_path / "site" / "apis" / "a.html").read_text(encoding="utf-8")
    assert "None" not in html and _pill(html) == "Not probed yet"


# --- sparkline on the page ----------------------------------------------------------------


def test_page_sparkline_breaks_at_days_without_a_latency(tmp_path):
    spec = [W(5, 500), W(4, 400), W(3, 200), W(1, 100), W(0, 300)]
    html = _render(tmp_path, _rec("a"), spec)
    svg = re.search(r"<svg.*?</svg>", html, re.S)[0]
    assert svg.count("<polyline") == 2
    assert 'aria-label="Median latency per day, 100 to 500 ms"' in svg
    assert "Not enough latency data yet" not in html


def test_page_sparkline_uses_one_value_per_strip_date_oldest_first(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(29, 10), W(28, 20), W(1, 30), W(0, 40)])
    points = re.findall(r'<polyline[^>]*points="([^"]*)"', html)
    assert len(points) == 2
    first_x = float(points[0].split()[0].split(",")[0])
    last_x = float(points[-1].split()[-1].split(",")[0])
    assert (first_x, last_x) == (15.5, 314.8)


def test_page_says_not_enough_latency_data_with_fewer_than_two_values(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0, 100)])
    assert "Not enough latency data yet" in html and "<polyline" not in html


def test_page_without_probes_says_not_enough_latency_data(tmp_path):
    assert "Not enough latency data yet" in _render(tmp_path, _rec("a"))


# --- details, badge, log ------------------------------------------------------------------


def test_details_state_the_probe_depth_in_words(tmp_path):
    endpoint = _text(_render(tmp_path, _rec("a"), [W(0)]))
    assert "Probe depth Endpoint (taken from the OpenAPI spec)" in endpoint
    site = _text(_render(tmp_path / "s", _rec("a"), [W(0, url=BASE)]))
    assert "Probe depth Site only (the API's website)" in site
    cold = _text(_render(tmp_path / "c", _rec("a")))
    assert "Probe depth Not probed yet" in cold


def test_details_keep_link_or_plain_text_urls(tmp_path):
    record = _rec("a", spec_url="https://a.example.com/openapi.json")
    html = _render(tmp_path, record, [W(0)])
    assert f'<a href="{BASE}"' in html
    assert '<a href="https://a.example.com/openapi.json"' in html
    assert f'<a href="{ENDPOINT}"' in html
    bad = _render(tmp_path / "b", _rec("a", base_url="javascript:alert(1)"), [W(0)])
    assert 'href="javascript' not in bad


def test_details_show_rate_limit_facts(tmp_path):
    html = _text(_render(tmp_path, _rec("a"), [(0, Outcome.responded, 429, 5, ENDPOINT)]))
    assert "saw 429 (30 days) yes" in html.lower()


def test_badge_image_points_one_level_up(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0)])
    assert '<img src="../badge/a.svg" alt="apipulse badge for a">' in html


def test_daily_probe_log_is_a_details_element_with_the_history_table_newest_first(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(2, 10), R(1, 20), W(0, 30)])
    details = re.search(
        r"<details>\s*<summary>Daily probe log</summary>(.*?)</details>", html, re.S
    )
    assert details, "Daily probe log details not found"
    table = re.search(r'<table id="history">(.*?)</table>', details[1], re.S)[1]
    assert re.findall(r"<th>([^<]*)</th>", table) == ["date", "outcome", "status", "elapsed ms"]
    rows = [_text(r) for r in re.findall(r"<tr>(.*?)</tr>", table, re.S) if "<td" in r]
    assert rows == [
        "2026-10-10 responded 200 30",
        "2026-10-09 responded 500 20",
        "2026-10-08 responded 200 10",
    ]


def test_daily_probe_log_without_probes_says_so_inside_the_details(tmp_path):
    html = _render(tmp_path, _rec("a"))
    details = re.search(
        r"<details>\s*<summary>Daily probe log</summary>(.*?)</details>", html, re.S
    )
    assert "no probes recorded" in details[1]


# --- escaping and determinism -------------------------------------------------------------


def test_hostile_stored_fields_are_escaped_everywhere(tmp_path):
    evil = "</script><script>alert(1)</script>"
    record = _rec(
        "a",
        name=evil,
        description=evil + "<img src=x onerror=alert(2)>",
        tags=(evil,),
        spec_url=evil,
        signup_url=evil,
        declared_auth=evil,
    )
    html = _render(tmp_path, record, [W(0)])
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;/script&gt;" in html
    assert "<script" not in html


def test_rerender_is_byte_identical(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _render(tmp_path, _rec("a", tags=("x",)), [W(3, 50), R(2, 60), D(1), W(0, 70)])
    page = tmp_path / "site" / "apis" / "a.html"
    first = page.read_bytes()
    render_site(settings)
    assert page.read_bytes() == first


def test_last_probe_time_has_no_seconds_or_microseconds(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0)])
    assert re.search(r'<p class="big mono">2026-10-10 03:00 UTC</p>', html)


def test_sparkline_sits_directly_under_the_strip_with_a_max_label(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(2, 90), W(1, 105), W(0, 40)])
    assert "max 105 ms" in _text(re.search(r'<div class="spark-cap">.*?</div>', html, re.S)[0])
    strip_end = html.index('<div class="strip-dates">')
    assert strip_end < html.index('<div class="spark-cap">') < html.index("<svg")


def test_no_max_label_when_there_is_no_sparkline(tmp_path):
    html = _render(tmp_path, _rec("a"), [W(0, 100)])
    assert "max " not in _text(re.sub(r"<style.*?</style>", "", html, flags=re.S))

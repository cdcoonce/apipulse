import json
import re
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import USER_AGENT, Outcome, ProbeResult
from apipulse.render import render_site
from apipulse.rollup import Rollup
from apipulse.store import (
    append_probes,
    connect,
    record_optouts,
    search_apis,
    upsert_apis,
    upsert_rollups,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _rec(
    id: str,
    name: str | None = None,
    description: str = "",
    tags: tuple[str, ...] = (),
    **overrides,
) -> ApiRecord:
    fields = {
        "id": id,
        "name": name or id,
        "description": description,
        "tags": tags,
        "base_url": f"https://{id.strip('./').replace('/', '-')}.example.com",
        "spec_url": None,
        "declared_auth": None,
        "signup_url": None,
        "source": "seed",
    }
    fields.update(overrides)
    return ApiRecord(**fields)


def _roll(api_id: str, reachable: int, working: int, p50: int | None, **kw) -> Rollup:
    return Rollup(api_id, reachable, working, 30, p50, p50, NOW, "responded", 200, None, **kw)


def _seed(
    settings: Settings,
    records,
    rollups=(),
    *,
    computed_at: datetime = NOW,
    seen_at: datetime = NOW,
) -> None:
    conn = connect(settings)
    try:
        upsert_apis(conn, list(records), seen_at=seen_at)
        if rollups:
            upsert_rollups(conn, list(rollups), computed_at=computed_at)
    finally:
        conn.close()


def _site(tmp_path, records, rollups=()):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, records, rollups)
    render_site(settings)
    return tmp_path / "site"


def _embedded(html: str):
    match = re.search(r'<script type="application/json" id="apis-data">(.*?)</script>', html, re.S)
    assert match, "apis-data script not found"
    return json.loads(match.group(1))


def test_index_html_lists_every_api_id(tmp_path):
    site = _site(tmp_path, [_rec("alpha"), _rec("beta")])
    html = (site / "index.html").read_text(encoding="utf-8")
    assert "alpha" in html and "beta" in html


SUMMARY_KEYS = {
    "id",
    "name",
    "reachable_30d",
    "working_30d",
    "p50_ms",
    "last_probe_at",
    "active",
    "auth",
    "description",
    "tags",
}


def test_apis_json_has_summary_fields_plus_description_and_tags(tmp_path):
    site = _site(
        tmp_path,
        [_rec("alpha", "Alpha", "first api", ("x", "y"))],
        [_roll("alpha", 7, 6, 120, auth="keyless")],
    )
    [entry] = json.loads((site / "apis.json").read_text(encoding="utf-8"))
    assert set(entry) == SUMMARY_KEYS
    assert entry == {
        "id": "alpha",
        "name": "Alpha",
        "reachable_30d": 7,
        "working_30d": 6,
        "p50_ms": 120,
        "last_probe_at": "2026-10-10T12:00:00Z",
        "active": True,
        "auth": "keyless",
        "description": "first api",
        "tags": ["x", "y"],
    }


def test_index_embeds_the_same_json_as_apis_json(tmp_path):
    site = _site(tmp_path, [_rec("alpha", "Alpha", "d", ("t",)), _rec("beta")])
    embedded = _embedded((site / "index.html").read_text(encoding="utf-8"))
    assert embedded == json.loads((site / "apis.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in embedded} == {"alpha", "beta"}


def _deactivate(tmp_path, api_id: str) -> None:
    conn = connect(Settings(data_dir=tmp_path))
    try:
        conn.execute("UPDATE apis SET active = 0 WHERE id = ?", (api_id,))
        conn.commit()
    finally:
        conn.close()


def test_inactive_api_stays_in_index_with_muted_row_and_inactive_flag(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("alive"), _rec("dead")])
    _deactivate(tmp_path, "dead")
    render_site(settings)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    by_id = {e["id"]: e for e in _embedded(html)}
    assert by_id["dead"]["active"] is False and by_id["alive"]["active"] is True
    assert re.search(r'<tr class="inactive"[^>]*>\s*<td><a [^>]*>dead', html)
    assert not re.search(r'<tr class="inactive"[^>]*>\s*<td><a [^>]*>alive', html)


def _cells(html: str, api_id: str) -> list[str]:
    row = re.search(rf"<tr[^>]*>\s*<td>\s*<a [^>]*>{re.escape(api_id)}</a>.*?</tr>", html, re.S)
    assert row, f"row for {api_id} not found"
    return [
        re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row[0], re.S)
    ]


def test_index_row_shows_id_name_stats_auth_and_last_probe_date(tmp_path):
    site = _site(
        tmp_path,
        [_rec("alpha", "Alpha API"), _rec("bare")],
        [_roll("alpha", 28, 25, 120, auth="keyless")],
    )
    html = (site / "index.html").read_text(encoding="utf-8")
    assert _cells(html, "alpha") == [
        "alpha",
        "Alpha API",
        "28",
        "25",
        "120",
        "keyless",
        "2026-10-10",
    ]
    assert 'href="apis/alpha.html"' in html
    assert _cells(html, "bare") == ["bare", "bare", "0", "0", "—", "unknown", "—"]


def test_index_and_apis_json_use_the_empty_query_ordering(tmp_path):
    site = _site(
        tmp_path,
        [_rec(i) for i in ("aa", "ab", "vv", "ww", "xx", "yy", "zz")],
        [
            _roll("zz", 30, 1, 900),
            _roll("yy", 20, 15, 900),
            _roll("xx", 20, 10, 100),
            _roll("ww", 20, 10, 200),
            _roll("vv", 20, 10, None),
        ],
    )
    expected = ["zz", "yy", "xx", "ww", "vv", "aa", "ab"]
    html = (site / "index.html").read_text(encoding="utf-8")
    assert [e["id"] for e in json.loads((site / "apis.json").read_text("utf-8"))] == expected
    assert [e["id"] for e in _embedded(html)] == expected
    assert re.findall(r'<td><a href="apis/([^"]+)\.html">', html) == expected


HOSTILE_DESC = '</script><script>alert("d")</script> & <!-- tricky \u2028 \u2029'


def test_embedded_json_cannot_close_its_script_tag(tmp_path):
    site = _site(tmp_path, [_rec("alpha", description=HOSTILE_DESC, tags=("<b>",))])
    html = (site / "index.html").read_text(encoding="utf-8")
    block = re.search(r'id="apis-data">(.*?)</script>', html, re.S)[1]
    assert not any(ch in block for ch in "<>&\u2028\u2029")
    assert 'alert("d")' not in html.replace(block, "")
    [entry] = _embedded(html)
    assert entry["description"] == HOSTILE_DESC and entry["tags"] == ["<b>"]
    on_disk = json.loads((site / "apis.json").read_text(encoding="utf-8"))
    assert on_disk == [entry]


def test_hostile_name_is_escaped_in_the_index_table(tmp_path):
    site = _site(tmp_path, [_rec("alpha", name="<script>alert(1)</script>")])
    html = (site / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<tr" in html


def test_one_page_per_api_including_inactive_and_summary_counts_them(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("alive"), _rec("dead")])
    _deactivate(tmp_path, "dead")
    summary = render_site(settings)
    assert sorted(p.name for p in (tmp_path / "site" / "apis").glob("*.html")) == [
        "alive.html",
        "dead.html",
    ]
    assert summary.pages == 2


FULL = _rec(
    "full",
    "Full API",
    "does everything",
    ("maps", "geo"),
    base_url="https://full.example.com/v1",
    spec_url="https://full.example.com/openapi.json",
    declared_auth="apiKey",
    signup_url="https://full.example.com/signup",
)
FULL_ROLLUP = Rollup(
    "full",
    reachable_30d=21,
    working_30d=19,
    days_observed_30d=23,
    p50_ms=137,
    p95_ms=842,
    last_probe_at=datetime(2026, 10, 9, 6, 30, tzinfo=UTC),
    last_outcome="responded",
    last_status=204,
    probe_target="https://full.example.com/v1/ping",
    auth="key-required",
    ratelimit_headers_seen=("x-ratelimit-limit", "retry-after"),
    saw_429_30d=True,
)


def test_api_page_renders_every_detail_payload_field(tmp_path):
    site = _site(tmp_path, [FULL], [FULL_ROLLUP])
    html = (site / "apis" / "full.html").read_text(encoding="utf-8")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    for needle in (
        "full",
        "Full API",
        "does everything",
        "maps",
        "geo",
        "https://full.example.com/v1",
        "https://full.example.com/openapi.json",
        "https://full.example.com/signup",
        "https://full.example.com/v1/ping",
        "21",
        "19",
        "23",
        "137",
        "842",
        "2026-10-09T06:30:00Z",
        "responded",
        "204",
        "key-required",
        "apiKey",
        "x-ratelimit-limit",
        "retry-after",
    ):
        assert needle in text, needle
    assert re.search(r'id="status">([^<]*)<', html)[1] == "active"


def test_inactive_api_page_still_renders_and_says_inactive(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("dead")])
    _deactivate(tmp_path, "dead")
    render_site(settings)
    html = (tmp_path / "site" / "apis" / "dead.html").read_text(encoding="utf-8")
    assert re.search(r'id="status">([^<]*)<', html)[1] == "inactive"


def test_empty_api_page_shows_placeholders_not_none(tmp_path):
    site = _site(tmp_path, [_rec("bare")])
    html = (site / "apis" / "bare.html").read_text(encoding="utf-8")
    assert "None" not in html and "no probes recorded" in html


def jan(day: int, hour: int) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def _probe(url: str, at: datetime, outcome: Outcome, status: int | None, ms: int | None):
    return ProbeResult(url, at, outcome, status, ms, None, None, {}, None)


def _history_rows(html: str) -> list[list[str]]:
    table = re.search(r'<table id="history">(.*?)</table>', html, re.S)
    assert table, "history table not found"
    rows = re.findall(r"<tr>(.*?)</tr>", table[1], re.S)
    return [
        [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        for r in rows
        if "<td" in r
    ]


def test_history_table_is_anchored_to_the_data_not_the_wall_clock(tmp_path):
    settings = Settings(data_dir=tmp_path)
    anchor = datetime(2026, 1, 20, 3, 0, tzinfo=UTC)
    _seed(settings, [_rec("alpha")], [_roll("alpha", 2, 1, 50)], computed_at=anchor)
    url = "https://alpha.example.com/x"
    append_probes(
        settings,
        [
            ("alpha", _probe(url, datetime(2025, 12, 5, 1, tzinfo=UTC), Outcome.responded, 200, 9)),
            ("alpha", _probe(url, jan(2, 1), Outcome.timeout, None, None)),
            ("alpha", _probe(url, jan(2, 9), Outcome.responded, 200, 50)),
            ("alpha", _probe(url, jan(15, 1), Outcome.responded, 500, 80)),
        ],
        now=jan(15, 1),
    )
    render_site(settings)
    html = (tmp_path / "site" / "apis" / "alpha.html").read_text(encoding="utf-8")
    assert _history_rows(html) == [
        ["2026-01-15", "responded", "500", "80"],
        ["2026-01-02", "responded", "200", "50"],
    ]


def test_without_rollups_the_anchor_is_the_newest_last_seen_at(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("alpha")], seen_at=jan(20, 3))
    append_probes(
        settings,
        [
            ("alpha", _probe("https://alpha.example.com", jan(10, 1), Outcome.responded, 200, 7)),
            (
                "alpha",
                _probe(
                    "https://alpha.example.com",
                    datetime(2025, 12, 1, tzinfo=UTC),
                    Outcome.responded,
                    200,
                    1,
                ),
            ),
        ],
        now=jan(10, 1),
    )
    render_site(settings)
    html = (tmp_path / "site" / "apis" / "alpha.html").read_text(encoding="utf-8")
    assert _history_rows(html) == [["2026-01-10", "responded", "200", "7"]]


def test_http_urls_on_the_api_page_are_links(tmp_path):
    site = _site(tmp_path, [FULL], [FULL_ROLLUP])
    html = (site / "apis" / "full.html").read_text(encoding="utf-8")
    for url in (
        "https://full.example.com/v1",
        "https://full.example.com/openapi.json",
        "https://full.example.com/signup",
        "https://full.example.com/v1/ping",
    ):
        assert f'<a href="{url}"' in html


def test_non_http_urls_are_plain_text_never_links(tmp_path):
    bad = _rec(
        "evil",
        base_url="javascript:alert(1)",
        spec_url=" javascript:alert(2)",
        signup_url="data:text/html,<b>x</b>",
    )
    site = _site(tmp_path, [bad], [_roll("evil", 1, 1, 5)])
    html = (site / "apis" / "evil.html").read_text(encoding="utf-8")
    assert "javascript:alert(1)" in html  # shown as text
    assert 'href="javascript' not in html and 'href=" javascript' not in html
    assert 'href="data:' not in html
    assert not re.search(r'href="(?!https?://|\.\./|\.\.?/)', html.replace('href="../', ""))


def test_hostile_name_is_escaped_on_its_api_page(tmp_path):
    site = _site(tmp_path, [_rec("alpha", name="<script>alert(1)</script>")])
    html = (site / "apis" / "alpha.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_unsafe_ids_are_skipped_everywhere_and_never_escape_the_site_dir(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    unsafe = ["../evil", ".hidden", "a/b", "..", "sp ace", "new\nline", ""]
    _seed(settings, [_rec("ok.id-1_x"), *[_rec(i) for i in unsafe]])
    summary = render_site(settings, out_dir=tmp_path / "site" / "out")
    out = tmp_path / "site" / "out"
    assert sorted(p.name for p in (out / "apis").iterdir()) == ["ok.id-1_x.html"]
    assert sorted(p.name for p in (out / "badge").iterdir()) == ["ok.id-1_x.svg"]
    assert [e["id"] for e in json.loads((out / "apis.json").read_text("utf-8"))] == ["ok.id-1_x"]
    assert (summary.pages, summary.badges) == (1, 1)
    written = {
        p.relative_to(tmp_path / "site") for p in (tmp_path / "site").rglob("*") if p.is_file()
    }
    assert all(str(p).startswith("out/") for p in written), written


def _opt_out(settings: Settings, key: str, recorded_at: datetime) -> None:
    conn = connect(settings)
    try:
        record_optouts(conn, [_rec(key)], recorded_at=recorded_at)
    finally:
        conn.close()


def test_opted_out_api_leaves_the_site_and_is_listed_as_removed(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("stay"), _rec("gone")], [_roll("gone", 3, 3, 10)])
    _deactivate(tmp_path, "gone")
    _opt_out(settings, "gone", datetime(2026, 9, 1, 23, 59, tzinfo=UTC))
    summary = render_site(settings)
    site = tmp_path / "site"
    assert not (site / "apis" / "gone.html").exists()
    assert not (site / "badge" / "gone.svg").exists()
    assert [e["id"] for e in json.loads((site / "apis.json").read_text("utf-8"))] == ["stay"]
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "apis/gone.html" not in index and '"id":"gone"' not in index
    removed = (site / "removed.html").read_text(encoding="utf-8")
    assert "gone" in removed and "2026-09-01" in removed
    assert (summary.pages, summary.badges, summary.removed) == (1, 1, 1)


def _removed_items(html: str) -> list[str]:
    block = re.search(r'<ul id="removed">(.*?)</ul>', html, re.S)
    assert block, "removed list not found"
    return [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", li)).strip()
        for li in re.findall(r"<li>(.*?)</li>", block[1], re.S)
    ]


def test_removed_page_is_ordered_by_key_and_counts_optouts_without_a_matching_api(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("stay")])
    _opt_out(settings, "zeta.example.com", datetime(2026, 8, 3, 5, tzinfo=UTC))
    _opt_out(settings, "alpha", datetime(2026, 7, 2, 5, tzinfo=UTC))
    summary = render_site(settings)
    html = (tmp_path / "site" / "removed.html").read_text(encoding="utf-8")
    assert "removed at the maintainer's request" in re.search(r"<h1>(.*?)</h1>", html, re.S)[1]
    assert _removed_items(html) == ["alpha - 2026-07-02", "zeta.example.com - 2026-08-03"]
    assert summary.removed == 2


def test_removed_page_escapes_keys_and_renders_when_empty(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("stay")])
    render_site(settings)
    empty = (tmp_path / "site" / "removed.html").read_text(encoding="utf-8")
    assert _removed_items(empty) == []
    assert "No APIs have been removed" in empty
    _opt_out(settings, "<img src=x onerror=alert(1)>", datetime(2026, 8, 3, tzinfo=UTC))
    render_site(settings)
    html = (tmp_path / "site" / "removed.html").read_text(encoding="utf-8")
    assert "<img src=x" not in html and "&lt;img src=x" in html


def test_render_prunes_stale_pages_and_badges_but_nothing_else(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("stay"), _rec("gone")])
    render_site(settings)
    site = tmp_path / "site"
    assert (site / "apis" / "gone.html").exists() and (site / "badge" / "gone.svg").exists()
    (site / "apis" / "notes.txt").write_text("keep", encoding="utf-8")
    (site / "badge" / "readme.md").write_text("keep", encoding="utf-8")
    (site / "CNAME").write_text("keep", encoding="utf-8")
    (site / "apis" / "sub").mkdir()
    _opt_out(settings, "gone", datetime(2026, 9, 1, tzinfo=UTC))
    render_site(settings)
    assert not (site / "apis" / "gone.html").exists()
    assert not (site / "badge" / "gone.svg").exists()
    assert (site / "apis" / "stay.html").exists() and (site / "badge" / "stay.svg").exists()
    assert (site / "apis" / "notes.txt").exists() and (site / "badge" / "readme.md").exists()
    assert (site / "CNAME").exists() and (site / "apis" / "sub").is_dir()


def _project_html(tmp_path) -> str:
    site = _site(tmp_path, [_rec("alpha")])
    return (site / "project.html").read_text(encoding="utf-8")


def test_project_page_states_exactly_what_a_probe_sends(tmp_path):
    html = _project_html(tmp_path)
    assert 'id="project-page"' in html
    assert html.count(USER_AGENT) >= 1
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    for needle in ("GET", "one request per API per day", "with no credentials", "has no body"):
        assert needle in text, needle


def test_project_page_explains_how_to_opt_out(tmp_path):
    html = _project_html(tmp_path)
    assert 'href="https://github.com/cdcoonce/apipulse/issues"' in html
    assert "opt-out: &lt;host&gt;" in html
    assert "optout.json" in html


def test_project_page_describes_the_json_api(tmp_path):
    html = _project_html(tmp_path)
    assert "/v1/apis?q=" in html and "/v1/apis/{id}" in html


def _search_script(html: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1, "expected exactly one inline script"
    return scripts[0]


def test_index_has_one_inline_search_script_that_never_uses_inner_html(tmp_path):
    site = _site(tmp_path, [_rec("alpha")])
    html = (site / "index.html").read_text(encoding="utf-8")
    script = _search_script(html)
    assert "rankApis" in script and "textContent" in script and "createElement" in script
    assert "innerHTML" not in html and "document.write" not in html
    assert '<input type="search"' in html
    assert not re.search(r"<script[^>]+src=", html)


# (id, name, description, tags, reachable, working, p50) - each ordering rule decides somewhere,
# and the id tie-breaker would give the opposite order wherever a higher rule decides.
RANK_FIXTURE = [
    ("z-top-rel", "Weather", "weather data", ("weather",), 1, 0, 900),
    ("a-one", "WEATHER station", "", (), 10, 10, 10),
    ("z-reach-high", "Weather two", "", (), 20, 5, 50),
    ("a-work-low", "Weather three", "", (), 15, 1, 10),
    ("z-work-high", "Weather four", "", (), 15, 9, 10),
    ("a-p50-slow", "Weather five", "", (), 12, 12, 900),
    ("m-p50-fast", "Weather six", "", (), 12, 12, 5),
    ("b-p50-none", "Weather seven", "", (), 12, 12, None),
    ("a-p50-none", "Weather eight", "", (), 12, 12, None),
    ("tag-only", "Anemometer", "", ("weathervane", "data"), 3, 3, 30),
    ("desc-only", "Radar", "Local WEATHER and data feeds", ("radar",), 2, 2, 20),
    ("geo", "Geocoder", "addresses to points", ("geo",), 30, 30, 1),
    ("no-rollup", "Weather nine", "", (), 0, 0, None),
]
RANK_QUERIES = [
    "weather",
    "WEATHER",
    "weather data",
    "data weather",
    "weather   data weather",
    "geo",
    "radar",
    "nomatch",
    "weather nomatch",
    "e",
    "",
    "   ",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_client_rank_function_orders_exactly_like_the_server_plain_search(tmp_path):
    settings = Settings(data_dir=tmp_path)
    records = [_rec(i, n, d, t) for i, n, d, t, *_ in RANK_FIXTURE]
    rollups = [_roll(i, r, w, p) for i, _, _, _, r, w, p in RANK_FIXTURE if i != "no-rollup"]
    _seed(settings, records, rollups)
    render_site(settings)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    source = re.search(r"// BEGIN rankApis\n(.*?)// END rankApis", html, re.S)
    assert source, "rankApis markers not found"
    apis = _embedded(html)
    program = (
        f"{source[1]}\nconst apis = {json.dumps(apis)};\n"
        f"const queries = {json.dumps(RANK_QUERIES)};\n"
        "console.log(JSON.stringify(queries.map(q => rankApis(apis, q).map(a => a.id))));\n"
    )
    done = subprocess.run(
        ["node", "-"], input=program, capture_output=True, text=True, check=True, timeout=30
    )
    client = json.loads(done.stdout)
    conn = connect(settings)
    try:
        for query, got in zip(RANK_QUERIES, client, strict=True):
            if query.strip():
                want = [h.id for h in search_apis(conn, query, limit=1000, use_fts=False)]
            else:
                want = [e["id"] for e in apis]
            assert got == want, query
    finally:
        conn.close()
    assert client[0][0] == "z-top-rel" and client[2][0] == "z-top-rel"
    assert client[0] != sorted(client[0])  # the fixture is not decided by the id tie-break


def _snapshot(root) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_rendering_is_byte_identical_across_runs_and_output_directories(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    anchor = datetime(2026, 1, 20, 3, tzinfo=UTC)
    _seed(
        settings,
        [
            FULL,
            _rec("quiet", "Quiet", "no data yet"),
            _rec("gone"),
            _rec("dead", "Dead API", "stopped"),
            _rec("xss", "<script>alert(1)</script>", HOSTILE_DESC, ("<b>",)),
        ],
        [FULL_ROLLUP, _roll("dead", 0, 0, None), _roll("xss", 4, 4, 12)],
        computed_at=anchor,
    )
    _deactivate(settings.data_dir, "dead")
    _opt_out(settings, "gone", datetime(2026, 1, 5, tzinfo=UTC))
    append_probes(
        settings,
        [
            ("full", _probe("https://full.example.com", jan(3, 1), Outcome.responded, 200, 40)),
            ("full", _probe("https://full.example.com", jan(4, 1), Outcome.timeout, None, None)),
            ("xss", _probe("https://xss.example.com", jan(4, 2), Outcome.responded, 429, 99)),
        ],
        now=jan(4, 2),
    )
    first = tmp_path / "first"
    render_site(settings, out_dir=first)
    before = _snapshot(first)
    render_site(settings, out_dir=first)
    render_site(settings, out_dir=tmp_path / "fresh")
    assert _snapshot(first) == before
    assert _snapshot(tmp_path / "fresh") == before
    assert {"index.html", "project.html", "removed.html", "apis.json", "apis/full.html"} <= set(
        before
    )
    assert "apis/gone.html" not in before and "apis/dead.html" in before


def test_templates_ship_inside_the_package():
    from importlib.resources import files

    shipped = {p.name for p in files("apipulse").joinpath("templates").iterdir()}
    assert {"base.html", "index.html", "api.html", "project.html", "removed.html"} <= shipped

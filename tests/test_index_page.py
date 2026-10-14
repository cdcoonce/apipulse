"""The redesigned shell, index and About page (issue #34)."""

import hashlib
import html as htmllib
import json
import re
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_site import (
    NOW,
    _deactivate,
    _embedded,
    _probes_for,
    _rec,
    _roll,
    _seed,
    _site,
)

from apipulse.config import Settings
from apipulse.prober import Outcome
from apipulse.render import render_site
from apipulse.rollup import Rollup
from apipulse.store import connect, upsert_rollups

GITHUB = "https://github.com/cdcoonce/apipulse"
RANK_APIS_SHA256 = "fd4cfaeea4df5d528a1a5f33a8137efeaef0e0c186a7e92713aa962e3027f5bf"


def _good(api_id: str, p50: int | None = 100, *, working=5, observed=5, auth="keyless") -> Rollup:
    """A rollup that qualifies for "Works without a key": an endpoint probe, keyless, all worked."""
    target = f"https://{api_id}.example.com/v1/ping"
    return Rollup(
        api_id, observed, working, observed, p50, p50, NOW, "responded", 200, target, auth=auth
    )


def _index(site) -> str:
    return (site / "index.html").read_text(encoding="utf-8")


def _page(site, name: str) -> str:
    return (site / name).read_text(encoding="utf-8")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _card_ids(html: str) -> list[str]:
    return re.findall(r'<a class="card" href="apis/([^"]+)\.html"', html)


def _rows(html: str) -> list[tuple[str, list[tuple[str, str]]]]:
    body = re.search(r'<tbody id="rows">(.*?)</tbody>', html, re.S)
    assert body, "directory tbody not found"
    return [
        (open_tag, re.findall(r"<td([^>]*)>(.*?)</td>", inner, re.S))
        for open_tag, inner in re.findall(r"(<tr[^>]*>)(.*?)</tr>", body[1], re.S)
    ]


def _row(html: str, api_id: str):
    for open_tag, cells in _rows(html):
        if f'href="apis/{api_id}.html"' in cells[0][1]:
            return open_tag, cells
    raise AssertionError(f"row for {api_id} not found")


# --- 1. shell -------------------------------------------------------------------------------

PAGES = [("index.html", "APIs"), ("project.html", "About"), ("removed.html", "Removed")]


@pytest.mark.parametrize(("page", "current"), PAGES)
def test_every_page_has_brand_nav_and_marks_only_the_current_link(tmp_path, page, current):
    html = _page(_site(tmp_path, [_rec("alpha")]), page)
    header = re.search(r"<header.*?</header>", html, re.S)[0]
    assert "apipulse" in re.search(r'<a class="brand"[^>]*>(.*?)</a>', header, re.S)[1]
    links = re.findall(r"<a ([^>]*)>([^<]*)</a>", re.search(r"<nav.*?</nav>", header, re.S)[0])
    assert [(text, re.search(r'href="([^"]+)"', attrs)[1]) for attrs, text in links] == [
        ("APIs", "index.html"),
        ("About", "project.html"),
        ("Removed", "removed.html"),
    ]
    marked = [text for attrs, text in links if 'aria-current="page"' in attrs]
    assert marked == [current]


def test_api_pages_use_the_shell_with_root_relative_nav(tmp_path):
    html = _page(_site(tmp_path, [_rec("alpha")]), "apis/alpha.html")
    assert 'href="../index.html"' in html and 'href="../project.html"' in html
    assert "<footer" in html and "prefers-color-scheme: dark" in html


def test_footer_shows_the_utc_date_of_the_strip_end_and_the_links(tmp_path):
    site = _site(tmp_path, [_rec("alpha")], [_roll("alpha", 1, 1, 5)])
    for name in ("index.html", "project.html", "removed.html", "apis/alpha.html"):
        footer = re.search(r"<footer.*?</footer>", _page(site, name), re.S)[0]
        assert "Data through 2026-10-10" in _text(footer), name
        assert f'href="{GITHUB}"' in footer
        assert re.search(r'href="(\.\./)?project\.html"', footer)


def test_footer_date_follows_the_newest_probe_not_the_rollup_anchor(tmp_path):
    settings = Settings(data_dir=tmp_path)
    url = "https://a.example.com"
    late = datetime(2026, 10, 10, 23, 30, tzinfo=UTC)
    _seed(settings, [_rec("a")], [_roll("a", 1, 1, 5)])
    _probes_for(settings, "a", url, [(late + timedelta(hours=1), Outcome.responded, 200)])
    render_site(settings)
    assert "Data through 2026-10-11" in _page(tmp_path / "site", "index.html")


def test_footer_says_so_when_there_is_no_probe_data(tmp_path, monkeypatch):
    import apipulse.render as render_module

    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("a")])
    monkeypatch.setattr(render_module, "data_anchor", lambda conn: None)
    render_site(settings)
    for name in ("index.html", "project.html", "removed.html", "apis/a.html"):
        html = _page(tmp_path / "site", name)
        assert "No probe data yet" in html and "Data through" not in html, name


def test_no_page_loads_anything_external_and_all_css_is_inline_with_a_dark_override(tmp_path):
    site = _site(tmp_path, [_rec("alpha")])
    for name in ("index.html", "project.html", "removed.html", "apis/alpha.html"):
        html = _page(site, name)
        assert not re.search(r'<link[^>]+rel="?stylesheet', html, re.I), name
        assert not re.search(r"<script[^>]+src=", html), name
        assert "@import" not in html, name
        assert not re.search(r"<link\b", html), name
        style = re.search(r"<style>(.*?)</style>", html, re.S)[1]
        assert ":root" in style and "var(--" in style, name
        assert re.search(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root", style), name


def test_mobile_breakpoint_hides_category_and_median_but_not_auth(tmp_path):
    html = _index(_site(tmp_path, [_rec("alpha")]))
    style = re.search(r"<style>(.*?)</style>", html, re.S)[1]
    mobile = re.search(r"@media \(max-width: 720px\)\s*\{(.*)\}", style, re.S)[1]
    assert re.search(r"\.hide-sm\s*\{\s*display:\s*none", mobile)
    _, cells = _rows(html)[0]
    classes = [re.search(r'class="([^"]*)"', attrs)[1] for attrs, _ in cells]
    assert classes[1].split()[-1] == "hide-sm" and classes[3].split()[-1] == "hide-sm"
    assert "hide-sm" not in classes[4] and classes[4].split()[0] == "auth"
    assert re.search(r"\.auth\s*\{[^}]*white-space:\s*normal", mobile)


# --- 2. works without a key -----------------------------------------------------------------


def _verified_ids(tmp_path, records, rollups) -> list[str]:
    return _card_ids(_index(_site(tmp_path, records, rollups)))


def test_only_apis_that_answered_keyless_at_an_endpoint_on_every_probed_day_are_cards(tmp_path):
    settings = Settings(data_dir=tmp_path)
    base = lambda i: f"https://{i}.example.com"  # noqa: E731
    ids = [
        "ok",
        "inactive",
        "site-depth",
        "no-depth",
        "key-required",
        "unknown",
        "one-down",
        "zero-days",
    ]
    rollups = [
        _good("ok"),
        _good("inactive"),
        replace(_good("site-depth"), probe_target=base("site-depth")),
        replace(_good("no-depth"), probe_target=None),
        _good("key-required", auth="key-required"),
        _good("unknown", auth="unknown"),
        _good("one-down", working=4),
        _good("zero-days", working=0, observed=0),
    ]
    _seed(settings, [_rec(i) for i in ids], rollups)
    _deactivate(tmp_path, "inactive")
    render_site(settings)
    assert _card_ids(_index(tmp_path / "site")) == ["ok"]


def test_cards_are_ordered_by_median_then_id_with_no_median_last(tmp_path):
    ids = ["d-slow", "c-none", "b-fast", "a-none", "e-tie-b", "e-tie-a"]
    p50 = {"d-slow": 900, "c-none": None, "b-fast": 5, "a-none": None, "e-tie-b": 50, "e-tie-a": 50}
    got = _verified_ids(tmp_path, [_rec(i) for i in ids], [_good(i, p50[i]) for i in ids])
    assert got == ["b-fast", "e-tie-a", "e-tie-b", "d-slow", "a-none", "c-none"]


def test_card_shows_name_median_plain_description_first_category_and_strip(tmp_path):
    settings = Settings(data_dir=tmp_path)
    records = [
        _rec("alpha", "Alpha API", "A [fine](https://x.example) <b>API</b>", ("weather", "geo")),
        _rec("bare", "Bare", "", ()),
    ]
    _seed(settings, records, [_good("alpha", 120), _good("bare", None)])
    render_site(settings)
    html = _index(tmp_path / "site")
    cards = re.findall(r'<a class="card" href="apis/([^"]+)\.html">(.*?)</a>', html, re.S)
    by_id = {i: body for i, body in cards}
    alpha = by_id["alpha"]
    assert "Alpha API" in alpha and "120 ms" in alpha
    assert "A fine API" in _text(alpha) and "<b>" not in alpha and "https://x.example" not in alpha
    assert re.search(r'class="chip"[^>]*>geo<', alpha)  # first of the sorted categories
    assert alpha.count("<i") == 31 and 'class="strip"' in alpha
    bare = by_id["bare"]
    assert "—" in _text(bare) and "ms" not in _text(bare).replace("Bare", "")
    assert re.search(r'class="chip"[^>]*>uncategorized<', bare)


def test_card_description_is_cut_at_140_characters(tmp_path):
    desc = " ".join(["word"] * 100)
    html = _index(_site(tmp_path, [_rec("a", description=desc)], [_good("a")]))
    card = re.search(r'<a class="card".*?</a>', html, re.S)[0]
    shown = re.search(r'class="desc"[^>]*>(.*?)</div>', card, re.S)[1]
    assert shown.endswith("…") and len(shown) <= 141 and desc not in html


def test_hostile_name_and_description_are_inert_on_a_card(tmp_path):
    name = "<script>alert(1)</script>"
    desc = '</script><img src=x onerror=alert(2)> [x](javascript:alert(3)) & "q"'
    html = _index(_site(tmp_path, [_rec("a", name, desc)], [_good("a")]))
    assert "<img" not in html and "<script>alert" not in html
    card = re.search(r'<a class="card".*?</a>', html, re.S)[0]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in card
    assert "javascript:" not in card


def test_heading_shows_the_count_and_twelve_cards_are_open_with_the_rest_in_details(tmp_path):
    ids = [f"api-{n:02d}" for n in range(15)]
    html = _index(
        _site(tmp_path, [_rec(i) for i in ids], [_good(i, 10 + n) for n, i in enumerate(ids)])
    )
    heading = re.search(r"<h2[^>]*>\s*Works without a key(.*?)</h2>", html, re.S)[1]
    assert "15" in _text(heading)
    head, rest = html.split('<details class="more"', 1)
    assert _card_ids(head.split("Works without a key", 1)[1]) == ids[:12]
    details = rest.split("</details>", 1)[0]
    assert re.search(r"<summary[^>]*>\s*Show all 15\s*</summary>", details)
    assert _card_ids(details) == ids[12:]


def test_twelve_or_fewer_cards_need_no_details_block(tmp_path):
    ids = [f"api-{n:02d}" for n in range(12)]
    html = _index(_site(tmp_path, [_rec(i) for i in ids], [_good(i) for i in ids]))
    assert len(_card_ids(html)) == 12 and "Show all" not in html


def test_with_no_qualifying_api_the_section_says_why(tmp_path):
    html = _index(_site(tmp_path, [_rec("a")], [_good("a", auth="key-required")]))
    assert _card_ids(html) == []
    assert (
        "No API qualifies yet: it needs an endpoint probe that answered with no key on every "
        "day probed." in _text(html)
    )
    assert "Show all" not in html


def test_qualifying_apis_do_not_show_the_empty_message(tmp_path):
    html = _index(_site(tmp_path, [_rec("a")], [_good("a")]))
    assert "No API qualifies yet" not in html


# --- 3. directory ---------------------------------------------------------------------------


def test_directory_has_one_row_per_entry_in_apis_json_order_all_linked(tmp_path):
    ids = ["aa", "ab", "vv", "ww", "xx"]
    site = _site(
        tmp_path,
        [_rec(i) for i in ids],
        [_roll("xx", 20, 10, 100), _roll("ww", 20, 10, 200), _roll("vv", 20, 10, None)],
    )
    html = _index(site)
    expected = [e["id"] for e in json.loads((site / "apis.json").read_text("utf-8"))]
    assert expected == ["xx", "ww", "vv", "aa", "ab"]
    got = [re.search(r'href="apis/([^"]+)\.html"', cells[0][1])[1] for _, cells in _rows(html)]
    assert got == expected
    assert all(f'href="apis/{i}.html"' in html for i in ids)


def test_row_cells_name_over_id_categories_strip_with_counts_median_and_auth(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(
        settings,
        [_rec("alpha", "Alpha API", "d", ("Open_Data", "geo"), declared_auth="apiKey")],
        [replace(_good("alpha", 120, working=4, observed=5), reachable_30d=5)],
    )
    render_site(settings)
    _, cells = _row(_index(tmp_path / "site"), "alpha")
    assert len(cells) == 5
    name, cats, days, median, auth = (c[1] for c in cells)
    assert re.search(r'<a href="apis/alpha\.html">.*Alpha API.*</a>', name, re.S)
    assert name.index("Alpha API") < name.rindex("alpha") and _text(name) == "Alpha API alpha"
    assert _text(cats) == "geo, open data"
    assert 'class="strip"' in days and _text(days) == "4/5"
    assert _text(median) == "120 ms"
    assert _text(auth) == "No key"


def test_a_row_without_a_median_shows_a_dash(tmp_path):
    _, cells = _row(_index(_site(tmp_path, [_rec("a")])), "a")
    assert _text(cells[3][1]) == "—"


AUTH_CASES = [
    ("keyless", "https://{id}.example.com/ep", "No key"),
    ("key-required", "https://{id}.example.com/ep", "Key required"),
    ("unknown", "https://{id}.example.com/ep", "Unclear"),
    ("keyless", "https://{id}.example.com", "Site only · listed: API key"),
    ("unknown", "https://{id}.example.com", "Site only · listed: API key"),
    (None, None, "Not probed yet · listed: API key"),
]


@pytest.mark.parametrize(("auth", "target", "expected"), AUTH_CASES)
def test_auth_cell_text_follows_probe_depth(tmp_path, auth, target, expected):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("a", declared_auth="apiKey")], [])
    if auth is not None:
        conn_roll = Rollup(
            "a", 3, 3, 3, 10, 10, NOW, "responded", 200, target.format(id="a"), auth=auth
        )
        conn = connect(settings)
        try:
            upsert_rollups(conn, [conn_roll], computed_at=NOW)
        finally:
            conn.close()
    render_site(settings)
    _, cells = _row(_index(tmp_path / "site"), "a")
    assert _text(cells[4][1]) == expected


def test_auth_pills_carry_a_distinct_class_per_measured_result(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(
        settings,
        [_rec("k"), _rec("r"), _rec("u")],
        [_good("k"), _good("r", auth="key-required"), _good("u", auth="unknown")],
    )
    render_site(settings)
    html = _index(tmp_path / "site")
    pills = {i: re.search(r'<span class="(pill [a-z]+)"', _row(html, i)[1][4][1])[1] for i in "kru"}
    assert len(set(pills.values())) == 3


def test_inactive_row_keeps_its_class_and_a_muted_style(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("alive"), _rec("dead")])
    _deactivate(tmp_path, "dead")
    render_site(settings)
    html = _index(tmp_path / "site")
    assert _row(html, "dead")[0] == '<tr class="inactive">'
    assert _row(html, "alive")[0] == "<tr>"
    style = re.search(r"<style>(.*?)</style>", html, re.S)[1]
    assert re.search(r"tr\.inactive[^{]*\{[^}]*(color|opacity)", style)


def test_hostile_name_and_categories_are_escaped_in_the_directory(tmp_path):
    html = _index(_site(tmp_path, [_rec("a", "<img src=x onerror=alert(1)>", "", ("<b>x",))]))
    assert "<img" not in html and "<b>x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html and "&lt;b&gt;x" in html


# --- 4. strip -------------------------------------------------------------------------------


def _strips(html: str) -> list[tuple[str, list[str]]]:
    found = re.findall(r'<span class="strip"([^>]*)>(.*?)</span>', html, re.S)
    return [
        (attrs, re.findall(r"<i(?: class=\"([A-Z]?)\")?></i>", inner)) for attrs, inner in found
    ]


def test_strip_markup_is_an_image_with_a_label_and_31_cells_in_order(tmp_path):
    settings = Settings(data_dir=tmp_path)
    url = "https://a.example.com/ep"
    _seed(settings, [_rec("a")], [replace(_good("a"), probe_target=url)])
    _probes_for(
        settings,
        "a",
        url,
        [
            (NOW, Outcome.responded, 200),
            (NOW - timedelta(days=1), Outcome.responded, 503),
            (NOW - timedelta(days=2), Outcome.timeout, None),
            (NOW - timedelta(days=4), Outcome.responded, 200),
        ],
    )
    render_site(settings)
    html = _index(tmp_path / "site")
    days = next(e["days"] for e in _embedded(html) if e["id"] == "a")
    assert days[-5:] == "W.DRW"
    strips = _strips(html)
    assert strips, "no strip rendered"
    for attrs, cells in strips:
        assert attrs.strip() == 'role="img" aria-label="Worked on 2 of 4 probed days"'
        assert len(cells) == 31
        assert "".join(c or "." for c in cells) == days
    assert html.count('<span class="strip"') == 2  # the card and the directory row
    assert html.count('<i class="W"></i>') == 4 and html.count("<i></i>") == 2 * 27


def test_strip_colours_come_from_tokens_and_a_legend_explains_them(tmp_path):
    html = _index(_site(tmp_path, [_rec("a")]))
    style = re.search(r"<style>(.*?)</style>", html, re.S)[1]
    for cls, token in (("W", "ok"), ("R", "warn"), ("D", "bad")):
        assert re.search(rf"\.strip i\.{cls}\s*\{{\s*background:\s*var\(--{token}\)", style)
    assert re.search(r"\.strip i\s*\{[^}]*background:\s*var\(--none\)", style)
    legend = _text(re.search(r'<div class="legend".*?</div>', html, re.S)[0])
    for label in ("worked", "answered but not OK", "no answer", "not probed"):
        assert label in legend


# --- 5. client filtering --------------------------------------------------------------------


def _script(html: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1, "expected exactly one inline script"
    return scripts[0]


def test_one_inline_script_builds_dom_without_inner_html(tmp_path):
    html = _index(_site(tmp_path, [_rec("alpha")]))
    script = _script(html)
    assert "rankApis" in script and "filterApis" in script
    assert "textContent" in script and "createElement" in script
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert banned not in html, banned
    assert not re.search(r"<script[^>]+src=", html)
    assert "Show more" in html and "Showing" in html


def test_controls_are_a_search_box_status_and_category_selects_and_an_endpoint_checkbox(tmp_path):
    html = _index(_site(tmp_path, [_rec("alpha")]))
    assert re.search(r'<input type="search"[^>]*id="q"', html)
    assert re.search(r'<select[^>]*id="status"', html)
    assert re.search(r'<select[^>]*id="cat"', html)
    assert re.search(r'<input type="checkbox"[^>]*id="ep"', html)
    assert "Endpoint-probed only" in html
    status = re.search(r'<select[^>]*id="status".*?</select>', html, re.S)[0]
    assert re.findall(r'<option value="([^"]*)"', status) == ["", "W", "R", "D"]


def test_category_options_list_every_category_with_its_count_sorted_by_name(tmp_path):
    records = [
        _rec("a", tags=("weather", "Open_Data")),
        _rec("b", tags=("weather", "finance")),
        _rec("c", tags=("weather",)),
    ]
    html = _index(_site(tmp_path, records))
    cat = re.search(r'<select[^>]*id="cat".*?</select>', html, re.S)[0]
    options = re.findall(r'<option value="([^"]*)">(.*?)</option>', cat)
    assert options == [
        ("", "All categories"),
        ("finance", "finance (1)"),
        ("open data", "open data (1)"),
        ("weather", "weather (3)"),
    ]


def test_empty_category_labels_are_dropped_everywhere(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, [_rec("a", tags=("_", "", "  ", "ok")), _rec("b", tags=("__",))], [_good("b")])
    render_site(settings)
    site = tmp_path / "site"
    by_id = {e["id"]: e for e in json.loads((site / "apis.json").read_text("utf-8"))}
    assert by_id["a"]["categories"] == ["ok"] and by_id["b"]["categories"] == []
    html = _index(site)
    cat = re.search(r'<select[^>]*id="cat".*?</select>', html, re.S)[0]
    assert re.findall(r'<option value="([^"]*)"', cat) == ["", "ok"]
    assert _text(_row(html, "a")[1][1][1]) == "ok" and _text(_row(html, "b")[1][1][1]) == ""
    assert re.search(r'class="chip"[^>]*>uncategorized<', html)


def _function(html: str, name: str) -> str:
    source = re.search(rf"// BEGIN {name}\n(.*?)// END {name}", html, re.S)
    assert source, f"{name} markers not found"
    return source[1]


def test_rank_apis_body_is_unchanged(tmp_path):
    body = _function(_index(_site(tmp_path, [_rec("a")])), "rankApis")
    assert hashlib.sha256(body.encode()).hexdigest() == RANK_APIS_SHA256


FILTER_APIS = [
    {"id": "w-geo-ep", "days": "..WW", "categories": ["geo"], "probe_depth": "endpoint"},
    {"id": "r-geo-site", "days": "WR..", "categories": ["geo", "maps"], "probe_depth": "site"},
    {"id": "d-maps-ep", "days": "WWD", "categories": ["maps"], "probe_depth": "endpoint"},
    {"id": "w-maps-none", "days": "RW.", "categories": ["maps"], "probe_depth": None},
    {"id": "dots", "days": "....", "categories": [], "probe_depth": None},
    {"id": "d-geo-ep", "days": "W.D.", "categories": ["geo"], "probe_depth": "endpoint"},
]
FILTER_CASES = [
    ({"status": "", "category": "", "endpointOnly": False}, "none"),
    ({"status": "W", "category": "", "endpointOnly": False}, "status W"),
    ({"status": "R", "category": "", "endpointOnly": False}, "status R"),
    ({"status": "D", "category": "", "endpointOnly": False}, "status D"),
    ({"status": "", "category": "geo", "endpointOnly": False}, "category geo"),
    ({"status": "", "category": "maps", "endpointOnly": False}, "category maps"),
    ({"status": "", "category": "nope", "endpointOnly": False}, "category absent"),
    ({"status": "", "category": "", "endpointOnly": True}, "endpoint only"),
    ({"status": "D", "category": "geo", "endpointOnly": True}, "all three"),
    ({"status": "W", "category": "maps", "endpointOnly": False}, "status and category"),
    ({}, "missing keys"),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_filter_apis_applies_each_filter_alone_and_in_combination(tmp_path):
    html = _index(_site(tmp_path, [_rec("alpha")]))
    program = (
        f"{_function(html, 'filterApis')}\nconst apis = {json.dumps(FILTER_APIS)};\n"
        f"const states = {json.dumps([s for s, _ in FILTER_CASES])};\n"
        "console.log(JSON.stringify(states.map(s => filterApis(apis, s).map(a => a.id))));\n"
    )
    done = subprocess.run(
        ["node", "-"], input=program, capture_output=True, text=True, check=True, timeout=30
    )
    got = dict(zip((label for _, label in FILTER_CASES), json.loads(done.stdout), strict=True))
    assert got["none"] == [a["id"] for a in FILTER_APIS]
    assert got["missing keys"] == got["none"]
    assert got["status W"] == ["w-geo-ep", "w-maps-none"]
    assert got["status R"] == ["r-geo-site"]
    assert got["status D"] == ["d-maps-ep", "d-geo-ep"]
    assert got["category geo"] == ["w-geo-ep", "r-geo-site", "d-geo-ep"]
    assert got["category maps"] == ["r-geo-site", "d-maps-ep", "w-maps-none"]
    assert got["category absent"] == []
    assert got["endpoint only"] == ["w-geo-ep", "d-maps-ep", "d-geo-ep"]
    assert got["all three"] == ["d-geo-ep"]
    assert got["status and category"] == ["w-maps-none"]


# --- 6/7. About page ------------------------------------------------------------------------


def test_about_page_explains_the_labels(tmp_path):
    html = _page(_site(tmp_path, [_rec("alpha")]), "project.html")
    section = re.split(r"<h2[^>]*>\s*What the labels mean\s*</h2>", html)
    assert len(section) == 2, "missing 'What the labels mean' heading"
    text = _text(re.split(r"<h2", section[1])[0])
    for needle in (
        "endpoint probe",
        "site probe",
        "only at endpoints",
        "listed",
        "public-apis",
        "APIs.guru",
        "2xx",
        "any HTTP response",
        "worked",
        "answered",
    ):
        assert needle in text, needle
    legend = re.search(r'<div class="legend".*?</div>', section[1], re.S)
    assert legend and "not probed" in legend[0]

from pathlib import Path

import httpx
import pytest

from apipulse.inventory import (
    PUBLIC_APIS_README_URL,
    fetch_public_apis,
    parse_public_apis,
)
from apipulse.prober import USER_AGENT

FIXTURE = Path(__file__).parent / "fixtures" / "public_apis_readme.md"
HEADER_CASES = Path(__file__).parent / "fixtures" / "public_apis_header_cases.md"


def _records() -> dict:
    return {r.name: r for r in parse_public_apis(FIXTURE.read_text())}


def test_parse_yields_six_records_and_maps_cat_facts():
    records = parse_public_apis(FIXTURE.read_text())
    assert len(records) == 6
    cat = next(r for r in records if r.name == "Cat Facts")
    assert cat.id == "alexwohlbruck.github.io"
    assert cat.base_url == "https://alexwohlbruck.github.io"
    assert cat.signup_url == "https://alexwohlbruck.github.io/cat-facts/"
    assert cat.tags == ("animals",)
    assert cat.declared_auth is None
    assert cat.source == "public-apis"
    assert cat.spec_url is None


def test_weatherbit_strips_bold_www_and_backticks():
    wb = _records()["Weatherbit"]
    assert wb.id == "weatherbit.io"
    assert wb.declared_auth == "apiKey"
    assert wb.tags == ("weather",)


def test_http_scheme_stray_column_and_header_lines():
    records = _records()
    httpbin = records["httpbin"]
    assert httpbin.base_url == "http://httpbin.org"
    assert httpbin.id == "httpbin.org"
    assert records["Extra Column"].declared_auth == "OAuth"
    assert "Broken" not in records
    assert all(not name.startswith("-") and name != "API" for name in records)


def test_no_heading_gives_empty_tags_and_odd_lines_are_skipped():
    md = "| [Early](https://early.example.com) | d | No | Yes | Yes |\n| not a link | x | No |\n"
    records = parse_public_apis(md)
    assert [r.id for r in records] == ["early.example.com"]
    assert records[0].tags == ()
    assert parse_public_apis("") == []


def _case_records() -> dict:
    return {r.name: r for r in parse_public_apis(HEADER_CASES.read_text())}


def test_three_column_apilayer_table_has_no_declared_auth_but_keeps_rows():
    records = _case_records()
    for name in ("Aggregator Alpha", "Beta Docs"):
        assert records[name].declared_auth is None
    alpha = records["Aggregator Alpha"]
    assert alpha.description == "Aggregates things"
    assert alpha.tags == ("apilayer apis",)
    assert alpha.id == "alpha.apilayer-example.com"
    assert alpha.base_url == "https://alpha.apilayer-example.com"
    assert alpha.signup_url == "https://alpha.apilayer-example.com/"
    assert alpha.source == "public-apis"


def test_reordered_table_reads_auth_from_its_header_case_insensitively():
    records = _case_records()
    assert records["Reorder One"].declared_auth == "apiKey"
    assert records["Reorder No"].declared_auth is None


def test_header_state_is_per_table():
    records = _case_records()
    assert records["Ordinary Two"].declared_auth == "OAuth"
    assert records["Ordinary Two"].description == "Ordinary columns"
    assert records["Orphan Row"].declared_auth is None


def test_authentication_header_is_not_the_auth_column():
    assert _case_records()["Lookalike"].declared_auth is None


def test_row_shorter_than_auth_column_yields_none_without_error():
    records = _case_records()
    assert records["Short Row"].declared_auth is None
    assert records["Full Row"].declared_auth == "apiKey"


def test_header_and_separator_without_outer_pipes_are_recognised():
    assert _case_records()["Unpiped One"].declared_auth == "apiKey"


def test_prose_line_before_separator_is_not_a_header():
    md = "Auth text\n|---|---|---|\n| [Row](https://row.example.com) | d | apiKey |\n"
    assert parse_public_apis(md)[0].declared_auth is None


def test_no_pipe_line_clears_the_auth_column():
    md = (
        "| API | Description | Auth |\n|---|---|---|\nprose\n"
        "| [Row](https://row.example.com) | d | apiKey |\n"
    )
    assert parse_public_apis(md)[0].declared_auth is None


def test_fetch_sends_user_agent_and_returns_text():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="# readme")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_public_apis(client=client) == "# readme"
    assert str(seen[0].url) == PUBLIC_APIS_README_URL
    assert seen[0].method == "GET"
    assert seen[0].headers["User-Agent"] == USER_AGENT


def test_fetch_raises_on_server_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_public_apis(client=client)

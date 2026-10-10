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

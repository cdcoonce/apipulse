import json
from pathlib import Path

import httpx
import pytest

from apipulse.inventory import APIS_GURU_INDEX_URL, fetch_apis_guru, parse_apis_guru
from apipulse.prober import USER_AGENT

FIXTURE = Path(__file__).parent / "fixtures" / "apis_guru_list.json"


def _index() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_returns_records_and_maps_github_fields():
    index = _index()
    records = parse_apis_guru(index)
    assert len(records) == 6
    gh = next(r for r in records if r.id == "github.com")
    entry = index["github.com"]["versions"][index["github.com"]["preferred"]]
    assert gh.name == "GitHub v3 REST API"
    assert gh.spec_url == entry["swaggerUrl"]
    assert gh.base_url == "https://github.com"
    assert gh.source == "apis.guru"
    assert gh.declared_auth is None


def test_colon_key_maps_to_dashed_id_and_host_base_url():
    index = _index()
    rec = next(r for r in parse_apis_guru(index) if r.id == "1password.com-events")
    entry = index["1password.com:events"]
    cats = entry["versions"][entry["preferred"]]["info"]["x-apisguru-categories"]
    assert rec.base_url == "https://1password.com"
    assert rec.tags == tuple(cats)


def test_missing_categories_gives_empty_tags_and_description_capped_at_500():
    index = _index()
    records = parse_apis_guru(index)
    assert next(r for r in records if r.id == "alertersystem.com").tags == ()
    assert all(len(r.description) <= 500 for r in records)
    index["long.example.com"] = {
        "preferred": "1",
        "versions": {"1": {"swaggerUrl": "https://x/s.json", "info": {"description": "d" * 700}}},
    }
    long = next(r for r in parse_apis_guru(index) if r.id == "long.example.com")
    assert len(long.description) == 500


def _entry(version: dict, preferred: str | None = "1") -> dict:
    entry: dict = {"versions": {"1": version}}
    if preferred is not None:
        entry["preferred"] = preferred
    return entry


def test_malformed_entries_are_skipped_or_tolerated():
    index = {
        "nospec.example.com": _entry({"info": {"title": "NoSpec"}}),
        "notadict.example.com": "oops",
        "noinfo.example.com": _entry({"swaggerUrl": "https://x/a.json"}),
        "noversions.example.com": {"preferred": "1"},
        "nopreferred.example.com": _entry({"swaggerUrl": "https://x/b.json"}, preferred=None),
        "badpreferred.example.com": _entry({"swaggerUrl": "https://x/c.json"}, preferred="9"),
        "openapi.example.com": _entry({"openapiUrl": "https://x/d.json"}),
    }
    got = {r.id: r for r in parse_apis_guru(index)}
    assert list(got) == [
        "noinfo.example.com",
        "nopreferred.example.com",
        "badpreferred.example.com",
        "openapi.example.com",
    ]
    assert got["nopreferred.example.com"].spec_url == "https://x/b.json"
    assert got["noinfo.example.com"].name == "noinfo.example.com"
    assert got["openapi.example.com"].spec_url == "https://x/d.json"


def test_fetch_sends_get_with_user_agent_and_returns_json():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_index())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_apis_guru(client) == _index()
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == APIS_GURU_INDEX_URL
    assert seen[0].headers["User-Agent"] == USER_AGENT


def test_fetch_raises_on_http_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_apis_guru(client)

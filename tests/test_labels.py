import pytest

from apipulse.labels import category_label, declared_auth_label


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "none listed"),
        ("", "none listed"),
        ("   ", "none listed"),
        ("No", "none listed"),
        ("no", "none listed"),
        ("apiKey", "API key"),
        ("`apiKey`", "API key"),
        ("  APIKEY ", "API key"),
        ("OAuth", "OAuth"),
        ("`OAuth`", "OAuth"),
        ("X-Mashape-Key", "API key"),
        ("`X-Mashape-Key`", "API key"),
        ("User-Agent", "User-Agent header"),
        ("bearer", "unclear"),
        ("api key", "unclear"),
    ],
)
def test_declared_auth_label_maps_the_source_list_vocabulary(raw, expected):
    assert declared_auth_label(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "[apiKey](https://evil.example)",
        "<script>alert(1)</script>",
        "<b>OAuth</b>",
        "x" * 2000,
        "apiKey " + "x" * 2000,
        "**no**",
        "no\nkey",
        "‮apiKey",
    ],
)
def test_hostile_declared_auth_is_only_ever_unclear(raw):
    assert declared_auth_label(raw) == "unclear"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("open_data", "open data"),
        ("Open Data", "open data"),
        ("  Open   Data  ", "open data"),
        ("open__data", "open data"),
        ("Financial", "finance"),
        ("financial", "finance"),
        ("Developer Tools", "development"),
        ("developer_tools", "development"),
        ("weather", "weather"),
        ("", ""),
    ],
)
def test_category_label_normalises_and_aliases(tag, expected):
    assert category_label(tag) == expected

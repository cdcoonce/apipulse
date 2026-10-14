import pytest

from apipulse.labels import category_label, declared_auth_label, plain_text


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain text", "plain text"),
        ("a <b>bold</b> <br/> word", "a bold word"),
        ("</script><img src=x onerror=alert(1)>", ""),
        ("x <!-- hidden --> y", "x y"),
        ("see [the docs](https://example.com/a_(b)) now", "see the docs now"),
        ("![logo](https://example.com/l.png) text", "logo text"),
        ("[a](u1) and [b](u2)", "a and b"),
        ("  lots \n\t of   space  ", "lots of space"),
        ("a < b and c > d", "a < b and c > d"),
        ("", ""),
    ],
)
def test_plain_text_strips_markup_links_and_whitespace(raw, expected):
    assert plain_text(raw, 140) == expected


def test_plain_text_leaves_text_that_fits_untouched_with_no_ellipsis():
    assert plain_text("one two three", 13) == "one two three"
    assert plain_text("one two three", 100) == "one two three"


def test_plain_text_cuts_at_a_word_boundary_and_adds_an_ellipsis():
    text = "alpha beta gamma delta epsilon"
    cut = plain_text(text, 15)
    assert cut == "alpha beta…"
    assert cut.endswith("…") and len(cut) <= 15
    assert not plain_text("alpha beta gamma", 12).endswith(" …")


@pytest.mark.parametrize("limit", [2, 5, 11, 12, 13, 30, 140])
def test_plain_text_never_exceeds_the_limit(limit):
    text = " ".join(["wordy", "longer-word", "x"] * 60)
    out = plain_text(text, limit)
    assert len(out) <= limit and out.endswith("…")
    assert text.startswith(out[:-1].rstrip())


def test_plain_text_hard_cuts_one_long_unbroken_word():
    assert plain_text("a" * 50, 10) == "a" * 9 + "…"


def test_plain_text_is_a_jinja_filter_and_its_output_is_still_autoescaped():
    from apipulse.render import _env

    env = _env()
    assert env.filters["plain_text"] is plain_text
    out = env.from_string("{{ d | plain_text(40) }}").render(d="<b>a</b> & <i>b < c</i>")
    assert out == "a &amp; b &lt; c"

import xml.etree.ElementTree as ET

from apipulse.badge import CHAR_PX, GREEN, GREY, RED, YELLOW, badge_color, badge_text, render_badge
from apipulse.rollup import Rollup, empty_rollup


def _r(reachable=30, observed=30, p50=212) -> Rollup:
    return Rollup("x", reachable, reachable, observed, p50, None, None, None, None, None)


def test_badge_text_variants():
    assert badge_text(_r(30, 30, 212)) == "reachable 30/30 · 212 ms"
    assert badge_text(_r(29, 30, None)) == "reachable 29/30"
    assert badge_text(None) == "no data"
    assert badge_text(_r(0, 0, None)) == "no data"
    assert badge_text(_r(4, 4, 212)) == "reachable 4/4 · 212 ms"
    assert badge_text(_r(3, 4, None)) == "reachable 3/4"
    assert badge_text(empty_rollup("x")) == "no data"


def test_badge_color_thresholds():
    assert badge_color(_r(30)) == GREEN
    assert badge_color(_r(27)) == GREEN
    assert badge_color(_r(26)) == YELLOW
    assert badge_color(_r(15)) == YELLOW
    assert badge_color(_r(14)) == RED
    assert badge_color(None) == GREY
    assert badge_color(_r(20, 0)) == GREY


def test_badge_color_uses_observed_days_as_denominator():
    assert badge_color(_r(4, 4)) == GREEN
    assert badge_color(_r(3, 4)) == YELLOW
    assert badge_color(_r(1, 4)) == RED
    assert badge_color(_r(9, 10)) == GREEN
    assert badge_color(_r(1, 2)) == YELLOW
    assert badge_color(_r(0, 1)) == RED


def test_badge_color_full_window_cases_pinned():
    assert badge_color(_r(27, 30)) == GREEN
    assert badge_color(_r(26, 30)) == YELLOW
    assert badge_color(_r(15, 30)) == YELLOW
    assert badge_color(_r(14, 30)) == RED


SVG = "{http://www.w3.org/2000/svg}"


def test_render_badge_is_valid_svg_with_title_and_color():
    root = ET.fromstring(render_badge(_r(26, 30, 212), "x"))
    assert root.tag == f"{SVG}svg"
    assert root.find(f"{SVG}title").text == "apipulse: reachable 26/30 · 212 ms"
    rects = root.findall(f"{SVG}rect")
    assert rects[1].get("fill") == YELLOW


def test_width_grows_with_text_and_matches_formula():
    short = ET.fromstring(render_badge(None, "x"))
    long_ = ET.fromstring(render_badge(_r(30, 30, 212), "x"))
    assert int(short.get("width")) < int(long_.get("width"))
    lw = round(len("apipulse") * CHAR_PX + 10)
    rw = round(len("reachable 30/30 · 212 ms") * CHAR_PX + 10)
    assert int(long_.get("width")) == lw + rw
    assert int(short.get("width")) == lw + round(len("no data") * CHAR_PX + 10)


def test_title_and_width_follow_observed_days_text():
    root = ET.fromstring(render_badge(_r(4, 4, 212), "x"))
    assert root.find(f"{SVG}title").text == "apipulse: reachable 4/4 · 212 ms"
    assert root.findall(f"{SVG}rect")[1].get("fill") == GREEN
    lw = round(len("apipulse") * CHAR_PX + 10)
    rw = round(len("reachable 4/4 · 212 ms") * CHAR_PX + 10)
    assert int(root.get("width")) == lw + rw


def test_text_is_xml_escaped(monkeypatch):
    monkeypatch.setattr("apipulse.badge.badge_text", lambda rollup: "a & b <c>")
    root = ET.fromstring(render_badge(None, "x"))
    assert root.find(f"{SVG}title").text == "apipulse: a & b <c>"

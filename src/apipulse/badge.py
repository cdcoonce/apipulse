from xml.sax.saxutils import escape

from apipulse.rollup import Rollup

LABEL = "apipulse"
GREEN = "#4c1"
YELLOW = "#dfb317"
RED = "#e05d44"
GREY = "#9f9f9f"
CHAR_PX = 6.5
PAD_PX = 10


def _has_data(rollup: Rollup | None) -> bool:
    return rollup is not None and rollup.days_observed_30d > 0


def badge_text(rollup: Rollup | None) -> str:
    if rollup is None or not _has_data(rollup):
        return "no data"
    text = f"reachable {rollup.reachable_30d}/30"
    if rollup.p50_ms is not None:
        text += f" · {rollup.p50_ms} ms"
    return text


def badge_color(rollup: Rollup | None) -> str:
    if rollup is None or not _has_data(rollup):
        return GREY
    if rollup.reachable_30d >= 27:
        return GREEN
    if rollup.reachable_30d >= 15:
        return YELLOW
    return RED


def _fmt(value: float) -> str:
    return f"{value:g}"


def render_badge(rollup: Rollup | None, api_id: str) -> str:
    raw = badge_text(rollup)
    text = escape(raw)
    color = badge_color(rollup)
    lw = round(len(LABEL) * CHAR_PX + PAD_PX)
    rw = round(len(raw) * CHAR_PX + PAD_PX)
    width = lw + rw
    title = f"{LABEL}: {text}"
    font = 'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11" fill="#fff"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img"'
        f' aria-label="{title}">'
        f"<title>{title}</title>"
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{rw}" height="20" fill="{color}"/>'
        f'<text x="{_fmt(lw / 2)}" y="14" text-anchor="middle" {font}>{escape(LABEL)}</text>'
        f'<text x="{_fmt(lw + rw / 2)}" y="14" text-anchor="middle" {font}>{text}</text>'
        f"<!-- {escape(api_id).replace('--', '-')} -->"
        f"</svg>\n"
    )

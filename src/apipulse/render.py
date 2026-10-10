from dataclasses import dataclass
from pathlib import Path

from apipulse.badge import render_badge
from apipulse.config import Settings
from apipulse.store import connect, load_apis, load_rollups


@dataclass(frozen=True)
class RenderSummary:
    pages: int
    removed: int
    badges: int
    out_dir: Path


def render_site(settings: Settings, *, out_dir: Path | None = None) -> RenderSummary:
    if out_dir is None:
        out_dir = settings.data_dir / "site"
    conn = connect(settings)
    try:
        apis = load_apis(conn, active_only=False)
        rollups = {r.api_id: r for r in load_rollups(conn)}
    finally:
        conn.close()
    badge_dir = out_dir / "badge"
    badge_dir.mkdir(parents=True, exist_ok=True)
    for api in apis:
        svg = render_badge(rollups.get(api.id), api.id)
        (badge_dir / f"{api.id}.svg").write_text(svg, encoding="utf-8")
    return RenderSummary(pages=0, removed=0, badges=len(apis), out_dir=out_dir)


def format_render_summary(s: RenderSummary) -> str:
    return f"render: {s.pages} api pages, {s.removed} removed, badges: {s.badges} -> {s.out_dir}"

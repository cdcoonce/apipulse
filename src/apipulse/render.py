import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup

from apipulse.api import detail_payload, search_summary
from apipulse.badge import render_badge
from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import USER_AGENT
from apipulse.rollup import WINDOW_DAYS, Rollup, empty_rollup
from apipulse.store import (
    ProbeRow,
    SearchHit,
    connect,
    data_anchor,
    load_apis_with_state,
    load_optouts,
    load_rollups,
    read_probes,
    search_order_key,
)


@dataclass(frozen=True)
class RenderSummary:
    pages: int
    removed: int
    badges: int
    out_dir: Path


_SAFE_ID = re.compile(r"[A-Za-z0-9._-]+")


def is_safe_id(api_id: str) -> bool:
    """Ids become file names: allow only a plain, non-hidden name."""
    return _SAFE_ID.fullmatch(api_id) is not None and not api_id.startswith(".")


def is_http_url(value: object) -> bool:
    """True only for http(s) URLs with no whitespace or control characters."""
    if not isinstance(value, str) or not value.isprintable() or any(c.isspace() for c in value):
        return False
    return value.lower().startswith(("http://", "https://"))


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("apipulse", "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.globals["is_http_url"] = is_http_url
    return env


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hit(api: ApiRecord, active: bool, rollup: Rollup | None) -> SearchHit:
    rollup = rollup or empty_rollup(api.id)
    return SearchHit(
        id=api.id,
        name=api.name,
        reachable_30d=rollup.reachable_30d,
        working_30d=rollup.working_30d,
        p50_ms=rollup.p50_ms,
        last_probe_at=rollup.last_probe_at,
        active=active,
        relevance=0.0,
        auth=rollup.auth,
    )


def _script_safe(text: str) -> str:
    """Escape JSON for embedding in a script element so data cannot close the tag."""
    for char, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(char, escape)
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _entry(api: ApiRecord, hit: SearchHit) -> dict:
    return {**search_summary(hit), "description": api.description, "tags": list(api.tags)}


def _prune(directory: Path, suffix: str, keep: set[str]) -> None:
    """Delete stale generated files; only direct children with this suffix are touched."""
    for path in directory.iterdir():
        if path.suffix == suffix and path.name not in keep and not path.is_dir():
            path.unlink()


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _window_probes(settings: Settings, anchor: datetime | None) -> dict[str, list[ProbeRow]]:
    """Read the 30-day probe window once, grouped by API id."""
    grouped: dict[str, list[ProbeRow]] = {}
    if anchor is None:
        return grouped
    for row in read_probes(settings, since=anchor - timedelta(days=WINDOW_DAYS)):
        grouped.setdefault(row.api_id, []).append(row)
    return grouped


def render_site(settings: Settings, *, out_dir: Path | None = None) -> RenderSummary:
    """Write the static site; the output depends only on the database and probe files."""
    if out_dir is None:
        out_dir = settings.data_dir / "site"
    conn = connect(settings)
    try:
        optouts = load_optouts(conn)
        gone = {key for key, _ in optouts}
        apis = [
            (api, active)
            for api, active in load_apis_with_state(conn)
            if is_safe_id(api.id) and api.id not in gone
        ]
        rollups = {r.api_id: r for r in load_rollups(conn)}
        anchor = data_anchor(conn)
    finally:
        conn.close()
    probes = _window_probes(settings, anchor)
    env = _env()
    pages_dir, badge_dir = out_dir / "apis", out_dir / "badge"
    pages_dir.mkdir(parents=True, exist_ok=True)
    badge_dir.mkdir(parents=True, exist_ok=True)

    hits = sorted(
        (_hit(api, active, rollups.get(api.id)) for api, active in apis), key=search_order_key
    )
    by_id = {api.id: api for api, _ in apis}
    entries = [_entry(by_id[h.id], h) for h in hits]
    apis_json = _dumps(entries)
    _write(out_dir / "apis.json", apis_json)
    index = env.get_template("index.html")
    _write(
        out_dir / "index.html",
        index.render(rows=entries, apis_json=Markup(_script_safe(apis_json)), root=""),
    )
    page = env.get_template("api.html")
    for api, active in apis:
        rollup = rollups.get(api.id) or empty_rollup(api.id)
        payload = detail_payload(api, active, rollup, probes.get(api.id, []))
        _write(pages_dir / f"{api.id}.html", page.render(d=payload, root="../"))
        _write(badge_dir / f"{api.id}.svg", render_badge(rollups.get(api.id), api.id))
    removed = [{"key": key, "date": recorded_at[:10]} for key, recorded_at in optouts]
    _write(
        out_dir / "removed.html", env.get_template("removed.html").render(removed=removed, root="")
    )
    project = env.get_template("project.html")
    _write(out_dir / "project.html", project.render(user_agent=USER_AGENT, root=""))
    _prune(pages_dir, ".html", {f"{api.id}.html" for api, _ in apis})
    _prune(badge_dir, ".svg", {f"{api.id}.svg" for api, _ in apis})
    return RenderSummary(pages=len(apis), removed=len(optouts), badges=len(apis), out_dir=out_dir)


def format_render_summary(s: RenderSummary) -> str:
    return f"render: {s.pages} api pages, {s.removed} removed, badges: {s.badges} -> {s.out_dir}"

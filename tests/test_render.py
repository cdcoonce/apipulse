from datetime import UTC, datetime
from pathlib import Path

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.render import RenderSummary, format_render_summary, render_site
from apipulse.rollup import Rollup
from apipulse.store import connect, upsert_apis, upsert_rollups

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _rec(id: str) -> ApiRecord:
    return ApiRecord(id, id, "", (), f"https://{id}.example.com", None, None, None, "seed")


def _seed_db(settings: Settings) -> None:
    conn = connect(settings)
    try:
        upsert_apis(conn, [_rec("a"), _rec("b"), _rec("c")], seen_at=NOW)
        conn.execute("UPDATE apis SET active = 0 WHERE id = 'b'")
        rollups = [
            Rollup("a", 30, 30, 30, 100, 200, None, None, None, None),
            Rollup("b", 5, 5, 30, 300, 400, None, None, None, None),
        ]
        upsert_rollups(conn, rollups, computed_at=datetime(2026, 10, 10, tzinfo=UTC))
        conn.commit()
    finally:
        conn.close()


def test_render_site_writes_a_badge_per_api_including_inactive(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed_db(settings)
    summary = render_site(settings)
    files = sorted((tmp_path / "site" / "badge").glob("*.svg"))
    assert [f.name for f in files] == ["a.svg", "b.svg", "c.svg"]
    assert (summary.badges, summary.pages, summary.removed) == (3, 3, 0)
    assert "no data" in (tmp_path / "site/badge/c.svg").read_text()
    assert "reachable 30/30" in (tmp_path / "site/badge/a.svg").read_text()
    assert "reachable 5/30" in (tmp_path / "site/badge/b.svg").read_text()


def test_render_twice_is_byte_identical(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed_db(settings)
    render_site(settings)
    first = {p.name: p.read_bytes() for p in (tmp_path / "site/badge").glob("*.svg")}
    render_site(settings)
    second = {p.name: p.read_bytes() for p in (tmp_path / "site/badge").glob("*.svg")}
    assert first == second and len(first) == 3


def test_out_dir_is_honored(tmp_path):
    settings = Settings(data_dir=tmp_path / "d")
    _seed_db(settings)
    out = tmp_path / "elsewhere"
    summary = render_site(settings, out_dir=out)
    assert summary.out_dir == out
    assert (out / "badge" / "a.svg").exists()
    assert not (tmp_path / "d" / "site").exists()


def test_format_render_summary():
    s = RenderSummary(pages=0, removed=0, badges=3, out_dir=Path("/x/site"))
    assert format_render_summary(s) == "render: 0 api pages, 0 removed, badges: 3 -> /x/site"

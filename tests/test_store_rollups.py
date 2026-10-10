from datetime import UTC, datetime

from apipulse.config import Settings
from apipulse.rollup import Rollup, empty_rollup
from apipulse.store import connect, load_rollup, load_rollups, upsert_rollups

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _full(api_id: str = "a", reachable: int = 5) -> Rollup:
    return Rollup(
        api_id=api_id,
        reachable_30d=reachable,
        working_30d=4,
        days_observed_30d=6,
        p50_ms=120,
        p95_ms=480,
        last_probe_at=datetime(2026, 10, 9, 8, 30, tzinfo=UTC),
        last_outcome="responded",
        last_status=200,
        probe_target="https://a.example.com",
    )


def test_upsert_then_load_round_trips_and_second_upsert_replaces(tmp_path):
    conn = connect(Settings(data_dir=tmp_path))
    try:
        upsert_rollups(conn, [_full(), empty_rollup("b")], computed_at=NOW)
        assert load_rollup(conn, "a") == _full()
        assert load_rollup(conn, "b") == empty_rollup("b")
        assert load_rollup(conn, "zzz") is None
        upsert_rollups(conn, [_full(reachable=9)], computed_at=NOW)
        assert load_rollup(conn, "a") == _full(reachable=9)
        assert [r.api_id for r in load_rollups(conn)] == ["a", "b"]
    finally:
        conn.close()

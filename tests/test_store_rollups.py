import sqlite3
from dataclasses import replace
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


def test_signal_fields_round_trip_through_the_store(tmp_path):
    rollup = replace(
        _full(),
        auth="key-required",
        ratelimit_headers_seen=("retry-after", "x-a"),
        saw_429_30d=True,
    )
    conn = connect(Settings(data_dir=tmp_path))
    try:
        upsert_rollups(conn, [rollup], computed_at=NOW)
        assert load_rollup(conn, "a") == rollup
    finally:
        conn.close()


def test_old_rollups_table_gains_signal_columns_on_connect(tmp_path):
    old = sqlite3.connect(tmp_path / "apipulse.db")
    old.execute(
        "CREATE TABLE rollups (api_id TEXT PRIMARY KEY, reachable_30d INTEGER NOT NULL,"
        " working_30d INTEGER NOT NULL, days_observed_30d INTEGER NOT NULL, p50_ms INTEGER,"
        " p95_ms INTEGER, last_probe_at TEXT, last_outcome TEXT, last_status INTEGER,"
        " probe_target TEXT, computed_at TEXT NOT NULL)"
    )
    old.execute(
        "INSERT INTO rollups VALUES ('a', 1, 1, 1, NULL, NULL, NULL, NULL, NULL, NULL, 'x')"
    )
    old.commit()
    old.close()
    settings = Settings(data_dir=tmp_path)
    connect(settings).close()
    conn = connect(settings)
    try:
        loaded = load_rollup(conn, "a")
        assert loaded is not None
        assert (loaded.auth, loaded.ratelimit_headers_seen, loaded.saw_429_30d) == (
            "unknown",
            (),
            False,
        )
    finally:
        conn.close()

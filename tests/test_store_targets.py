import sqlite3
from datetime import UTC, datetime

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.store import connect, load_probe_targets, set_probe_target, upsert_apis

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _rec(id: str) -> ApiRecord:
    return ApiRecord(
        id=id,
        name=id,
        description="",
        tags=(),
        base_url=f"https://{id}.example.com",
        spec_url=None,
        declared_auth=None,
        signup_url=None,
        source="seed",
    )


def _columns(conn) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(apis)")}


def test_fresh_db_has_target_columns(tmp_path):
    conn = connect(Settings(data_dir=tmp_path))
    try:
        assert {"probe_target", "target_selected_at"} <= _columns(conn)
    finally:
        conn.close()


def test_old_schema_db_gains_columns_and_connect_is_idempotent(tmp_path):
    raw = sqlite3.connect(tmp_path / "apipulse.db")
    raw.execute(
        "CREATE TABLE apis (id TEXT PRIMARY KEY, name TEXT NOT NULL,"
        " description TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',"
        " base_url TEXT NOT NULL, spec_url TEXT, declared_auth TEXT, signup_url TEXT,"
        " source TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,"
        " active INTEGER NOT NULL DEFAULT 1)"
    )
    raw.commit()
    raw.close()
    settings = Settings(data_dir=tmp_path)
    for _ in range(2):
        conn = connect(settings)
        try:
            assert {"probe_target", "target_selected_at"} <= _columns(conn)
        finally:
            conn.close()


def test_set_and_load_probe_targets_round_trip(tmp_path):
    conn = connect(Settings(data_dir=tmp_path))
    try:
        upsert_apis(conn, [_rec("a"), _rec("b")], seen_at=NOW)
        assert load_probe_targets(conn) == {}
        set_probe_target(conn, "a", "https://a.example.com/x", selected_at=NOW)
        assert load_probe_targets(conn) == {"a": ("https://a.example.com/x", NOW)}
    finally:
        conn.close()

from datetime import UTC, datetime

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.store import UpsertSummary, connect, load_apis, record_optouts, upsert_apis


def _settings(tmp_path):
    return Settings(data_dir=tmp_path / "nested" / "data")


def test_connect_creates_db_and_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    connect(settings).close()
    assert (settings.data_dir / "apipulse.db").exists()
    conn = connect(settings)
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"apis", "optouts"} <= tables


def _rec(id: str, tags: tuple[str, ...] = ()) -> ApiRecord:
    return ApiRecord(
        id=id,
        name=f"name-{id}",
        description="desc",
        tags=tags,
        base_url=f"https://{id}.example.com",
        spec_url=None,
        declared_auth="none",
        signup_url=None,
        source="seed",
    )


T1 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
T3 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def test_upsert_inserts_and_load_apis_round_trips(tmp_path):
    conn = connect(_settings(tmp_path))
    records = [_rec("b", ("x", "y")), _rec("a")]
    summary = upsert_apis(conn, records, seen_at=T1)
    assert summary == UpsertSummary(2, 0, 0)
    loaded = load_apis(conn)
    assert [r.id for r in loaded] == ["a", "b"]
    assert loaded[1] == records[0]
    assert loaded[1].tags == ("x", "y")


def test_second_upsert_keeps_first_seen_and_advances_last_seen(tmp_path):
    conn = connect(_settings(tmp_path))
    records = [_rec("a"), _rec("b")]
    upsert_apis(conn, records, seen_at=T1)
    summary = upsert_apis(conn, records, seen_at=T2)
    assert summary == UpsertSummary(0, 2, 0)
    row = conn.execute("SELECT * FROM apis WHERE id = 'a'").fetchone()
    assert row["first_seen_at"] == T1.isoformat()
    assert row["last_seen_at"] == T2.isoformat()


def test_dropped_record_is_deactivated_not_deleted_and_can_return(tmp_path):
    conn = connect(_settings(tmp_path))
    a, b = _rec("a"), _rec("b")
    upsert_apis(conn, [a, b], seen_at=T1)
    summary = upsert_apis(conn, [a], seen_at=T2)
    assert summary == UpsertSummary(0, 1, 1)
    row = conn.execute("SELECT active FROM apis WHERE id = 'b'").fetchone()
    assert row["active"] == 0
    assert [r.id for r in load_apis(conn, active_only=False)] == ["a", "b"]
    assert [r.id for r in load_apis(conn)] == ["a"]
    upsert_apis(conn, [a, b], seen_at=T3)
    row = conn.execute("SELECT active FROM apis WHERE id = 'b'").fetchone()
    assert row["active"] == 1


def test_record_optouts_is_idempotent(tmp_path):
    conn = connect(_settings(tmp_path))
    removed = [_rec("a"), _rec("b")]
    record_optouts(conn, removed, recorded_at=T1)
    record_optouts(conn, removed, recorded_at=T2)
    rows = conn.execute("SELECT * FROM optouts ORDER BY key").fetchall()
    assert [r["key"] for r in rows] == ["a", "b"]
    assert rows[0]["recorded_at"] == T1.isoformat()

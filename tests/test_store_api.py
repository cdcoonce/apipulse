from datetime import UTC, datetime

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.store import connect, load_api, upsert_apis

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


def test_load_api_returns_record_and_active_flag_or_none(tmp_path):
    conn = connect(Settings(data_dir=tmp_path))
    try:
        upsert_apis(conn, [_rec("a"), _rec("b")], seen_at=NOW)
        assert load_api(conn, "a") == (_rec("a"), True)
        upsert_apis(conn, [_rec("b")], seen_at=NOW)
        assert load_api(conn, "a") == (_rec("a"), False)
        assert load_api(conn, "nope") is None
    finally:
        conn.close()

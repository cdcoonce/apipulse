from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apipulse.api import create_app
from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.store import connect, record_optouts, upsert_apis

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
REMOVED = {"detail": "removed at the maintainer's request"}


def _rec(id: str) -> ApiRecord:
    return ApiRecord(
        id=id,
        name=id,
        description="Weather forecasts",
        tags=("weather",),
        base_url=f"https://{id}.example.com",
        spec_url=None,
        declared_auth=None,
        signup_url=None,
        source="seed",
    )


def _client(tmp_path) -> TestClient:
    """`gone` is opted out and deactivated, `idle` is deactivated only, `live` is active."""
    settings = Settings(data_dir=tmp_path)
    conn = connect(settings)
    try:
        everyone = [_rec("gone"), _rec("idle"), _rec("live")]
        upsert_apis(conn, everyone, seen_at=NOW)
        upsert_apis(conn, [_rec("live")], seen_at=NOW)
        record_optouts(conn, [_rec("gone")], recorded_at=NOW)
        # An opt-out recorded for an id that never entered `apis`.
        record_optouts(conn, [_rec("never-listed")], recorded_at=NOW)
    finally:
        conn.close()
    return TestClient(create_app(settings))


def test_detail_of_opted_out_api_is_410(tmp_path):
    response = _client(tmp_path).get("/v1/apis/gone")
    assert response.status_code == 410
    assert response.json() == REMOVED


def test_detail_of_opted_out_id_absent_from_apis_is_410(tmp_path):
    response = _client(tmp_path).get("/v1/apis/never-listed")
    assert response.status_code == 410
    assert response.json() == REMOVED


def test_detail_of_inactive_not_opted_out_api_is_200_inactive(tmp_path):
    response = _client(tmp_path).get("/v1/apis/idle")
    assert response.status_code == 200
    assert response.json()["active"] is False


def test_detail_of_unknown_id_is_still_404(tmp_path):
    response = _client(tmp_path).get("/v1/apis/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown api id"}


def test_search_route_excludes_opted_out_api(tmp_path):
    results = _client(tmp_path).get("/v1/apis", params={"q": "weather"}).json()["results"]
    assert {r["id"]: r["active"] for r in results} == {"idle": False, "live": True}

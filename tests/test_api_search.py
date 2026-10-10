from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apipulse.api import create_app
from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.rollup import Rollup
from apipulse.store import connect, upsert_apis, upsert_rollups

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _rec(id: str, description: str, tags: tuple[str, ...]) -> ApiRecord:
    return ApiRecord(
        id=id,
        name=id,
        description=description,
        tags=tags,
        base_url=f"https://{id}.example.com",
        spec_url=None,
        declared_auth=None,
        signup_url=None,
        source="seed",
    )


def _roll(api_id: str, reachable: int, working: int, p50: int) -> Rollup:
    return Rollup(api_id, reachable, working, 30, p50, None, NOW, "responded", 200, None)


SEED = [
    _rec("openweathermap", "Weather forecasts", ("weather",)),
    _rec("weatherbit", "Weather data", ("weather",)),
    _rec("geocodio", "Geocoding addresses", ("geo",)),
    _rec("nws", "National Weather Service", ("weather", "government")),
    _rec("deadapi", "Weather archive", ("weather",)),
]


def _client(tmp_path) -> TestClient:
    settings = Settings(data_dir=tmp_path)
    conn = connect(settings)
    try:
        upsert_apis(conn, SEED, seen_at=NOW)
        upsert_rollups(
            conn,
            [_roll("openweathermap", 30, 29, 100), _roll("weatherbit", 25, 20, 150)],
            computed_at=datetime(2026, 10, 10, tzinfo=UTC),
        )
    finally:
        conn.close()
    return TestClient(create_app(settings))


def test_search_route_payload_shape(tmp_path):
    response = _client(tmp_path).get("/v1/apis", params={"q": " weather "})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "weather"
    assert body["count"] == len(body["results"]) == 4
    for item in body["results"]:
        assert set(item) == {
            "id",
            "name",
            "reachable_30d",
            "working_30d",
            "p50_ms",
            "last_probe_at",
            "active",
        }
    first = body["results"][0]
    assert first["last_probe_at"] == "2026-10-10T12:00:00Z"
    assert body["results"][-1]["last_probe_at"] is None


def test_search_route_validation(tmp_path):
    client = _client(tmp_path)
    assert client.get("/v1/apis?q=").status_code == 422
    assert client.get("/v1/apis?q=%20%20").status_code == 422
    assert client.get("/v1/apis").status_code == 422
    assert client.get("/v1/apis?q=weather&limit=0").status_code == 422
    assert client.get("/v1/apis?q=weather&limit=201").status_code == 422
    capped = client.get("/v1/apis?q=weather&limit=2").json()
    assert capped["count"] == 2


def test_detail_route_still_works(tmp_path):
    response = _client(tmp_path).get("/v1/apis/openweathermap")
    assert response.status_code == 200
    assert response.json()["id"] == "openweathermap"

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from apipulse.api import build_detail, create_app
from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import Outcome, ProbeResult
from apipulse.rollup import Rollup
from apipulse.store import append_probes, connect, upsert_apis, upsert_rollups

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _rec(id: str) -> ApiRecord:
    return ApiRecord(
        id=id,
        name=id.upper(),
        description=f"about {id}",
        tags=("t1", "t2"),
        base_url=f"https://{id}.example.com",
        spec_url=f"https://{id}.example.com/spec.json",
        declared_auth=None,
        signup_url=None,
        source="seed",
    )


def _seed(settings: Settings, ids: list[str]) -> None:
    conn = connect(settings)
    try:
        upsert_apis(conn, [_rec(i) for i in ids], seen_at=NOW)
    finally:
        conn.close()


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_unknown_id_is_404(tmp_path):
    response = _client(Settings(data_dir=tmp_path)).get("/v1/apis/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown api id"}


def test_api_without_rollup_or_probes_has_empty_stats(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a"])
    response = _client(settings).get("/v1/apis/a")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "a"
    assert body["name"] == "A"
    assert body["tags"] == ["t1", "t2"]
    assert body["active"] is True
    assert body["reachable_30d"] == 0
    assert body["latency"] == {"p50_ms": None, "p95_ms": None}
    assert body["last_probe"] is None
    assert body["history"] == []
    assert body["probe_target"] is None


def test_rollup_values_appear_in_payload(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a"])
    rollup = Rollup(
        api_id="a",
        reachable_30d=5,
        working_30d=4,
        days_observed_30d=6,
        p50_ms=120,
        p95_ms=480,
        last_probe_at=datetime(2026, 10, 10, 7, 12, tzinfo=UTC),
        last_outcome="responded",
        last_status=200,
        probe_target="https://a.example.com/health",
    )
    conn = connect(settings)
    try:
        upsert_rollups(conn, [rollup], computed_at=NOW)
    finally:
        conn.close()
    body = _client(settings).get("/v1/apis/a").json()
    assert (body["reachable_30d"], body["working_30d"], body["days_observed_30d"]) == (5, 4, 6)
    assert body["latency"] == {"p50_ms": 120, "p95_ms": 480}
    assert body["probe_target"] == "https://a.example.com/health"
    assert body["last_probe"] == {
        "at": "2026-10-10T07:12:00Z",
        "outcome": "responded",
        "status": 200,
    }


def _probe(at: datetime, *, status: int = 200, ms: int = 100) -> ProbeResult:
    return ProbeResult(
        url="https://x.example.com",
        probed_at=at,
        outcome=Outcome.responded,
        status=status,
        elapsed_ms=ms,
        tls_ok=True,
        final_url=None,
        ratelimit_headers={},
        error=None,
    )


def _append(settings: Settings, rows: list[tuple[str, datetime, int]]) -> None:
    results = [(api_id, _probe(at, status=status)) for api_id, at, status in rows]
    append_probes(settings, results, now=NOW)


def test_history_is_one_newest_row_per_day_newest_first_within_30_days(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a"])
    day1 = NOW - timedelta(days=1)
    _append(
        settings,
        [
            ("a", NOW, 200),
            ("a", day1.replace(hour=3), 200),
            ("a", day1.replace(hour=9), 500),
            ("a", NOW - timedelta(days=5), 200),
            ("a", NOW - timedelta(days=40), 200),
        ],
    )
    history = build_detail(settings, "a", now=NOW)["history"]
    assert [h["date"] for h in history] == ["2026-10-10", "2026-10-09", "2026-10-05"]
    assert history[1] == {
        "date": "2026-10-09",
        "outcome": "responded",
        "status": 500,
        "elapsed_ms": 100,
    }


def test_history_excludes_other_apis_rows(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b"])
    _append(settings, [("a", NOW, 200), ("b", NOW - timedelta(days=1), 503), ("b", NOW, 503)])
    history = build_detail(settings, "a", now=NOW)["history"]
    assert history == [
        {"date": "2026-10-10", "outcome": "responded", "status": 200, "elapsed_ms": 100}
    ]


def test_inactive_api_is_still_served_with_active_false(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b"])
    _seed(settings, ["b"])
    response = _client(settings).get("/v1/apis/a")
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["name"] == "A"
    assert body["base_url"] == "https://a.example.com"


def test_payload_keys_are_the_public_contract(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a"])
    assert set(_client(settings).get("/v1/apis/a").json()) == {
        "id",
        "name",
        "description",
        "tags",
        "base_url",
        "spec_url",
        "active",
        "probe_target",
        "reachable_30d",
        "working_30d",
        "days_observed_30d",
        "latency",
        "last_probe",
        "history",
    }

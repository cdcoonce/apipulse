from datetime import UTC, datetime

from apipulse.api import build_detail, detail_payload
from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import Outcome, ProbeResult
from apipulse.rollup import Rollup, empty_rollup
from apipulse.store import (
    SearchHit,
    append_probes,
    connect,
    load_api,
    load_rollup,
    read_probes,
    upsert_apis,
    upsert_rollups,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
REC = ApiRecord("a", "A", "about a", ("t1",), "https://a.example.com", None, "apiKey", None, "seed")


def _seed(settings: Settings) -> None:
    conn = connect(settings)
    try:
        upsert_apis(conn, [REC], seen_at=NOW)
        upsert_rollups(
            conn,
            [Rollup("a", 1, 1, 1, 90, 90, NOW, "responded", 200, "https://a.example.com/x")],
            computed_at=NOW,
        )
    finally:
        conn.close()
    result = ProbeResult(
        "https://a.example.com/x", NOW, Outcome.responded, 200, 90, True, None, {}, None
    )
    append_probes(settings, [("a", result)], now=NOW)


def test_detail_payload_is_the_pure_core_of_build_detail(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings)
    conn = connect(settings)
    try:
        record, active = load_api(conn, "a")
        rollup = load_rollup(conn, "a") or empty_rollup("a")
    finally:
        conn.close()
    probes = read_probes(settings, since=datetime(2026, 9, 1, tzinfo=UTC))
    assert detail_payload(record, active, rollup, probes) == build_detail(settings, "a", now=NOW)


def test_search_summary_has_exactly_the_v1_summary_keys():
    from apipulse.api import search_summary

    hit = SearchHit("a", "A", 3, 2, 10, NOW, True, 0.0, "keyless")
    assert search_summary(hit) == {
        "id": "a",
        "name": "A",
        "reachable_30d": 3,
        "working_30d": 2,
        "p50_ms": 10,
        "last_probe_at": "2026-10-10T12:00:00Z",
        "active": True,
        "auth": "keyless",
    }

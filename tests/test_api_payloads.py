from datetime import UTC, datetime

import pytest

from apipulse.api import build_detail, detail_payload, search_summary
from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import Outcome, ProbeResult
from apipulse.rollup import Rollup, empty_rollup
from apipulse.store import (
    SearchHit,
    append_probes,
    connect,
    fts_available,
    load_api,
    load_rollup,
    read_probes,
    search_apis,
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
        "days_observed_30d": 0,
        "probe_depth": None,
    }


def test_search_summary_carries_days_observed_and_probe_depth():
    from apipulse.api import search_summary

    hit = SearchHit("a", "A", 3, 2, 10, NOW, True, 0.0, "keyless", 9, "endpoint")
    summary = search_summary(hit)
    assert summary["days_observed_30d"] == 9
    assert summary["probe_depth"] == "endpoint"


def test_detail_payload_adds_exactly_probe_depth_to_the_pre_existing_keys():
    rollup = Rollup("a", 1, 1, 1, 90, 90, NOW, "responded", 200, "https://a.example.com")
    payload = detail_payload(REC, True, rollup, [])
    assert payload["probe_depth"] == "site"
    assert set(payload) == {
        "id", "name", "description", "tags", "base_url", "spec_url", "active", "probe_target",
        "reachable_30d", "working_30d", "days_observed_30d", "probe_depth", "latency",
        "last_probe", "history", "auth", "ratelimit",
    }  # fmt: skip


def test_detail_payload_for_an_empty_rollup_has_no_probe_depth():
    assert detail_payload(REC, True, empty_rollup("a"), [])["probe_depth"] is None


def _rec_b(id: str, description: str = "") -> ApiRecord:
    return ApiRecord(id, id, description, (), f"https://{id}.example.com", None, None, None, "seed")


def _search_seed(settings: Settings) -> None:
    conn = connect(settings)
    try:
        upsert_apis(
            conn,
            [_rec_b("site", "zebra"), _rec_b("deep", "zebra"), _rec_b("cold", "zebra")],
            seen_at=NOW,
        )
        upsert_rollups(
            conn,
            [
                # days_observed_30d (11) differs from working_30d (4) and reachable_30d (7).
                Rollup("site", 7, 4, 11, 50, 50, NOW, "responded", 200, "https://site.example.com"),
                Rollup(
                    "deep", 2, 2, 3, 70, 70, NOW, "responded", 200, "https://deep.example.com/v1"
                ),
            ],
            computed_at=NOW,
        )
    finally:
        conn.close()


@pytest.mark.parametrize("use_fts", [True, False])
def test_both_search_paths_fill_days_observed_and_probe_depth(tmp_path, use_fts):
    settings = Settings(data_dir=tmp_path)
    _search_seed(settings)
    conn = connect(settings)
    try:
        if use_fts and not fts_available(conn):
            pytest.skip("sqlite lacks FTS5")
        hits = {h.id: h for h in search_apis(conn, "zebra", limit=10, use_fts=use_fts)}
    finally:
        conn.close()
    assert set(hits) == {"site", "deep", "cold"}
    site = search_summary(hits["site"])
    assert (site["reachable_30d"], site["working_30d"], site["days_observed_30d"]) == (7, 4, 11)
    assert site["probe_depth"] == "site"
    assert (hits["deep"].days_observed_30d, hits["deep"].probe_depth) == (3, "endpoint")
    assert (hits["cold"].days_observed_30d, hits["cold"].probe_depth) == (0, None)


@pytest.mark.parametrize("use_fts", [True, False])
def test_search_ranking_ignores_the_new_fields(tmp_path, use_fts):
    settings = Settings(data_dir=tmp_path)
    _search_seed(settings)
    conn = connect(settings)
    try:
        if use_fts and not fts_available(conn):
            pytest.skip("sqlite lacks FTS5")
        order = [h.id for h in search_apis(conn, "zebra", limit=10, use_fts=use_fts)]
    finally:
        conn.close()
    assert order == ["site", "deep", "cold"]

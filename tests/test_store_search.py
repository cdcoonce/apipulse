from datetime import UTC, datetime

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.rollup import Rollup
from apipulse.store import connect, search_apis, upsert_apis, upsert_rollups

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _rec(id: str, name: str, description: str, tags: tuple[str, ...]) -> ApiRecord:
    return ApiRecord(
        id=id,
        name=name,
        description=description,
        tags=tags,
        base_url=f"https://{id}.example.com",
        spec_url=None,
        declared_auth=None,
        signup_url=None,
        source="seed",
    )


def _roll(api_id: str, reachable: int, working: int, p50: int | None) -> Rollup:
    return Rollup(
        api_id=api_id,
        reachable_30d=reachable,
        working_30d=working,
        days_observed_30d=30,
        p50_ms=p50,
        p95_ms=None,
        last_probe_at=NOW,
        last_outcome="responded",
        last_status=200,
        probe_target=None,
    )


SEED = [
    _rec("openweathermap", "openweathermap", "Weather forecasts", ("weather",)),
    _rec("weatherbit", "weatherbit", "Weather data", ("weather",)),
    _rec("geocodio", "geocodio", "Geocoding addresses", ("geo",)),
    _rec("catfact", "catfact", "Cat facts", ("animals",)),
    _rec("nws", "nws", "National Weather Service", ("weather", "government")),
    _rec("deadapi", "deadapi", "Weather archive", ("weather",)),
]


def _seeded(tmp_path):
    conn = connect(Settings(data_dir=tmp_path))
    upsert_apis(conn, SEED, seen_at=NOW)
    upsert_rollups(
        conn,
        [
            _roll("openweathermap", 30, 29, 100),
            _roll("weatherbit", 25, 20, 150),
            _roll("geocodio", 28, 28, 90),
            _roll("catfact", 10, 10, 50),
            _roll("nws", 30, 30, 200),
        ],
        computed_at=NOW,
    )
    return conn


def test_search_matches_name_description_tags_and_prefix(tmp_path):
    conn = _seeded(tmp_path)
    weather = {h.id for h in search_apis(conn, "weather", limit=20)}
    assert weather == {"openweathermap", "weatherbit", "nws", "deadapi"}
    assert [h.id for h in search_apis(conn, "geocod", limit=20)] == ["geocodio"]


def _tied(tmp_path, rollups):
    conn = connect(Settings(data_dir=tmp_path))
    ids = ["t1", "t2", "t3", "t4"]
    upsert_apis(
        conn, [_rec(i, "forecast", "Weather forecasts", ("weather",)) for i in ids], seen_at=NOW
    )
    upsert_rollups(conn, rollups, computed_at=NOW)
    return conn


def test_ties_break_on_reachable_then_working_then_p50_with_none_last(tmp_path):
    conn = _tied(
        tmp_path,
        [
            _roll("t1", 10, 5, None),
            _roll("t2", 20, 1, 500),
            _roll("t3", 10, 5, 100),
            _roll("t4", 10, 9, 900),
        ],
    )
    ids = [h.id for h in search_apis(conn, "weather", limit=20)]
    assert ids == ["t2", "t4", "t3", "t1"]


def test_api_without_rollup_has_zeros_and_sorts_last(tmp_path):
    conn = _tied(tmp_path, [_roll("t1", 1, 1, 10), _roll("t2", 2, 2, 20), _roll("t3", 3, 3, 30)])
    hits = search_apis(conn, "weather", limit=20)
    assert [h.id for h in hits] == ["t3", "t2", "t1", "t4"]
    assert (hits[-1].reachable_30d, hits[-1].working_30d) == (0, 0)
    assert (hits[-1].p50_ms, hits[-1].last_probe_at) == (None, None)


def test_limit_tokenizer_and_all_tokens_required(tmp_path):
    from apipulse.store import tokenize_query

    conn = _seeded(tmp_path)
    assert len(search_apis(conn, "weather", limit=2)) == 2
    assert tokenize_query("  Weather   FORE ") == ["weather", "fore"]
    assert search_apis(conn, "weather cat", limit=20) == []
    assert [h.id for h in search_apis(conn, "weather national", limit=20)] == ["nws"]
    assert search_apis(conn, "   ", limit=20) == []


def test_deterministic_and_deactivated_still_searchable(tmp_path):
    conn = _seeded(tmp_path)
    assert search_apis(conn, "weather", limit=20) == search_apis(conn, "weather", limit=20)
    upsert_apis(conn, [r for r in SEED if r.id != "nws"], seen_at=NOW)
    nws = [h for h in search_apis(conn, "national", limit=20)]
    assert [(h.id, h.active) for h in nws] == [("nws", False)]


def test_fallback_matches_fts_id_sets(tmp_path):
    conn = _seeded(tmp_path)
    for q in ("weather", "geocod", "weather national", "weather cat", "Cat"):
        fts = {h.id for h in search_apis(conn, q, limit=20, use_fts=True)}
        plain = {h.id for h in search_apis(conn, q, limit=20, use_fts=False)}
        assert plain == fts, q


def test_fallback_honors_tie_break_order_and_limit(tmp_path):
    conn = _tied(
        tmp_path,
        [
            _roll("t1", 10, 5, None),
            _roll("t2", 20, 1, 500),
            _roll("t3", 10, 5, 100),
            _roll("t4", 10, 9, 900),
        ],
    )
    hits = search_apis(conn, "weather", limit=20, use_fts=False)
    assert [h.id for h in hits] == ["t2", "t4", "t3", "t1"]
    assert len(search_apis(conn, "weather", limit=2, use_fts=False)) == 2
    assert search_apis(conn, "  ", limit=5, use_fts=False) == []


def test_existing_database_is_backfilled_into_fts_on_connect(tmp_path):
    conn = _seeded(tmp_path)
    conn.execute("DROP TABLE apis_fts")
    conn.commit()
    conn.close()
    reopened = connect(Settings(data_dir=tmp_path))
    assert {h.id for h in search_apis(reopened, "weather", limit=20)} == {
        "openweathermap",
        "weatherbit",
        "nws",
        "deadapi",
    }

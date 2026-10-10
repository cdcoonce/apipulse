from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pyarrow.parquet as pq

from apipulse import runner
from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.runner import RunSummary, format_summary, run_probes
from apipulse.store import connect, load_rollup, upsert_apis

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


def _seed(settings: Settings, ids: list[str]) -> None:
    conn = connect(settings)
    try:
        upsert_apis(conn, [_rec(i) for i in ids], seen_at=NOW)
    finally:
        conn.close()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ids(summary: RunSummary) -> list[str]:
    return sorted(pq.read_table(summary.parquet_path)["api_id"].to_pylist())


def test_run_probes_probes_every_active_api_and_archives_rows(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b", "c"])
    client = _client(lambda request: httpx.Response(200))
    summary = run_probes(settings, client=client, now=NOW)
    assert (summary.probed, summary.responded, summary.working) == (3, 3, 3)
    assert summary.by_outcome == {"responded": 3}
    assert summary.errors == 0
    assert _ids(summary) == ["a", "b", "c"]


def test_run_probes_records_a_timeout_and_keeps_going(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b", "c"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "b.example.com":
            raise httpx.ConnectTimeout("slow", request=request)
        return httpx.Response(200)

    summary = run_probes(settings, client=_client(handler), now=NOW)
    assert (summary.probed, summary.responded, summary.working) == (3, 2, 2)
    assert summary.by_outcome == {"responded": 2, "timeout": 1}
    assert summary.errors == 0
    rows = {r["api_id"]: r["outcome"] for r in pq.read_table(summary.parquet_path).to_pylist()}
    assert rows == {"a": "responded", "b": "timeout", "c": "responded"}


def test_run_probes_skips_inactive_apis(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b", "c"])
    _seed(settings, ["a", "b"])
    summary = run_probes(settings, client=_client(lambda r: httpx.Response(200)), now=NOW)
    assert summary.probed == 2
    assert _ids(summary) == ["a", "b"]


def test_run_probes_with_empty_inventory_writes_valid_file(tmp_path):
    settings = Settings(data_dir=tmp_path)
    summary = run_probes(settings, client=_client(lambda r: httpx.Response(200)), now=NOW)
    assert summary.probed == 0
    assert summary.by_outcome == {}
    assert pq.read_table(summary.parquet_path).num_rows == 0


def _summary(by_outcome: dict[str, int], **kw) -> RunSummary:
    base = dict(
        probed=sum(by_outcome.values()),
        responded=by_outcome.get("responded", 0),
        working=by_outcome.get("responded", 0),
        by_outcome=by_outcome,
        errors=0,
        wall_ms=4312,
        parquet_path=Path("data/probes/2026-10.parquet"),
        rollups=3,
    )
    return RunSummary(**{**base, **kw})


def test_format_summary_lists_non_responded_outcomes_in_enum_order():
    text = format_summary(_summary({"responded": 5, "dns_failure": 2, "timeout": 1}))
    assert text == (
        "probe: 8 probed, 5 responded, 5 working, timeout=1, dns_failure=2, "
        "0 errors, 4312 ms -> data/probes/2026-10.parquet, rollups: 3"
    )


def test_format_summary_omits_outcomes_segment_when_all_responded():
    assert format_summary(_summary({"responded": 7})) == (
        "probe: 7 probed, 7 responded, 7 working, 0 errors, 4312 ms -> "
        "data/probes/2026-10.parquet, rollups: 3"
    )


def test_format_summary_for_empty_run():
    text = format_summary(_summary({}))
    assert text.startswith("probe: 0 probed, 0 responded, 0 working, 0 errors, 4312 ms -> ")


def test_run_probes_counts_a_raising_probe_as_other_error(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b", "c"])
    real = runner.probe

    def flaky(url, **kwargs):
        if "b.example.com" in url:
            raise RuntimeError("boom")
        return real(url, **kwargs)

    monkeypatch.setattr("apipulse.runner.probe", flaky)
    summary = run_probes(settings, client=_client(lambda r: httpx.Response(200)), now=NOW)
    assert summary.errors == 1
    assert summary.by_outcome == {"responded": 2, "other_error": 1}
    rows = {r["api_id"]: r for r in pq.read_table(summary.parquet_path).to_pylist()}
    assert set(rows) == {"a", "b", "c"}
    assert rows["b"]["outcome"] == "other_error"
    assert rows["b"]["error"] == "boom"
    assert rows["b"]["probed_at"] == NOW


def test_run_probes_upserts_a_rollup_for_every_active_api(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b", "c"])
    summary = run_probes(settings, client=_client(lambda r: httpx.Response(200)), now=NOW)
    assert summary.rollups == 3
    conn = connect(settings)
    try:
        a = load_rollup(conn, "a")
    finally:
        conn.close()
    assert a is not None
    assert (a.reachable_30d, a.working_30d, a.days_observed_30d) == (1, 1, 1)
    assert a.probe_target == "https://a.example.com"


def test_run_probes_does_not_recompute_inactive_apis(tmp_path):
    settings = Settings(data_dir=tmp_path)
    _seed(settings, ["a", "b"])
    client = _client(lambda r: httpx.Response(200))
    run_probes(settings, client=client, now=NOW)
    _seed(settings, ["a"])
    later = NOW + timedelta(days=1)
    summary = run_probes(settings, client=client, now=later)
    assert summary.rollups == 1
    conn = connect(settings)
    try:
        a, b = load_rollup(conn, "a"), load_rollup(conn, "b")
    finally:
        conn.close()
    assert a is not None and a.days_observed_30d == 2
    assert b is not None and b.days_observed_30d == 1

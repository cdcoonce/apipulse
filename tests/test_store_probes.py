import json
from datetime import UTC, datetime

import pyarrow.parquet as pq

from apipulse.config import Settings
from apipulse.prober import Outcome, ProbeResult
from apipulse.store import PROBES_SCHEMA, append_probes, read_probes

NOW = datetime(2026, 10, 10, tzinfo=UTC)
COLUMNS = [
    "api_id",
    "url",
    "probed_at",
    "outcome",
    "status",
    "elapsed_ms",
    "tls_ok",
    "final_url",
    "ratelimit_headers_json",
    "error",
]


def _ok(url: str, at: datetime = NOW, headers: dict[str, str] | None = None) -> ProbeResult:
    return ProbeResult(
        url=url,
        probed_at=at,
        outcome=Outcome.responded,
        status=200,
        elapsed_ms=12,
        tls_ok=True,
        final_url=url,
        ratelimit_headers=headers or {},
        error=None,
    )


def test_append_probes_writes_monthly_parquet_with_expected_columns(tmp_path):
    settings = Settings(data_dir=tmp_path)
    results = [("a", _ok("https://a.example.com")), ("b", _ok("https://b.example.com"))]
    path = append_probes(settings, results, now=NOW)
    assert path == tmp_path / "probes" / "2026-10.parquet"
    table = pq.read_table(path)
    assert table.num_rows == 2
    assert table.column_names == COLUMNS


def test_append_probes_appends_to_existing_file_atomically(tmp_path):
    settings = Settings(data_dir=tmp_path)
    append_probes(settings, [("a", _ok("https://a.example.com"))] * 2, now=NOW)
    path = append_probes(settings, [("b", _ok("https://b.example.com"))] * 2, now=NOW)
    table = pq.read_table(path)
    assert table.num_rows == 4
    assert table.schema.equals(PROBES_SCHEMA)
    assert list(path.parent.glob("*.tmp")) == []


def test_read_probes_round_trips_nulls_and_headers(tmp_path):
    settings = Settings(data_dir=tmp_path)
    failure = ProbeResult(
        url="https://f.example.com",
        probed_at=NOW,
        outcome=Outcome.dns_failure,
        status=None,
        elapsed_ms=None,
        tls_ok=None,
        final_url=None,
        ratelimit_headers={},
        error="no such host",
    )
    limited = _ok("https://r.example.com", headers={"x-ratelimit-limit": "60"})
    append_probes(settings, [("f", failure), ("r", limited)], now=NOW)
    rows = {r.api_id: r for r in read_probes(settings, since=datetime(2026, 1, 1, tzinfo=UTC))}
    f, r = rows["f"], rows["r"]
    assert (f.outcome, f.status, f.elapsed_ms, f.tls_ok, f.final_url) == (
        "dns_failure",
        None,
        None,
        None,
        None,
    )
    assert f.error == "no such host"
    assert json.loads(r.ratelimit_headers_json) == {"x-ratelimit-limit": "60"}
    assert r.status == 200
    assert r.probed_at.tzinfo is not None
    assert r.probed_at.utcoffset().total_seconds() == 0


def test_read_probes_filters_by_since_across_months_ascending(tmp_path):
    settings = Settings(data_dir=tmp_path)
    days = [(2026, 10, 10), (2026, 9, 30), (2026, 10, 5), (2026, 10, 4)]
    for y, m, d in days:
        at = datetime(y, m, d, tzinfo=UTC)
        append_probes(settings, [(f"{m}-{d}", _ok("https://x.example.com", at))], now=at)
    rows = read_probes(settings, since=datetime(2026, 10, 5, tzinfo=UTC))
    assert [r.api_id for r in rows] == ["10-5", "10-10"]


def test_read_probes_without_probes_dir_returns_empty(tmp_path):
    assert read_probes(Settings(data_dir=tmp_path), since=datetime(2026, 1, 1, tzinfo=UTC)) == []

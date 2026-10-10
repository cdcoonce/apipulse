import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from apipulse.commands.refresh_inventory import run
from apipulse.config import Settings
from apipulse.store import connect

FIXTURE = Path(__file__).parent / "fixtures" / "apis_guru_list.json"


def _entry(id: str, base_url: str) -> dict:
    return {"id": id, "name": id, "base_url": base_url}


def _setup(tmp_path: Path) -> Settings:
    (tmp_path / "seed.json").write_text(
        json.dumps(
            [
                _entry("one", "https://one.example.com"),
                _entry("one-dup", "https://www.one.example.com/v2"),
                _entry("two", "https://two.example.com"),
            ]
        )
    )
    (tmp_path / "optout.json").write_text(json.dumps(["two.example.com"]))
    return Settings(data_dir=tmp_path)


def test_run_reports_summary_and_populates_db(tmp_path):
    settings = _setup(tmp_path)
    assert (
        run(settings, source="seed")
        == "inventory: seed=3 -> 1 active, 1 removed (opt-out), 0 deactivated"
    )
    conn = connect(settings)
    assert [r["id"] for r in conn.execute("SELECT id FROM apis WHERE active = 1")] == ["one"]
    assert [r["key"] for r in conn.execute("SELECT key FROM optouts")] == ["two"]


def test_run_twice_keeps_first_seen_and_deactivates_nothing(tmp_path):
    settings = _setup(tmp_path)
    t1 = datetime(2026, 10, 1, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, tzinfo=UTC)
    run(settings, source="seed", now=t1)
    assert (
        run(settings, source="seed", now=t2)
        == "inventory: seed=3 -> 1 active, 1 removed (opt-out), 0 deactivated"
    )
    row = connect(settings).execute("SELECT * FROM apis WHERE id = 'one'").fetchone()
    assert row["first_seen_at"] == t1.isoformat()
    assert row["last_seen_at"] == t2.isoformat()


def test_run_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError, match="unknown source: nope"):
        run(_setup(tmp_path), source="nope")


def _guru_client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=json.loads(FIXTURE.read_text()))
        )
    )


def _setup_httpbin(tmp_path: Path) -> Settings:
    (tmp_path / "seed.json").write_text(
        json.dumps([{"id": "httpbin.org", "name": "httpbin", "base_url": "https://httpbin.org"}])
    )
    (tmp_path / "optout.json").write_text("[]")
    return Settings(data_dir=tmp_path)


def _sources(settings: Settings) -> dict[str, str]:
    conn = connect(settings)
    try:
        return {row["id"]: row["source"] for row in conn.execute("SELECT id, source FROM apis")}
    finally:
        conn.close()


def test_seed_then_apis_guru_seed_wins_host_collision(tmp_path):
    settings = _setup_httpbin(tmp_path)
    line = run(settings, source="seed,apis.guru", client=_guru_client())
    assert line == "inventory: seed=1 apis.guru=6 -> 6 active, 0 removed (opt-out), 0 deactivated"
    sources = _sources(settings)
    assert sources["httpbin.org"] == "seed"
    assert sources["github.com"] == "apis.guru"


def test_source_order_decides_host_collision(tmp_path):
    settings = _setup_httpbin(tmp_path)
    run(settings, source="apis.guru,seed", client=_guru_client())
    assert _sources(settings)["httpbin.org"] == "apis.guru"


def test_second_run_keeps_first_seen_and_deactivates_nothing_multi_source(tmp_path):
    settings = _setup_httpbin(tmp_path)
    t1 = datetime(2026, 10, 1, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, tzinfo=UTC)
    run(settings, client=_guru_client(), now=t1)
    line = run(settings, client=_guru_client(), now=t2)
    assert line.endswith("0 deactivated")
    conn = connect(settings)
    row = conn.execute("SELECT first_seen_at FROM apis WHERE id = 'github.com'").fetchone()
    assert row["first_seen_at"] == t1.isoformat()


def test_seed_only_and_invalid_source_lists(tmp_path):
    settings = _setup_httpbin(tmp_path)
    assert run(settings, source="seed") == (
        "inventory: seed=1 -> 1 active, 0 removed (opt-out), 0 deactivated"
    )
    for bad in ("seed,,apis.guru", "nope", ""):
        with pytest.raises(ValueError):
            run(settings, source=bad, client=_guru_client())

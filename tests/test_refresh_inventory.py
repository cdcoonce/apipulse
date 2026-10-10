import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apipulse.commands.refresh_inventory import run
from apipulse.config import Settings
from apipulse.store import connect


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
    assert run(settings) == "inventory: 1 active, 1 removed (opt-out), 0 deactivated"
    conn = connect(settings)
    assert [r["id"] for r in conn.execute("SELECT id FROM apis WHERE active = 1")] == ["one"]
    assert [r["key"] for r in conn.execute("SELECT key FROM optouts")] == ["two"]


def test_run_twice_keeps_first_seen_and_deactivates_nothing(tmp_path):
    settings = _setup(tmp_path)
    t1 = datetime(2026, 10, 1, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, tzinfo=UTC)
    run(settings, now=t1)
    assert run(settings, now=t2) == "inventory: 1 active, 1 removed (opt-out), 0 deactivated"
    row = connect(settings).execute("SELECT * FROM apis WHERE id = 'one'").fetchone()
    assert row["first_seen_at"] == t1.isoformat()
    assert row["last_seen_at"] == t2.isoformat()


def test_run_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError, match="unknown source: nope"):
        run(_setup(tmp_path), source="nope")

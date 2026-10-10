from pathlib import Path

from apipulse.config import Settings, resolve_settings


def test_flag_wins_over_env(monkeypatch):
    monkeypatch.setenv("APIPULSE_DATA_DIR", "/tmp/e")
    assert resolve_settings(Path("x")) == Settings(data_dir=Path("x"))


def test_env_used_when_no_flag(monkeypatch):
    monkeypatch.setenv("APIPULSE_DATA_DIR", "/tmp/e")
    assert resolve_settings().data_dir == Path("/tmp/e")


def test_default_is_data_when_nothing_set(monkeypatch):
    monkeypatch.delenv("APIPULSE_DATA_DIR", raising=False)
    assert resolve_settings().data_dir == Path("data")

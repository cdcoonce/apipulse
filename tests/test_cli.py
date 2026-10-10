import json
from datetime import UTC, datetime

import pytest

from apipulse.cli import main
from apipulse.prober import Outcome, ProbeResult


def test_version_flag_prints_version_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "apipulse 0.1.0" in capsys.readouterr().out


def test_help_lists_all_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ("refresh-inventory", "probe", "render", "serve"):
        assert name in out


@pytest.mark.parametrize("command", ["probe", "render", "serve"])
def test_subcommand_is_a_stub_that_returns_zero(command, capsys):
    assert main([command]) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"{command}: not implemented"


def test_no_subcommand_prints_usage_and_returns_two(capsys):
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out


def test_data_dir_flag_reaches_handler(capsys, monkeypatch):
    monkeypatch.setenv("APIPULSE_DATA_DIR", "/tmp/e")
    assert main(["probe", "--data-dir", "z"]) == 0
    assert "data_dir: z" in capsys.readouterr().out.splitlines()


def test_env_data_dir_reaches_handler(capsys, monkeypatch):
    monkeypatch.setenv("APIPULSE_DATA_DIR", "/tmp/e")
    assert main(["probe"]) == 0
    assert "data_dir: /tmp/e" in capsys.readouterr().out.splitlines()


def test_probe_one_prints_json_and_returns_zero(capsys, monkeypatch):
    fake = ProbeResult(
        url="https://example.invalid",
        probed_at=datetime(2026, 10, 10, tzinfo=UTC),
        outcome=Outcome.dns_failure,
        status=None,
        elapsed_ms=None,
        tls_ok=None,
        final_url=None,
        ratelimit_headers={},
        error="no such host",
    )
    monkeypatch.setattr("apipulse.cli.probe", lambda url, **kwargs: fake)
    assert main(["probe-one", "https://example.invalid"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "dns_failure"


def test_refresh_inventory_runs_against_data_dir(tmp_path, capsys):
    (tmp_path / "seed.json").write_text(
        json.dumps([{"id": "a", "name": "A", "base_url": "https://a.example.com"}])
    )
    (tmp_path / "optout.json").write_text("[]")
    assert main(["refresh-inventory", "--data-dir", str(tmp_path)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "inventory: 1 active, 0 removed (opt-out), 0 deactivated"

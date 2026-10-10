import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
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


def test_serve_passes_host_port_and_settings_to_server(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("apipulse.cli._serve", lambda *args: calls.append(args))
    argv = ["serve", "--host", "0.0.0.0", "--port", "9090", "--data-dir", str(tmp_path)]
    assert main(argv) == 0
    [(settings, host, port)] = calls
    assert (settings.data_dir, host, port) == (tmp_path, "0.0.0.0", 9090)


def test_serve_defaults_to_loopback_8080(monkeypatch):
    calls = []
    monkeypatch.setattr("apipulse.cli._serve", lambda *args: calls.append(args))
    assert main(["serve"]) == 0
    assert [(host, port) for _, host, port in calls] == [("127.0.0.1", 8080)]


def test_no_subcommand_prints_usage_and_returns_two(capsys):
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out


def test_render_prints_summary_and_returns_zero(tmp_path, capsys):
    assert main(["render", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"render: 0 api pages, 0 removed, badges: 0 -> {tmp_path}/site"]


def test_data_dir_flag_wins_over_env(tmp_path, monkeypatch):
    env_dir, flag_dir = tmp_path / "env", tmp_path / "flag"
    monkeypatch.setenv("APIPULSE_DATA_DIR", str(env_dir))
    assert main(["render", "--data-dir", str(flag_dir)]) == 0
    assert (flag_dir / "apipulse.db").exists()
    assert not env_dir.exists()


def test_env_data_dir_is_used_without_flag(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    monkeypatch.setenv("APIPULSE_DATA_DIR", str(env_dir))
    assert main(["render"]) == 0
    assert (env_dir / "apipulse.db").exists()


def test_render_out_dir_flag(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["render", "--data-dir", str(tmp_path / "d"), "--out-dir", str(out)]) == 0
    assert capsys.readouterr().out.strip().endswith(f"-> {out}")
    assert (out / "badge").is_dir()


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


def _write_one_seed(tmp_path):
    (tmp_path / "seed.json").write_text(
        json.dumps([{"id": "a", "name": "A", "base_url": "https://a.example.com"}])
    )
    (tmp_path / "optout.json").write_text("[]")


def test_refresh_inventory_runs_against_data_dir(tmp_path, capsys):
    _write_one_seed(tmp_path)
    assert main(["refresh-inventory", "--source", "seed", "--data-dir", str(tmp_path)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "inventory: seed=1 -> 1 active, 0 removed (opt-out), 0 deactivated"


def test_refresh_inventory_defaults_to_all_three_sources(tmp_path, capsys, monkeypatch):
    _write_one_seed(tmp_path)
    fixtures = Path(__file__).parent / "fixtures"
    guru = json.loads((fixtures / "apis_guru_list.json").read_text())
    readme = (fixtures / "public_apis_readme.md").read_text()
    monkeypatch.setattr(
        "apipulse.commands.refresh_inventory.fetch_apis_guru", lambda client=None: guru
    )
    monkeypatch.setattr(
        "apipulse.commands.refresh_inventory.fetch_public_apis", lambda client=None: readme
    )
    assert main(["refresh-inventory", "--data-dir", str(tmp_path)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("inventory: seed=1 apis.guru=6 public-apis=6 ->")


def test_probe_runs_against_inventory_and_prints_summary(tmp_path, capsys, monkeypatch):
    (tmp_path / "seed.json").write_text(
        json.dumps(
            [
                {"id": "a", "name": "A", "base_url": "https://a.example.com"},
                {"id": "b", "name": "B", "base_url": "https://b.example.com"},
            ]
        )
    )
    (tmp_path / "optout.json").write_text("[]")
    assert main(["refresh-inventory", "--source", "seed", "--data-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    real_client = httpx.Client
    monkeypatch.setattr(
        "apipulse.runner.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    assert main(["probe", "--data-dir", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("probe: 2 probed, 2 responded, 2 working, 0 errors,")
    # Every probe returns 200, but one probe per API is fewer than the 3 statuses
    # classify_auth needs, so nothing is classified keyless yet.
    assert re.search(
        rf"-> {re.escape(str(tmp_path))}/probes/\d{{4}}-\d{{2}}\.parquet,"
        r" rollups: 2, keyless: 0, key-required: 0$",
        lines[0],
    )

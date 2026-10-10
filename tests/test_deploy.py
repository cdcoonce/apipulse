"""Deployment artifacts (#15): verify script, cron, systemd, launchd, runbook."""

import configparser
import os
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import Outcome, ProbeResult
from apipulse.rollup import Rollup
from apipulse.store import append_probes, connect, upsert_apis, upsert_rollups

ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "scripts" / "verify_run.sh"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _record(api_id: str) -> ApiRecord:
    return ApiRecord(
        id=api_id,
        name=api_id.upper(),
        description="",
        tags=(),
        base_url=f"https://{api_id}.example.com",
        spec_url=None,
        declared_auth=None,
        signup_url=None,
        source="seed",
    )


def _result() -> ProbeResult:
    return ProbeResult(
        url="https://x.example.com",
        probed_at=NOW,
        outcome=Outcome.responded,
        status=200,
        elapsed_ms=100,
        tls_ok=True,
        final_url=None,
        ratelimit_headers={},
        error=None,
    )


def _rollup(api_id: str) -> Rollup:
    return Rollup(
        api_id=api_id,
        reachable_30d=1,
        working_30d=1,
        days_observed_30d=1,
        p50_ms=100,
        p95_ms=100,
        last_probe_at=NOW,
        last_outcome="responded",
        last_status=200,
        probe_target="https://x.example.com",
    )


def _make_data_dir(data_dir: Path, *, probe_rows: int = 3, rollups: int = 2) -> Path:
    """A data dir shaped like a real run: monthly Parquet, db with rollups, rendered site.

    The inventory holds one more API than there are rollups (a newly added API not yet
    probed), so a script counting the wrong table cannot print the right number.
    """
    settings = Settings(data_dir=data_dir)
    ids = [f"api{i}" for i in range(rollups + 1)]
    conn = connect(settings)
    try:
        upsert_apis(conn, [_record(i) for i in ids], seen_at=NOW)
        upsert_rollups(conn, [_rollup(i) for i in ids[:rollups]], computed_at=NOW)
    finally:
        conn.close()
    parquet = append_probes(settings, [("api0", _result())] * probe_rows, now=NOW)
    (data_dir / "site").mkdir()
    (data_dir / "site" / "index.html").write_text("<html></html>")
    return parquet


def _verify(data_dir: Path, *, arg: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHON": sys.executable}
    env.pop("APIPULSE_DATA_DIR", None)
    if arg:
        cmd = ["bash", str(VERIFY), str(data_dir)]
    else:
        cmd = ["bash", str(VERIFY)]
        env["APIPULSE_DATA_DIR"] = str(data_dir)
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)


def test_verify_run_exits_zero_and_reports_counts_on_a_fresh_run(tmp_path):
    parquet = _make_data_dir(tmp_path, probe_rows=3, rollups=2)
    result = _verify(tmp_path)
    assert result.returncode == 0, result.stderr
    assert parquet.name in result.stdout
    assert "rows: 3" in result.stdout
    assert "rollups: 2" in result.stdout
    assert "site/index.html" in result.stdout


def _age(path: Path, hours: float) -> None:
    """Portable `touch -d`: set the mtime `hours` into the past."""
    stamp = datetime.now(UTC).timestamp() - hours * 3600
    os.utime(path, (stamp, stamp))


def test_verify_run_exits_one_naming_staleness_when_parquet_is_37_hours_old(tmp_path):
    parquet = _make_data_dir(tmp_path)
    _age(parquet, 37)
    result = _verify(tmp_path)
    assert result.returncode == 1
    assert "stale" in (result.stdout + result.stderr).lower()


def test_verify_run_exits_two_when_there_is_no_parquet(tmp_path):
    result = _verify(tmp_path)
    assert result.returncode == 2
    assert "no parquet" in (result.stdout + result.stderr).lower()


def test_verify_run_still_passes_at_35_hours(tmp_path):
    parquet = _make_data_dir(tmp_path)
    _age(parquet, 35)
    assert _verify(tmp_path).returncode == 0


def test_verify_run_judges_the_lexically_last_monthly_file_not_the_newest_mtime(tmp_path):
    october = _make_data_dir(tmp_path)
    september = append_probes(
        Settings(data_dir=tmp_path), [("api0", _result())], now=NOW - timedelta(days=30)
    )
    assert september.name < october.name
    _age(october, 37)  # newest month is stale ...
    # ... while the older month was touched just now and must not rescue the verdict.
    result = _verify(tmp_path)
    assert result.returncode == 1
    assert october.name in result.stdout


def test_verify_run_takes_the_data_dir_from_the_first_argument(tmp_path):
    _make_data_dir(tmp_path)
    assert _verify(tmp_path, arg=True).returncode == 0


def test_verify_run_argument_wins_over_the_environment(tmp_path):
    good = tmp_path / "good"
    empty = tmp_path / "empty"
    good.mkdir()
    empty.mkdir()
    parquet = _make_data_dir(good)
    env = {**os.environ, "PYTHON": sys.executable, "APIPULSE_DATA_DIR": str(empty)}
    result = subprocess.run(
        ["bash", str(VERIFY), str(good)], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert parquet.name in result.stdout


def test_verify_run_reports_a_missing_site_without_failing(tmp_path):
    _make_data_dir(tmp_path)
    (tmp_path / "site" / "index.html").unlink()
    result = _verify(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "site/index.html: missing" in result.stdout


def test_verify_run_reports_a_missing_db_and_does_not_create_one(tmp_path):
    parquet = _make_data_dir(tmp_path)
    (tmp_path / "apipulse.db").unlink()
    result = _verify(tmp_path)
    assert parquet.name in result.stdout
    assert "rollups: missing" in result.stdout
    assert not (tmp_path / "apipulse.db").exists()


CRON = ROOT / "deploy" / "cron" / "apipulse.cron"
CLONE = "/home/cdcoonce/src/apipulse"
UV = "/home/cdcoonce/.local/bin/uv"


def _cron_entries() -> dict[str, tuple[str, str]]:
    """Map job name -> (five schedule fields, command), keyed by the log file name."""
    entries = {}
    for line in CRON.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split(None, 5)
        command = fields[5]
        job = "probe" if " probe" in command else "refresh"
        entries[job] = (" ".join(fields[:5]), command)
    return entries


def test_cron_has_exactly_the_refresh_and_probe_entries_on_the_agreed_schedules():
    entries = _cron_entries()
    assert set(entries) == {"refresh", "probe"}
    assert entries["refresh"][0] == "30 0 * * 0"
    assert entries["probe"][0] == "0 1 * * *"
    non_comment = [
        line for line in CRON.read_text().splitlines() if line.strip() and line[0] != "#"
    ]
    assert len(non_comment) == 2


def test_cron_entries_share_one_flock_lock_with_an_hour_wait():
    locks = set()
    for _, command in _cron_entries().values():
        match = re.search(r"flock -w 3600 (\S+)", command)
        assert match, command
        locks.add(match.group(1))
    assert len(locks) == 1
    assert next(iter(locks)).startswith("/home/cdcoonce/")


def test_cron_entries_cd_to_the_clone_set_the_data_dir_and_call_uv_by_absolute_path():
    for job, (_, command) in _cron_entries().items():
        assert f"cd {CLONE} &&" in command, job
        assert f"APIPULSE_DATA_DIR={CLONE}/data" in command, job
        assert f"{UV} run --frozen apipulse" in command, job
        # no bare `uv`/`apipulse` invocation: cron's PATH is minimal
        assert not re.search(r"(?<![/\w.-])uv run", command), job


def test_cron_probe_runs_render_only_after_probe_inside_the_same_flock():
    command = _cron_entries()["probe"][1]
    assert command.count("flock") == 1
    match = re.search(r"flock -w 3600 \S+ /bin/sh -c '([^']*)'", command)
    assert match, command
    inner = match.group(1)
    assert "apipulse probe && " in inner
    assert inner.index("apipulse probe") < inner.index("apipulse render")
    assert inner.count("&&") == 2  # cd && probe && render: no `;` or `||` that skips the gate


def test_cron_appends_stdout_and_stderr_to_a_per_job_log_in_the_state_dir():
    state = "/home/cdcoonce/.local/state/apipulse"
    for job, (_, command) in _cron_entries().items():
        assert f"flock -w 3600 {state}/run.lock /bin/sh -c '" in command, job
        assert f"' >> {state}/{job}.log 2>&1 ||" in command, job


def test_cron_lines_leave_a_timestamped_trace_and_log_a_failure_in_their_own_log():
    state = "/home/cdcoonce/.local/state/apipulse"
    for job, (_, command) in _cron_entries().items():
        log = f"{state}/{job}.log"
        assert command.startswith(f"date -Is >> {log}; APIPULSE_DATA_DIR="), job
        assert command.rstrip().endswith(f'|| echo "apipulse {job} failed: exit $?" >> {log}'), job


def test_cron_file_has_no_percent_signs_and_explains_the_early_schedule():
    text = CRON.read_text()
    assert "%" not in text
    assert "04:30" in text
    assert "reboot" in text.lower()


SERVICE = ROOT / "deploy" / "systemd" / "apipulse-serve.service"


def test_systemd_user_unit_keeps_serve_running_on_loopback():
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keep directive case
    parser.read_string(SERVICE.read_text())
    service = parser["Service"]
    assert service["ExecStart"] == (
        f"{UV} run --frozen apipulse serve --host 127.0.0.1 --port 8080"
    )
    assert service["WorkingDirectory"] == CLONE
    assert service["Environment"] == f"APIPULSE_DATA_DIR={CLONE}/data"
    assert service["Restart"] == "always"
    assert service["RestartSec"] == "10"
    assert parser["Install"]["WantedBy"] == "default.target"


LAUNCHD = ROOT / "deploy" / "launchd"
PLISTS = ("probe", "refresh", "serve")


def _plist_path(name: str) -> Path:
    return LAUNCHD / f"com.cdcoonce.apipulse.{name}.plist"


def _plist(name: str) -> dict:
    with _plist_path(name).open("rb") as handle:
        return plistlib.load(handle)


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS-only")
@pytest.mark.parametrize("name", PLISTS)
def test_plist_passes_plutil_lint(name):
    result = subprocess.run(
        ["plutil", "-lint", str(_plist_path(name))], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", PLISTS)
def test_plist_label_matches_its_file_name_and_data_dir_is_the_clones_data(name):
    data = _plist(name)
    assert data["Label"] == _plist_path(name).stem
    assert data["EnvironmentVariables"] == {
        "APIPULSE_DATA_DIR": "/Users/cdcoonce/src/apipulse/data"
    }


UV_MAC = "/Users/cdcoonce/.local/bin/uv"
CLONE_MAC = "/Users/cdcoonce/src/apipulse"
LOGS_MAC = "/Users/cdcoonce/Library/Logs/apipulse"


def test_probe_plist_runs_probe_then_render_at_01_00_daily_and_logs_to_library_logs():
    data = _plist("probe")
    assert data["ProgramArguments"][:2] == ["/bin/sh", "-c"]
    assert len(data["ProgramArguments"]) == 3
    command = data["ProgramArguments"][2]
    assert command == (
        f"cd {CLONE_MAC} && {UV_MAC} run --frozen apipulse probe"
        f" && {UV_MAC} run --frozen apipulse render"
    )
    assert data["StartCalendarInterval"] == {"Hour": 1, "Minute": 0}
    assert data["StandardOutPath"] == f"{LOGS_MAC}/probe.log"
    assert data["StandardErrorPath"] == f"{LOGS_MAC}/probe.log"


def test_refresh_plist_runs_refresh_inventory_on_sunday_00_30_and_logs_to_library_logs():
    data = _plist("refresh")
    assert data["ProgramArguments"][:2] == ["/bin/sh", "-c"]
    assert data["ProgramArguments"][2] == (
        f"cd {CLONE_MAC} && {UV_MAC} run --frozen apipulse refresh-inventory"
    )
    assert data["StartCalendarInterval"] == {"Weekday": 0, "Hour": 0, "Minute": 30}
    assert data["StandardOutPath"] == f"{LOGS_MAC}/refresh.log"
    assert data["StandardErrorPath"] == f"{LOGS_MAC}/refresh.log"


def test_serve_plist_keeps_serve_alive_on_loopback():
    data = _plist("serve")
    assert data["ProgramArguments"] == [
        UV_MAC,
        "run",
        "--frozen",
        "apipulse",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
    ]
    assert data["WorkingDirectory"] == CLONE_MAC
    assert data["KeepAlive"] is True
    assert data["RunAtLoad"] is True
    assert data["StandardOutPath"] == f"{LOGS_MAC}/serve.log"
    assert data["StandardErrorPath"] == f"{LOGS_MAC}/serve.log"


def test_no_deployment_file_references_an_apipulse_subcommand_beyond_the_four():
    allowed = {"probe", "render", "refresh-inventory", "serve"}
    texts = {
        path.name: " ".join(_plist(path.stem.rsplit(".", 1)[1])["ProgramArguments"])
        for path in LAUNCHD.glob("*.plist")
    }
    assert len(texts) == 3
    texts[CRON.name] = CRON.read_text()
    texts[SERVICE.name] = SERVICE.read_text()
    for name, text in texts.items():
        subcommands = re.findall(r"--frozen apipulse ([a-z][a-z-]*)", text)
        assert subcommands, name
        assert set(subcommands) <= allowed, (name, subcommands)


RUNBOOK = ROOT / "deploy" / "README.md"


@pytest.mark.parametrize(
    "needle",
    [
        "apipulse refresh-inventory",
        "apipulse probe",
        "apipulse render",
        "apipulse serve",
        "/home/cdcoonce/src/apipulse",
        "APIPULSE_DATA_DIR",
        "crontab -l",
        "enable-linger",
        "deploy-",
        "scripts/verify_run.sh",
        "04:30",
        "reboot",
        "192.168.0.103",
        "Ubuntu",
        "launchd",
    ],
)
def test_runbook_mentions(needle):
    assert needle in RUNBOOK.read_text()


def test_verify_script_is_executable():
    assert os.access(VERIFY, os.X_OK)


def test_verify_run_accepts_a_relative_data_dir_argument(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    parquet = _make_data_dir(data, rollups=2)
    env = {**os.environ, "PYTHON": sys.executable}
    env.pop("APIPULSE_DATA_DIR", None)
    result = subprocess.run(
        ["bash", str(VERIFY), "data"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert parquet.name in result.stdout
    assert "rollups: 2" in result.stdout


def test_verify_run_exits_four_not_one_on_an_unexpected_error(tmp_path):
    parquet = _make_data_dir(tmp_path)
    parquet.write_bytes(b"not a parquet file")  # fresh mtime, unreadable content
    result = _verify(tmp_path)
    assert result.returncode == 4
    assert "error" in result.stderr.lower()
    assert "stale" not in result.stderr.lower()


def test_verify_run_exits_three_when_the_site_is_two_hours_older_than_the_probe(tmp_path):
    parquet = _make_data_dir(tmp_path)
    _age(tmp_path / "site" / "index.html", 2)  # parquet is fresh, render did not follow it
    result = _verify(tmp_path)
    assert result.returncode == 3
    assert "RENDER STALE: site/index.html is older than the latest probe" in result.stderr
    assert parquet.name in result.stdout


def test_verify_run_tolerates_a_site_30_minutes_older_than_the_probe(tmp_path):
    _make_data_dir(tmp_path)
    _age(tmp_path / "site" / "index.html", 0.5)
    assert _verify(tmp_path).returncode == 0


def test_verify_run_reports_stale_parquet_ahead_of_a_stale_render(tmp_path):
    parquet = _make_data_dir(tmp_path)
    _age(parquet, 37)
    _age(tmp_path / "site" / "index.html", 40)
    result = _verify(tmp_path)
    assert result.returncode == 1
    assert "RENDER STALE" not in result.stderr


def _runbook() -> str:
    return RUNBOOK.read_text()


def test_runbook_labels_every_shell_block_with_where_it_runs():
    lines = _runbook().splitlines()
    blocks = [i for i, line in enumerate(lines) if line.startswith("```sh")]
    assert len(blocks) >= 8
    for i in blocks:
        label = next(lines[k] for k in range(i - 1, -1, -1) if lines[k].strip())
        assert re.search(r"\*\*On (your Mac|rammingspeed)[^*]*\*\*", label), (i, label)


def test_runbook_says_it_applies_after_the_merge_to_main():
    text = _runbook()
    assert "merged to `main`" in text
    assert "git clone https://github.com/cdcoonce/apipulse" in text


def test_runbook_tags_locally_before_installing_and_before_every_pull():
    text = _runbook()
    assert 'git tag "deploy-$(date +%F)"' in text
    assert "git push origin deploy" not in text
    assert "git tag -l 'deploy-*'" in text
    assert "no GitHub credentials" in text
    pull_section = text[text.index("## 6.") : text.index("## 7.")]
    assert pull_section.index("git tag") < pull_section.index("git pull --ff-only")


def test_runbook_first_run_is_backgrounded_under_the_cron_lock_and_gates_the_schedule():
    text = _runbook()
    assert "nohup" in text
    assert "first-probe.log" in text
    assert "flock -w 3600 /home/cdcoonce/.local/state/apipulse/run.lock" in text
    assert "more than 3 hours" in text
    assert "Charles" in text
    assert text.index("first-probe.log") < text.index("(crontab -l 2>/dev/null; cat")


def test_runbook_cron_install_is_guarded_against_duplicates():
    text = _runbook()
    assert (
        "crontab -l 2>/dev/null | grep -q 'apipulse' && echo 'apipulse already in crontab; "
        "not appending' || (crontab -l 2>/dev/null; cat deploy/cron/apipulse.cron) | crontab -"
    ) in text
    assert "double-probe" in text


def test_runbook_enables_linger_before_the_service_and_waits_before_curl():
    text = _runbook()
    assert text.index("sudo loginctl enable-linger cdcoonce") < text.index(
        "systemctl --user enable --now apipulse-serve"
    )
    assert text.index("systemctl --user enable --now apipulse-serve") < text.index("sleep 5")
    assert "journalctl --user -u apipulse-serve -n 50" in text


def test_runbook_verify_passes_the_data_dir_and_documents_every_exit_code():
    text = _runbook()
    assert "scripts/verify_run.sh /home/cdcoonce/src/apipulse/data" in text
    for code in ("`0`", "`1`", "`2`", "`3`", "`4`"):
        assert code in text
    assert "RENDER STALE" in text
    assert "under about 10 hours" in text
    assert "ls -l ~/.local/state/apipulse/" in text
    assert 'grep -n "$(date +%F)" ~/.local/state/apipulse/probe.log' in text
    assert "refresh.log" in text


def test_runbook_rollback_changes_code_only_and_checks_the_optout_list_first():
    text = _runbook()
    section = text[text.index("## 7.") :]
    assert "git -C ~/src/apipulse diff deploy-" in section
    assert "data/optout.json" in section
    assert "fix forward" in section
    assert "systemctl --user restart apipulse-serve" in section
    assert "code only" in section.lower()
    assert section.index("diff") < section.index("checkout deploy-")


def test_runbook_says_the_maintainer_makes_the_preflight_decisions():
    text = _runbook()
    section = text[text.index("## 1.") : text.index("## 2.")]
    assert "Charles" in section
    assert "maintainer" in section


def test_runbook_explains_that_errors_counts_exceptions_not_transport_failures():
    text = _runbook()
    assert "exceptions inside apipulse" in text
    assert "transport failures" in text

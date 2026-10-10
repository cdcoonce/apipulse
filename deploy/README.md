# Deploying apipulse on rammingspeed

This is the runbook for the home server `rammingspeed`. It assumes you have this repo and ssh
access, nothing else. Everything is installed by hand; nothing here installs itself.

## What is installed, and what is not

rammingspeed is **Ubuntu Server 24.04 (Linux)**, not macOS. launchd does not exist there. So:

| What | File | Installed on rammingspeed? |
| --- | --- | --- |
| Scheduled refresh and probe+render | `deploy/cron/apipulse.cron` | Yes (cron) |
| Always-on API server | `deploy/systemd/apipulse-serve.service` | Yes (systemd user unit) |
| Same jobs for a macOS host | `deploy/launchd/*.plist` | **No.** Only for a future macOS host. |
| Post-run check | `scripts/verify_run.sh` | Yes (run by hand) |

The three launchd plists mirror the cron schedule and use the placeholder home `/Users/cdcoonce`
(launchd does not expand `~`). If you ever install them on a Mac, edit those paths first, copy
them to `~/Library/LaunchAgents/`, create `~/Library/Logs/apipulse/`, and `launchctl load` each.
Do not copy them to rammingspeed.

Facts about the box this runbook relies on (checked 2026-10-10): user `cdcoonce`, home
`/home/cdcoonce`, timezone America/Phoenix (no DST), LAN IP `192.168.0.103` (mDNS
`rammingspeed.local` is unreliable, so ssh by IP), `python3` 3.12, `git` and `flock` present,
`uv` and the `sqlite3` CLI **not** installed, cron active with no crontab for `cdcoonce`, port
8080 free. Other tenants (oura-pipeline, waga, weather-forecast-audit under `~/src/`, and the
Dagster Docker stack on :3000) share the machine: do not touch, stop or modify them.

## Schedule, and why it is early

| Job | When (local) | Command |
| --- | --- | --- |
| Inventory refresh | Sunday 00:30 | `apipulse refresh-inventory` |
| Probe then render | Daily 01:00 | `apipulse probe && apipulse render` |

The server has unattended-upgrades configured to **reboot automatically at 04:30**. Probing is
sequential with a 10 second timeout per request across roughly 3,000 APIs, so a run that
starts at 03:30 can be killed mid-way by that reboot. Starting at 01:00 leaves about 3.5 hours.
A killed probe writes nothing (the month's Parquet is appended at the very end), so the symptom
is a stale Parquet and `scripts/verify_run.sh` failing. Refresh and probe share one `flock` lock
so they never write SQLite at the same time; a job waits up to an hour for the lock.

## 1. Decisions to make before the first live run

Nothing has probed the live internet before this install. The first `refresh-inventory` and the
first `probe` are the first real traffic. Settle these two first:

1. **Residential IP.** Every probe will come from the home's residential IP address (the PRD
   assumes this). Confirm that is acceptable to you.
2. **The User-Agent link.** Probes send
   `apipulse/<version> (+https://github.com/cdcoonce/apipulse#project-page)`. That anchor lands
   on the "Project page" section of the repository README. Open it and confirm it reads
   correctly: it must say what a probe sends (one `GET` per API per day, no credentials, no
   body) and how to opt out.

## 2. Install

```sh
ssh cdcoonce@192.168.0.103

# uv, user-level (lands in ~/.local/bin/uv; cron and systemd call it by absolute path)
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv --version

# clone next to the other tenants
mkdir -p ~/src
git clone https://github.com/cdcoonce/apipulse ~/src/apipulse
cd ~/src/apipulse
~/.local/bin/uv sync --frozen

# log and lock directory used by the cron lines
mkdir -p ~/.local/state/apipulse

# tag what is deployed; rollback depends on this (there are no tags yet)
git tag deploy-YYYY-MM-DD        # use today's date, e.g. deploy-2026-10-11
git push origin deploy-YYYY-MM-DD
```

### Data directory

The data directory is the clone's own `data/`: `/home/cdcoonce/src/apipulse/data`. Every
scheduled job sets `APIPULSE_DATA_DIR` to that path. This is deliberate. `refresh-inventory`
reads `seed.json` and `optout.json` **from the data directory**, and both are committed in the
repo's `data/`. Everything else under `data/` (SQLite database, `probes/*.parquet`, `site/`) is
gitignored, so `git pull` never touches it.

Warning: pointing `APIPULSE_DATA_DIR` somewhere else makes `refresh-inventory` silently read an
empty or missing opt-out list, and hosts that asked to be removed would be probed again. If you
ever move the data directory, copy `seed.json` and `optout.json` into it, and keep them in sync
with the repo.

The precedence is `--data-dir PATH`, then `APIPULSE_DATA_DIR`, then `./data`.

## 3. First run by hand

Run these in order, on the server, from the clone. The `export` makes every command in the
same shell use the clone's data directory.

```sh
cd /home/cdcoonce/src/apipulse
export APIPULSE_DATA_DIR=/home/cdcoonce/src/apipulse/data

~/.local/bin/uv run --frozen apipulse refresh-inventory
~/.local/bin/uv run --frozen apipulse probe          # slow: up to a few hours worst case
~/.local/bin/uv run --frozen apipulse render
scripts/verify_run.sh

# optional, to try the API by hand (Ctrl-C to stop; the systemd unit below runs it for real)
~/.local/bin/uv run --frozen apipulse serve --host 127.0.0.1 --port 8080
```

`probe` needs the inventory, so refresh comes first; `render` needs probe results to be useful.
Start the first `probe` early in the day, not near 04:30. All commands exit 0 on success and
print one summary line.

### Reading the summary lines

`refresh-inventory`:

```
inventory: seed=7 apis.guru=2500 public-apis=1400 -> 3100 active, 0 removed (opt-out), 0 deactivated
```

- `seed=`, `apis.guru=`, `public-apis=`: records each source yielded (a source at 0 when it
  should not be is a fetch or parse problem).
- `active`: APIs now in the inventory after de-duplicating by host and applying opt-outs.
- `removed (opt-out)`: hosts dropped because they are in `optout.json`.
- `deactivated`: APIs that disappeared from every source since last time.

`probe`:

```
probe: 3100 probed, 2800 responded, 2500 working, timeout=150, dns_failure=100, 0 errors, targets_refreshed: 3100, 5400000 ms -> /home/cdcoonce/src/apipulse/data/probes/2026-10.parquet, rollups: 3100, keyless: 1800, key-required: 600
```

- `probed`: one probe per active API. `responded`: got any HTTP response. `working`: responded
  with a 2xx status.
- Then one `name=count` per non-`responded` outcome seen (`timeout`, `dns_failure`,
  `tls_failure`, `connection_refused`, `other_error`). Failed APIs are data, not run failures.
- `errors`: probes that raised an unexpected exception inside apipulse (recorded as
  `other_error`). This should be 0; anything else deserves a look in the log.
- `targets_refreshed`: probe URLs re-chosen from OpenAPI specs this run (only APIs that have a
  spec; large on the first run, small afterwards because targets are re-selected on a TTL).
- `ms`: wall time of the run. `->` is the monthly Parquet file that was appended to.
- `rollups`: rollup rows written (one per active API). `keyless` / `key-required`: APIs
  classified by their recent responses.

`render`:

```
render: 3100 api pages, 0 removed, badges: 3100 -> /home/cdcoonce/src/apipulse/data/site
```

- `api pages` and `badges` should both equal the active count from `refresh-inventory`.
  `removed` is the number of opted-out hosts listed on `removed.html`.
  The site lands in `data/site/`.

(Numbers above are illustrative; the shapes are what the code prints.)

### Verify

```sh
cd /home/cdcoonce/src/apipulse && scripts/verify_run.sh
```

It prints the latest monthly Parquet file and its age, that file's row count, the `rollups` row
count, and the `site/index.html` modification time. Exit codes: `0` fresh (Parquet modified
within 36 hours), `1` stale (older than 36 hours), `2` no Parquet file at all. Row count grows
by about one row per active API per daily run within a month; rollups should equal the active
API count. The data directory comes from the first argument, then `APIPULSE_DATA_DIR`, then
`./data`; the Python interpreter from `PYTHON`, else the clone's `.venv/bin/python`.

## 4. Schedule

Install the crontab by **appending**; never replace what is there.

```sh
cd /home/cdcoonce/src/apipulse
(crontab -l 2>/dev/null; cat deploy/cron/apipulse.cron) | crontab -
crontab -l          # confirm: your existing lines plus the two apipulse lines
```

Install the server as a systemd **user** unit:

```sh
mkdir -p ~/.config/systemd/user
cp deploy/systemd/apipulse-serve.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now apipulse-serve

# The one sudo step. Linger is off for cdcoonce, so without it a user service only runs
# while someone is logged in and would not come back after the 04:30 reboot.
sudo loginctl enable-linger cdcoonce

systemctl --user status apipulse-serve
curl -s 'http://127.0.0.1:8080/v1/apis?q=weather' | head -c 300
```

The server listens on `127.0.0.1:8080` only. Exposing it beyond localhost is out of scope here.

## 5. Verify a scheduled run (the next morning)

```sh
cd /home/cdcoonce/src/apipulse
scripts/verify_run.sh            # exit 0, Parquet age well under 36 h
tail -n 5 ~/.local/state/apipulse/probe.log     # the probe and render summary lines
tail -n 5 ~/.local/state/apipulse/refresh.log   # Sunday mornings: that day's refresh line
```

A non-zero `verify_run.sh` means the daily job did not complete. Look at the tail of
`probe.log`. Typical causes: the box rebooted mid-run, `uv` not at `~/.local/bin/uv`, the state
directory missing, or the lock held by an earlier overrunning job.

## 6. Opt-outs on the server

When an opt-out PR (`opt-out: <host>` issue) merges, on the server:

```sh
cd /home/cdcoonce/src/apipulse
git pull --ff-only
~/.local/bin/uv sync --frozen
export APIPULSE_DATA_DIR=/home/cdcoonce/src/apipulse/data
~/.local/bin/uv run --frozen apipulse refresh-inventory
~/.local/bin/uv run --frozen apipulse render
```

Or wait for Sunday 00:30 for the refresh and the next 01:00 run for render. A probe that runs
before `refresh-inventory` has read the new list still probes that host once more.

## 7. Roll back

```sh
crontab -l                       # then: crontab -e   and delete the apipulse lines
systemctl --user disable --now apipulse-serve

cd /home/cdcoonce/src/apipulse
git checkout deploy-YYYY-MM-DD   # the previous deploy tag
~/.local/bin/uv sync --frozen
```

To go forward again: `git checkout main && git pull --ff-only`.

- Schema changes so far are additive (columns are added to the database on connect), so older
  code reading a newer database is expected to work, but that is **not tested**.
- Checking out an older tag also reverts the committed `data/optout.json`. Do not roll back to
  a tag older than the latest opt-out unless you re-apply it.
- **Never delete the data directory to roll back.** Probe history cannot be backfilled.

## Notes

- Logs live in `~/.local/state/apipulse/` and grow by a few lines per day. There is no rotation
  in v1.
- Do not touch the Docker/Dagster stack or the other tenants.
- Tag every deploy (`deploy-YYYY-MM-DD`) so there is always something to roll back to.

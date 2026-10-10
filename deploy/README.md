# Deploying apipulse on rammingspeed

This is the runbook for the home server `rammingspeed`. It assumes you have this repo and ssh
access, nothing else. Everything is installed by hand; nothing here installs itself.

It applies once the deployment slice (#15) is merged to `main`: the server clones `main`, and
the files named below (`deploy/`, `scripts/verify_run.sh`) only exist there after the merge.

Every command block says where it runs: **On your Mac** or **On rammingspeed**. Never paste a
Mac block into the server shell or the reverse.

## What is installed, and what is not

rammingspeed is **Ubuntu Server 24.04 (Linux)**, not macOS. launchd does not exist there. So:

| What | File | Installed on rammingspeed? |
| --- | --- | --- |
| Scheduled refresh and probe+render | `deploy/cron/apipulse.cron` | Yes (cron) |
| Always-on API server | `deploy/systemd/apipulse-serve.service` | Yes (systemd user unit) |
| Same jobs for a macOS host | `deploy/launchd/*.plist` | **No.** Only for a future macOS host. |
| Post-run check | `scripts/verify_run.sh` | Yes (run by hand) |

The three launchd plists mirror the schedule and data dir but NOT the lock,
timestamps or failure echo that the cron lines add; they are for a macOS host only.
They use the placeholder home `/Users/cdcoonce` (launchd does not expand `~`). If you ever install them on a Mac, edit those
paths first, copy them to `~/Library/LaunchAgents/`, create `~/Library/Logs/apipulse/`, and
`launchctl load` each. Do not copy them to rammingspeed.

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
A killed probe writes no probe results (the month's Parquet is appended at the very end), so the
symptom is a Parquet that stops advancing and, after 36 hours, `scripts/verify_run.sh` failing.
Refresh and probe share one `flock` lock so they never write SQLite at the same time; a job waits
up to an hour for the lock.

## 1. Decisions to make before the first live run

Charles (the maintainer) makes both decisions below before anything runs, and records both
decisions, and later the install outcome, as a comment on the #15 pull request. The operator
reads that pull request before starting and stops if the comment is absent. Nothing has probed
the live internet before this install: the first `refresh-inventory` and the first `probe` are
the first real traffic.

1. **Residential IP.** Every probe will come from the home's residential IP address (the PRD
   assumes this). Confirm that is acceptable.
2. **The User-Agent link.** Probes send
   `apipulse/<version> (+https://github.com/cdcoonce/apipulse#project-page)`. That anchor lands
   on the "Project page" section of the repository README. Open it and confirm it reads
   correctly: it must say what a probe sends (one `GET` per API per day, no credentials, no
   body) and how to opt out.

## 2. Install

**On your Mac:**

```sh
ssh cdcoonce@192.168.0.103
```

Install uv user-level; it lands in `~/.local/bin/uv`, and cron and systemd call it by absolute
path.

**On rammingspeed:**

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv --version
```

Clone `main` next to the other tenants and install the locked dependencies. The repository is
public, so the clone needs no credentials.

**On rammingspeed:**

```sh
mkdir -p ~/src
git clone https://github.com/cdcoonce/apipulse ~/src/apipulse
cd ~/src/apipulse
~/.local/bin/uv sync --frozen
mkdir -p ~/.local/state/apipulse
```

Tag what is deployed. Tags are local to the server: it has no GitHub credentials, so nothing is
pushed. Rollback depends on these tags.

**On rammingspeed:**

```sh
cd ~/src/apipulse
git tag "deploy-$(date +%F)"
git tag -l 'deploy-*'
```

Tag again before every later `git pull` (section 6). For the very first deploy there is no
earlier tag, so the rollback target is this install tag itself, or a commit SHA from
`git -C ~/src/apipulse log --oneline`.

Repeating a step is mostly harmless. Running `git clone` into an existing directory fails
harmlessly ("already exists and is not an empty directory"): skip it and carry on. Tagging twice
on the same day fails harmlessly too ("tag already exists"): use the suffix form,
`git tag "deploy-$(date +%F)-2"`.

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

## 3. First run by hand (before cron exists)

The first probe can take hours, so run it detached. tmux may not be installed; `nohup` always
is. Take the same lock as the cron lines (`flock -w 3600 .../run.lock`) so a scheduled job can
never overlap it. Do **not** install cron until the first probe and render have finished and
you have read the wall time (below).

First check nothing is already running. If this prints a process line, stop: do not start
another.

**On rammingspeed:**

```sh
pgrep -fa 'apipulse (probe|render|refresh-inventory)' && echo 'already running; do not start another'
```

Then run the inventory (quick; it needs the network and raises if a directory fetch fails), and
launch probe then render detached:

**On rammingspeed:**

```sh
cd /home/cdcoonce/src/apipulse
export APIPULSE_DATA_DIR=/home/cdcoonce/src/apipulse/data

# 1. inventory
flock -w 3600 /home/cdcoonce/.local/state/apipulse/run.lock \
  /home/cdcoonce/.local/bin/uv run --frozen apipulse refresh-inventory

# 2. probe then render, detached; render runs only if probe succeeded
nohup flock -w 3600 /home/cdcoonce/.local/state/apipulse/run.lock /bin/sh -c \
  '/home/cdcoonce/.local/bin/uv run --frozen apipulse probe && /home/cdcoonce/.local/bin/uv run --frozen apipulse render' \
  > /home/cdcoonce/.local/state/apipulse/first-probe.log 2>&1 &
```

Start it early in the day, not near 04:30. The first line of `first-probe.log` may be
`nohup: ignoring input`; that is normal. The probe prints nothing until it ends. To see whether
it is still running (no output means it finished), then read the log:

**On rammingspeed:**

```sh
pgrep -fa 'apipulse (probe|render)'
cat /home/cdcoonce/.local/state/apipulse/first-probe.log
```

When it has finished the log holds the `probe:` and `render:` lines described below.

### If the first run failed

- No `probe:` line and `pgrep` prints nothing: the run is over and did not succeed. Read the
  traceback at the end of `first-probe.log`.
- `refresh-inventory` raises (non-zero exit, a traceback) if either directory fetch fails. Re-run
  it later. The seed source alone is not a deploy: do not carry on to the probe with a partial
  inventory.
- A reboot during the run leaves no Parquet: nothing is written until the end. Re-run earlier in
  the day.
- Re-running is safe only when `pgrep` is empty. If it prints a line, the first run is still
  going.

### Gate: how long did the first probe take?

Read the wall time in the `probe:` line (`<N> ms`). **If the first probe took more than 3 hours
(10,800,000 ms), the 01:00 slot does not fit before the 04:30 reboot.** Stop here, do not install
cron, and report the wall time to Charles. He decides between moving the schedule, disabling
the auto-reboot, or bounding the run.

### Reading the summary lines

`refresh-inventory`:

```
inventory: seed=7 apis.guru=2500 public-apis=1400 -> 3100 active, 0 removed (opt-out), 0 deactivated
```

- `seed=`, `apis.guru=`, `public-apis=`: records each source yielded (a source at 0 when it
  should not be is a fetch or parse problem).
- `active`: APIs that are active in the inventory after this refresh (records de-duplicated by
  host, opted-out hosts left out).
- `removed (opt-out)`: records dropped this refresh because their host is in `optout.json`.
- `deactivated`: every API that was active before this refresh and is not kept by it. That
  includes newly opted-out APIs, so the first refresh after an opt-out shows
  `1 removed (opt-out), 1 deactivated`. That is expected, not a fault. A later refresh shows
  `1 removed (opt-out), 0 deactivated`, because the API is already inactive.

`probe`:

```
probe: 3100 probed, 2800 responded, 2500 working, timeout=150, dns_failure=100, 0 errors, targets_refreshed: 3100, 5400000 ms -> /home/cdcoonce/src/apipulse/data/probes/2026-10.parquet, rollups: 3100, keyless: 1800, key-required: 600
```

- `probed`: one probe per active API. `responded`: got any HTTP response. `working`: responded
  with a 2xx status.
- Then one `name=count` per non-`responded` outcome seen (`timeout`, `dns_failure`,
  `tls_failure`, `connection_refused`, `other_error`). Failed APIs are data, not run failures.
- `errors`: counts exceptions inside apipulse itself (each is recorded as `other_error`). It
  should be 0. Network-level problems (timeouts, DNS, TLS, refused connections) are not errors in
  this sense; those transport failures appear as the outcome counts above.
- `targets_refreshed`: probe URLs re-chosen from OpenAPI specs this run (only APIs that have a
  spec; large on the first run, small afterwards because targets are re-selected on a TTL).
- `ms`: wall time of the run, the number the gate above reads. `->` is the monthly Parquet file
  that was appended to.
- `rollups`: rollup rows written this run, one per active API (so it equals `probed`).
  `keyless` / `key-required`: APIs classified by their recent responses.

`render`:

```
render: 3100 api pages, 0 removed, badges: 3100 -> /home/cdcoonce/src/apipulse/data/site
```

- `api pages` equals `badges`, and both count every API in the inventory table (active + deactivated),
  minus opted-out ids (and ids that are not safe as file names). So after deactivations it is
  larger than the `active` figure from `refresh-inventory`. Do not expect them to match.
- `removed` is the number of opt-outs recorded in the database (all of them, ever), listed on
  `removed.html`. The site lands in `data/site/`.

In the database the `rollups` table keeps one row per API ever probed and rows are never deleted.
Right after a probe its row count is therefore at least the active count, and higher once any API
has been deactivated. Between a refresh and the next probe, newly added APIs have no rollup yet.
`scripts/verify_run.sh` prints this row count.

(Numbers above are illustrative; the shapes are what the code prints.)

### Verify the first run

**On rammingspeed:**

```sh
cd /home/cdcoonce/src/apipulse
scripts/verify_run.sh /home/cdcoonce/src/apipulse/data
```

Always pass the data directory explicitly. It prints the latest monthly Parquet file and its
age, that file's row count, the `rollups` row count, and the `site/index.html:` modification
time. On the first run read that `site/index.html:` line: exit `0` does not prove a render
happened, because a missing site directory is reported as `missing` and does not fail. If it says
`missing`, the render did not run.

Exit codes:

- `0`: fresh. The latest Parquet was modified within 36 hours and the rendered site is not behind it.
- `1`: stale. The latest Parquet is older than 36 hours.
- `2`: no Parquet file at all.
- `3`: `RENDER STALE`. The render is behind the probe. Render writes `index.html` first and
  `project.html` last, so the script judges the oldest of `index.html`, `removed.html` and
  `project.html`: if it is more than an hour older than the latest Parquet, or if `index.html`
  exists without `project.html` (a partial render), it exits `3`.
- `4`: unexpected error in the script itself (message on stderr). A crash never exits `1`.

The precedence is 2, then 1, then 3.

## 4. Schedule

Only after the first probe and render finished and the gate above passed.

### Cron

Install by **appending the two job lines only** (the comments stay in the repo file), never by
replacing the crontab. The guard refuses to append twice, because duplicate lines would
double-probe every API. It matches on the clone path `/src/apipulse`, which both job lines carry.

**On rammingspeed:**

```sh
cd /home/cdcoonce/src/apipulse
crontab -l 2>/dev/null | grep -q '/src/apipulse' && echo 'apipulse already in crontab; not appending' || (crontab -l 2>/dev/null; grep -v '^#' deploy/cron/apipulse.cron | grep .) | crontab -
crontab -l          # confirm: your existing lines plus the two apipulse lines
```

### Serve (systemd user unit)

Linger first. Linger is off for `cdcoonce`, and without it a user service stops when you log out
and does not come back after the 04:30 reboot. This is the one sudo step. If you do not have the
sudo password, or linger cannot be enabled, do NOT install the unit: stop here and hand this one
line to Charles (the service would die at logout and after the 04:30 reboot). Do the rest after
it is done.

**On rammingspeed:**

```sh
sudo loginctl enable-linger cdcoonce
```

Only once that has succeeded:

**On rammingspeed:**

```sh
mkdir -p ~/.config/systemd/user
cp ~/src/apipulse/deploy/systemd/apipulse-serve.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now apipulse-serve
sleep 5          # uv starts the server a moment after systemd says "started"
systemctl --user status apipulse-serve
curl -s 'http://127.0.0.1:8080/v1/apis?q=weather' | head -c 300
```

If the curl returns nothing or the status is failed:

**On rammingspeed:**

```sh
journalctl --user -u apipulse-serve -n 50
```

The server listens on `127.0.0.1:8080` only. Exposing it beyond localhost is out of scope here.

To try the API by hand instead (Ctrl-C to stop it before you enable the unit, which uses the
same port):

**On rammingspeed:**

```sh
cd /home/cdcoonce/src/apipulse
APIPULSE_DATA_DIR=/home/cdcoonce/src/apipulse/data \
  ~/.local/bin/uv run --frozen apipulse serve --host 127.0.0.1 --port 8080
```

## 5. Verify a scheduled run (the next morning)

**On rammingspeed:**

```sh
cd /home/cdcoonce/src/apipulse
scripts/verify_run.sh /home/cdcoonce/src/apipulse/data
ls -l ~/.local/state/apipulse/
tail -n 5 ~/.local/state/apipulse/probe.log
awk -v d="$(date +%F)" 'index($0,d){seen=1} seen' ~/.local/state/apipulse/probe.log | grep -E '^apipulse (probe\+render|refresh) failed'
```

Reading the result:

- After the 01:00 run, a morning check should show the Parquet age under about 10 hours. The
  36 hour threshold is only the alarm for "no run since yesterday": one missed run can still pass
  it, so also look at the log.
- Every cron line writes a `date -Is` timestamp to its log first, so today's run starts at a line
  carrying today's date, followed by the `probe:` and `render:` lines. uv's own output (for
  example sync messages) can appear in the logs too.
- The `awk ... | grep` line prints any `apipulse ... failed: exit N` line written since today's
  first timestamp. No output means no failure today. A line `apipulse probe+render failed: exit N`
  means the job failed, or waited an hour for the lock and gave up. A timestamp with nothing after
  it for hours means the run was cut off (reboot).
- The Sunday refresh has no verify script; check it only through `refresh.log` (the `inventory:`
  line after Sunday's timestamp), and run the same `awk` line with `refresh.log`. `refresh.log`
  does not exist until the first Sunday run: that is expected, not a fault.

A non-zero `verify_run.sh` means the daily job did not complete cleanly. Typical causes: the box
rebooted mid-run, `uv` is not at `~/.local/bin/uv`, the state directory is missing, or the lock
was held by an earlier overrunning job.

## 6. Opt-outs on the server

When an opt-out PR (`opt-out: <host>` issue) merges, update the server. First check the clone is
on a branch (a detached HEAD, left over from a rollback, cannot pull). If this prints the
`detached` message, do section 7's roll-forward first.

**On rammingspeed:**

```sh
git -C ~/src/apipulse symbolic-ref -q HEAD || echo 'detached: roll forward first (section 7)'
```

Tag first, so there is something to roll back to (if today's tag exists, use the suffix form
`deploy-$(date +%F)-2`):

**On rammingspeed:**

```sh
cd ~/src/apipulse
git tag "deploy-$(date +%F)"
```

Then pull and refresh inside the same lock cron takes, so this cannot swap code or write SQLite
under a running probe. If a probe is running this waits up to an hour, so do it in the daytime.
Finally restart the server so it loads the new code:

**On rammingspeed:**

```sh
flock -w 3600 ~/.local/state/apipulse/run.lock /bin/sh -c 'export PATH=/home/cdcoonce/.local/bin:$PATH APIPULSE_DATA_DIR=/home/cdcoonce/src/apipulse/data && cd /home/cdcoonce/src/apipulse && git pull --ff-only && uv sync --frozen && uv run --frozen apipulse refresh-inventory && uv run --frozen apipulse render'
systemctl --user restart apipulse-serve
```

Or wait for Sunday 00:30 for the refresh and the next 01:00 run for render, after the pull. A
probe that runs before `refresh-inventory` has read the new list still probes that host once more.
The refresh line will show `1 removed (opt-out), 1 deactivated` for a newly opted-out host.

## 7. Roll back, and roll forward again

Rollback changes **code only**. Cron and the service stay installed: they run whatever is checked
out. Pick and inspect a target:

**On rammingspeed:**

```sh
cd ~/src/apipulse
git tag -l 'deploy-*'
git log --oneline -1 deploy-YYYY-MM-DD
```

Before rolling back, check that the older code would not re-probe a host that opted out. If
this prints the `optout.json differs` message, inspect with `git diff`: if any host was added
after the tag, do not roll back past it; fix forward instead.

**On rammingspeed:**

```sh
cd ~/src/apipulse
git diff --quiet deploy-YYYY-MM-DD main -- data/optout.json || echo 'optout.json differs: inspect with git diff; if any host was added after deploy-YYYY-MM-DD, do not roll back past it — ask Charles'
```

If it printed nothing (or the only differences are removals), roll back inside the cron lock, so
the code cannot change under a running probe, then restart the server:

**On rammingspeed:**

```sh
flock -w 3600 ~/.local/state/apipulse/run.lock /bin/sh -c 'git -C ~/src/apipulse checkout deploy-YYYY-MM-DD && cd ~/src/apipulse && /home/cdcoonce/.local/bin/uv sync --frozen'
systemctl --user restart apipulse-serve
```

To go forward again (HEAD stays detached until you do). Tag what is checked out only if it has no
deploy tag yet, then check out `main`, all inside the lock; if today's tag name is taken use
`git tag "deploy-$(date +%F)-2"`:

**On rammingspeed:**

```sh
cd ~/src/apipulse
git describe --tags --exact-match HEAD || git tag "deploy-$(date +%F)"
flock -w 3600 ~/.local/state/apipulse/run.lock /bin/sh -c 'export PATH=/home/cdcoonce/.local/bin:$PATH && cd /home/cdcoonce/src/apipulse && git checkout main && git pull --ff-only && uv sync --frozen'
systemctl --user restart apipulse-serve
```

Only remove the schedule if the schedule itself is the problem. This deletes exactly the lines
containing `/src/apipulse` (the weekly `refresh-inventory` line and the daily `probe` line; the
comments were never installed), and stops the unit:

**On rammingspeed:**

```sh
crontab -l | grep -v '/src/apipulse' | crontab -
crontab -l                       # confirm only your other lines remain
systemctl --user disable --now apipulse-serve
```

To put them back, re-run the guarded append from section 4 (it works because no `/src/apipulse`
line remains) and `systemctl --user enable --now apipulse-serve`.

- Schema changes so far are additive (columns are added to the database on connect), so older
  code reading a newer database is expected to work, but that is **not tested**.
- **Never delete the data directory to roll back.** Probe history cannot be backfilled.

## Notes

- Logs live in `~/.local/state/apipulse/` and grow by a few lines per day. There is no rotation
  in v1.
- Do not touch the Docker/Dagster stack or the other tenants.
- Tag every deploy (`deploy-YYYY-MM-DD`) so there is always something to roll back to.

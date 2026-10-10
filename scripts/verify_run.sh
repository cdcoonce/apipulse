#!/usr/bin/env bash
# Verify the last scheduled apipulse run left fresh data behind.
#
# Usage: scripts/verify_run.sh [DATA_DIR]
# Exit: 0 fresh, 1 latest Parquet older than 36 h, 2 no Parquet, 3 site older than the latest
#       probe by over 1 h (render failed), 4 unexpected error.
# Data dir: first argument, else $APIPULSE_DATA_DIR, else ./data.
# Python:   $PYTHON, else <repo>/.venv/bin/python (needs pyarrow).
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
data_dir="${1:-${APIPULSE_DATA_DIR:-data}}"
python_bin="${PYTHON:-$repo/.venv/bin/python}"

"$python_bin" - "$data_dir" <<'PY'
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


def _unexpected(kind, exc, _tb):
    # Exit 1 means STALE and nothing else; any crash must be told apart from it.
    sys.stdout.flush()
    print(f"ERROR: unexpected {kind.__name__}: {exc}", file=sys.stderr, flush=True)
    os._exit(4)


sys.excepthook = _unexpected

import pyarrow.parquet as pq  # noqa: E402  (after the hook so an import failure exits 4)

data_dir = Path(sys.argv[1]).resolve()
files = sorted((data_dir / "probes").glob("*.parquet"))
if not files:
    print(f"NO PARQUET: nothing under {data_dir / 'probes'}", file=sys.stderr)
    sys.exit(2)
latest = files[-1]
MAX_AGE_HOURS = 36
RENDER_LAG_HOURS = 1
age_hours = (time.time() - latest.stat().st_mtime) / 3600
print(f"latest parquet: {latest} (age {age_hours:.1f} h)")
print(f"rows: {pq.read_metadata(latest).num_rows}")
db = data_dir / "apipulse.db"
if db.exists():
    # Read-only URI: verifying must never create or modify the database.
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    print(f"rollups: {conn.execute('SELECT COUNT(*) FROM rollups').fetchone()[0]}")
    conn.close()
else:
    print("rollups: missing (no apipulse.db)")
site = data_dir / "site" / "index.html"
if site.exists():
    site_mtime = datetime.fromtimestamp(site.stat().st_mtime).astimezone()
    print(f"site/index.html: {site_mtime:%Y-%m-%d %H:%M:%S %Z}")
else:
    print("site/index.html: missing")
if age_hours > MAX_AGE_HOURS:
    print(f"STALE: latest parquet is older than {MAX_AGE_HOURS} hours", file=sys.stderr)
    sys.exit(1)
# A probe that succeeded but a render that did not leaves a site older than the data.
if site.exists() and site.stat().st_mtime < latest.stat().st_mtime - RENDER_LAG_HOURS * 3600:
    print("RENDER STALE: site/index.html is older than the latest probe", file=sys.stderr)
    sys.exit(3)
PY

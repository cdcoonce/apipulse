#!/usr/bin/env bash
# Verify the last scheduled apipulse run left fresh data behind.
#
# Usage: scripts/verify_run.sh [DATA_DIR]
# Data dir: first argument, else $APIPULSE_DATA_DIR, else ./data.
# Python:   $PYTHON, else <repo>/.venv/bin/python (needs pyarrow).
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
data_dir="${1:-${APIPULSE_DATA_DIR:-data}}"
python_bin="${PYTHON:-$repo/.venv/bin/python}"

"$python_bin" - "$data_dir" <<'PY'
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

data_dir = Path(sys.argv[1])
files = sorted((data_dir / "probes").glob("*.parquet"))
if not files:
    print(f"NO PARQUET: nothing under {data_dir / 'probes'}", file=sys.stderr)
    sys.exit(2)
latest = files[-1]
MAX_AGE_HOURS = 36
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
PY

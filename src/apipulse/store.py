import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from apipulse.config import Settings
from apipulse.inventory import ApiRecord
from apipulse.prober import ProbeResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS apis (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    base_url TEXT NOT NULL,
    spec_url TEXT,
    declared_auth TEXT,
    signup_url TEXT,
    source TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS optouts (
    key TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL
);
"""


def connect(settings: Settings) -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "apipulse.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


@dataclass(frozen=True)
class ProbeRow:
    api_id: str
    url: str
    probed_at: datetime
    outcome: str
    status: int | None
    elapsed_ms: int | None
    tls_ok: bool | None
    final_url: str | None
    ratelimit_headers_json: str
    error: str | None


@dataclass(frozen=True)
class UpsertSummary:
    inserted: int
    updated: int
    deactivated: int


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def upsert_apis(
    conn: sqlite3.Connection, records: list[ApiRecord], *, seen_at: datetime
) -> UpsertSummary:
    stamp = _iso(seen_at)
    existing = {row["id"] for row in conn.execute("SELECT id FROM apis")}
    inserted = updated = 0
    for r in records:
        values = (
            r.name,
            r.description,
            json.dumps(list(r.tags)),
            r.base_url,
            r.spec_url,
            r.declared_auth,
            r.signup_url,
            r.source,
        )
        if r.id in existing:
            conn.execute(
                "UPDATE apis SET name=?, description=?, tags=?, base_url=?, spec_url=?,"
                " declared_auth=?, signup_url=?, source=?, last_seen_at=?, active=1"
                " WHERE id=?",
                (*values, stamp, r.id),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO apis (name, description, tags, base_url, spec_url,"
                " declared_auth, signup_url, source, id, first_seen_at, last_seen_at, active)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (*values, r.id, stamp, stamp),
            )
            inserted += 1
    kept = {r.id for r in records}
    dropped = [
        row["id"]
        for row in conn.execute("SELECT id FROM apis WHERE active = 1")
        if row["id"] not in kept
    ]
    for api_id in dropped:
        conn.execute("UPDATE apis SET active = 0 WHERE id = ?", (api_id,))
    conn.commit()
    return UpsertSummary(inserted, updated, len(dropped))


def load_apis(conn: sqlite3.Connection, *, active_only: bool = True) -> list[ApiRecord]:
    query = "SELECT * FROM apis"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY id"
    return [
        ApiRecord(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            tags=tuple(json.loads(row["tags"])),
            base_url=row["base_url"],
            spec_url=row["spec_url"],
            declared_auth=row["declared_auth"],
            signup_url=row["signup_url"],
            source=row["source"],
        )
        for row in conn.execute(query)
    ]


def record_optouts(
    conn: sqlite3.Connection, removed: list[ApiRecord], *, recorded_at: datetime
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO optouts (key, recorded_at) VALUES (?, ?)",
        [(r.id, _iso(recorded_at)) for r in removed],
    )
    conn.commit()


PROBES_SCHEMA = pa.schema(
    [
        pa.field("api_id", pa.string()),
        pa.field("url", pa.string()),
        pa.field("probed_at", pa.timestamp("us", tz="UTC")),
        pa.field("outcome", pa.string()),
        pa.field("status", pa.int32()),
        pa.field("elapsed_ms", pa.int32()),
        pa.field("tls_ok", pa.bool_()),
        pa.field("final_url", pa.string()),
        pa.field("ratelimit_headers_json", pa.string()),
        pa.field("error", pa.string()),
    ]
)


def probes_path(settings: Settings, month: datetime) -> Path:
    return settings.data_dir / "probes" / f"{month:%Y-%m}.parquet"


def append_probes(
    settings: Settings, results: list[tuple[str, ProbeResult]], *, now: datetime
) -> Path:
    path = probes_path(settings, now)
    rows = [
        {
            "api_id": api_id,
            "url": r.url,
            "probed_at": r.probed_at,
            "outcome": r.outcome.value,
            "status": r.status,
            "elapsed_ms": r.elapsed_ms,
            "tls_ok": r.tls_ok,
            "final_url": r.final_url,
            "ratelimit_headers_json": json.dumps(r.ratelimit_headers, sort_keys=True),
            "error": r.error,
        }
        for api_id, r in results
    ]
    table = pa.Table.from_pylist(rows, schema=PROBES_SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        table = pa.concat_tables([pq.read_table(path), table])
    with tempfile.NamedTemporaryFile(
        dir=path.parent, suffix=".parquet.tmp", delete=False
    ) as handle:
        tmp_name = handle.name
    try:
        pq.write_table(table, tmp_name)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def read_probes(settings: Settings, *, since: datetime) -> list[ProbeRow]:
    first_month = f"{since:%Y-%m}"
    rows: list[ProbeRow] = []
    for path in sorted((settings.data_dir / "probes").glob("*.parquet")):
        if path.stem < first_month:
            continue
        rows.extend(ProbeRow(**raw) for raw in pq.read_table(path).to_pylist())
    return sorted((r for r in rows if r.probed_at >= since), key=lambda r: r.probed_at)

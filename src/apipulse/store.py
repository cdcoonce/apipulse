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
from apipulse.rollup import Rollup, probe_depth

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
CREATE TABLE IF NOT EXISTS rollups (
    api_id TEXT PRIMARY KEY,
    reachable_30d INTEGER NOT NULL,
    working_30d INTEGER NOT NULL,
    days_observed_30d INTEGER NOT NULL,
    p50_ms INTEGER,
    p95_ms INTEGER,
    last_probe_at TEXT,
    last_outcome TEXT,
    last_status INTEGER,
    probe_target TEXT,
    computed_at TEXT NOT NULL,
    auth TEXT NOT NULL DEFAULT 'unknown',
    ratelimit_headers_seen TEXT NOT NULL DEFAULT '[]',
    saw_429_30d INTEGER NOT NULL DEFAULT 0
);
"""


_ROLLUP_SIGNAL_DDL = {
    "auth": "TEXT NOT NULL DEFAULT 'unknown'",
    "ratelimit_headers_seen": "TEXT NOT NULL DEFAULT '[]'",
    "saw_429_30d": "INTEGER NOT NULL DEFAULT 0",
}


def connect(settings: Settings) -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "apipulse.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def fts_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'apis_fts'").fetchone()
    return row is not None


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(apis)")}
    for column in ("probe_target", "target_selected_at"):
        if column not in existing:
            conn.execute(f"ALTER TABLE apis ADD COLUMN {column} TEXT")
    rollup_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rollups)")}
    for column, ddl in _ROLLUP_SIGNAL_DDL.items():
        if column not in rollup_columns:
            conn.execute(f"ALTER TABLE rollups ADD COLUMN {column} {ddl}")
    if not fts_available(conn):
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE apis_fts USING fts5(id UNINDEXED, name, description, tags)"
            )
        except sqlite3.OperationalError:
            conn.commit()
            return
        conn.execute(
            "INSERT INTO apis_fts (id, name, description, tags)"
            " SELECT id, name, description, tags FROM apis"
        )
    conn.commit()


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
        _index_fts(conn, r)
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


def _index_fts(conn: sqlite3.Connection, record: ApiRecord) -> None:
    if not fts_available(conn):
        return
    conn.execute("DELETE FROM apis_fts WHERE id = ?", (record.id,))
    conn.execute(
        "INSERT INTO apis_fts (id, name, description, tags) VALUES (?, ?, ?, ?)",
        (record.id, record.name, record.description, " ".join(record.tags)),
    )


@dataclass(frozen=True)
class SearchHit:
    id: str
    name: str
    reachable_30d: int
    working_30d: int
    p50_ms: int | None
    last_probe_at: datetime | None
    active: bool
    relevance: float
    auth: str
    days_observed_30d: int = 0
    probe_depth: str | None = None


def tokenize_query(q: str) -> list[str]:
    return [t.lower() for t in q.split()]


def _hit(row: sqlite3.Row, relevance: float) -> SearchHit:
    stamp = row["last_probe_at"]
    return SearchHit(
        id=row["id"],
        name=row["name"],
        reachable_30d=row["reachable_30d"] or 0,
        working_30d=row["working_30d"] or 0,
        p50_ms=row["p50_ms"],
        last_probe_at=datetime.fromisoformat(stamp) if stamp else None,
        active=bool(row["active"]),
        relevance=relevance,
        auth=row["auth"] or "unknown",
        days_observed_30d=row["days_observed_30d"] or 0,
        probe_depth=probe_depth(row["probe_target"], row["base_url"]),
    )


_FTS_SEARCH = (
    "SELECT a.id, a.name, a.active, r.reachable_30d, r.working_30d, r.p50_ms,"
    " r.last_probe_at, r.auth, r.days_observed_30d, r.probe_target, a.base_url,"
    " bm25(apis_fts) AS rank"
    " FROM apis_fts JOIN apis a ON a.id = apis_fts.id"
    " LEFT JOIN rollups r ON r.api_id = a.id"
    " WHERE apis_fts MATCH ? AND a.id NOT IN (SELECT key FROM optouts)"
    " ORDER BY rank ASC, COALESCE(r.reachable_30d, 0) DESC,"
    " COALESCE(r.working_30d, 0) DESC, (r.p50_ms IS NULL) ASC, r.p50_ms ASC, a.id ASC"
    " LIMIT ?"
)


_PLAIN_SEARCH = (
    "SELECT a.id, a.name, a.description, a.tags, a.active, r.reachable_30d, r.working_30d,"
    " r.p50_ms, r.last_probe_at, r.auth, r.days_observed_30d, r.probe_target, a.base_url"
    " FROM apis a LEFT JOIN rollups r ON r.api_id = a.id"
    " WHERE a.id NOT IN (SELECT key FROM optouts)"
)


def search_order_key(hit: SearchHit) -> tuple:
    """Plain-search ordering: relevance, then reachable, working, p50 (nulls last), id."""
    return (
        -hit.relevance,
        -hit.reachable_30d,
        -hit.working_30d,
        hit.p50_ms is None,
        hit.p50_ms or 0,
        hit.id,
    )


def _search_plain(conn: sqlite3.Connection, tokens: list[str], limit: int) -> list[SearchHit]:
    scored: list[SearchHit] = []
    for row in conn.execute(_PLAIN_SEARCH):
        columns = (
            row["name"].lower(),
            row["description"].lower(),
            " ".join(json.loads(row["tags"])).lower(),
        )
        counts = [sum(1 for c in columns if t in c) for t in tokens]
        if all(counts):
            scored.append(_hit(row, float(sum(counts))))
    scored.sort(key=search_order_key)
    return scored[:limit]


def search_apis(
    conn: sqlite3.Connection, q: str, *, limit: int, use_fts: bool | None = None
) -> list[SearchHit]:
    tokens = tokenize_query(q)
    if not tokens:
        return []
    if use_fts is None:
        use_fts = fts_available(conn)
    if not use_fts:
        return _search_plain(conn, tokens, limit)
    match = " AND ".join('"' + t.replace('"', '""') + '"*' for t in tokens)
    return [_hit(row, -row["rank"]) for row in conn.execute(_FTS_SEARCH, (match, limit))]


def _api_record(row: sqlite3.Row) -> ApiRecord:
    return ApiRecord(
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


def load_apis_with_state(conn: sqlite3.Connection) -> list[tuple[ApiRecord, bool]]:
    """Every API ordered by id, paired with its active flag."""
    rows = conn.execute("SELECT * FROM apis ORDER BY id")
    return [(_api_record(row), bool(row["active"])) for row in rows]


def load_apis(conn: sqlite3.Connection, *, active_only: bool = True) -> list[ApiRecord]:
    return [record for record, active in load_apis_with_state(conn) if active or not active_only]


def data_anchor(conn: sqlite3.Connection) -> datetime | None:
    """A moment derived from the stored data, never the wall clock.

    The newest rollup computation time, or the newest inventory sighting when no rollup exists.
    """
    stamps = [row[0] for row in conn.execute("SELECT computed_at FROM rollups")]
    if not stamps:
        stamps = [row[0] for row in conn.execute("SELECT last_seen_at FROM apis")]
    return max((datetime.fromisoformat(s) for s in stamps), default=None)


def load_probe_targets(conn: sqlite3.Connection) -> dict[str, tuple[str, datetime]]:
    rows = conn.execute(
        "SELECT id, probe_target, target_selected_at FROM apis"
        " WHERE probe_target IS NOT NULL AND target_selected_at IS NOT NULL"
    )
    return {
        row["id"]: (row["probe_target"], datetime.fromisoformat(row["target_selected_at"]))
        for row in rows
    }


def set_probe_target(
    conn: sqlite3.Connection, api_id: str, target: str, *, selected_at: datetime
) -> None:
    conn.execute(
        "UPDATE apis SET probe_target = ?, target_selected_at = ? WHERE id = ?",
        (target, _iso(selected_at), api_id),
    )
    conn.commit()


def record_optouts(
    conn: sqlite3.Connection, removed: list[ApiRecord], *, recorded_at: datetime
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO optouts (key, recorded_at) VALUES (?, ?)",
        [(r.id, _iso(recorded_at)) for r in removed],
    )
    conn.commit()


def is_opted_out(conn: sqlite3.Connection, api_id: str) -> bool:
    return conn.execute("SELECT 1 FROM optouts WHERE key = ?", (api_id,)).fetchone() is not None


def load_optouts(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every recorded opt-out as (key, recorded_at)."""
    return [
        (row["key"], row["recorded_at"])
        for row in conn.execute("SELECT * FROM optouts ORDER BY key")
    ]


_ROLLUP_COLUMNS = (
    "api_id",
    "reachable_30d",
    "working_30d",
    "days_observed_30d",
    "p50_ms",
    "p95_ms",
    "last_probe_at",
    "last_outcome",
    "last_status",
    "probe_target",
    "auth",
    "ratelimit_headers_seen",
    "saw_429_30d",
)


def upsert_rollups(
    conn: sqlite3.Connection, rollups: list[Rollup], *, computed_at: datetime
) -> None:
    columns = ", ".join((*_ROLLUP_COLUMNS, "computed_at"))
    marks = ", ".join("?" for _ in range(len(_ROLLUP_COLUMNS) + 1))
    updates = ", ".join(f"{c}=excluded.{c}" for c in (*_ROLLUP_COLUMNS[1:], "computed_at"))
    conn.executemany(
        f"INSERT INTO rollups ({columns}) VALUES ({marks})"
        f" ON CONFLICT(api_id) DO UPDATE SET {updates}",
        [
            (
                r.api_id,
                r.reachable_30d,
                r.working_30d,
                r.days_observed_30d,
                r.p50_ms,
                r.p95_ms,
                _iso(r.last_probe_at) if r.last_probe_at else None,
                r.last_outcome,
                r.last_status,
                r.probe_target,
                r.auth,
                json.dumps(list(r.ratelimit_headers_seen)),
                int(r.saw_429_30d),
                _iso(computed_at),
            )
            for r in rollups
        ],
    )
    conn.commit()


def _rollup_from_row(row: sqlite3.Row) -> Rollup:
    stamp = row["last_probe_at"]
    return Rollup(
        api_id=row["api_id"],
        reachable_30d=row["reachable_30d"],
        working_30d=row["working_30d"],
        days_observed_30d=row["days_observed_30d"],
        p50_ms=row["p50_ms"],
        p95_ms=row["p95_ms"],
        last_probe_at=datetime.fromisoformat(stamp) if stamp else None,
        last_outcome=row["last_outcome"],
        last_status=row["last_status"],
        probe_target=row["probe_target"],
        auth=row["auth"],
        ratelimit_headers_seen=tuple(json.loads(row["ratelimit_headers_seen"])),
        saw_429_30d=bool(row["saw_429_30d"]),
    )


def load_rollup(conn: sqlite3.Connection, api_id: str) -> Rollup | None:
    row = conn.execute("SELECT * FROM rollups WHERE api_id = ?", (api_id,)).fetchone()
    return _rollup_from_row(row) if row else None


def load_rollups(conn: sqlite3.Connection) -> list[Rollup]:
    return [_rollup_from_row(r) for r in conn.execute("SELECT * FROM rollups ORDER BY api_id")]


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


def load_api(conn: sqlite3.Connection, api_id: str) -> tuple[ApiRecord, bool] | None:
    row = conn.execute("SELECT * FROM apis WHERE id = ?", (api_id,)).fetchone()
    if row is None:
        return None
    record = ApiRecord(
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
    return record, bool(row["active"])

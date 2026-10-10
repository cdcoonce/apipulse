from datetime import UTC, datetime

import httpx

from apipulse.config import Settings
from apipulse.inventory import (
    ApiRecord,
    apply_optout,
    dedupe_by_host,
    fetch_apis_guru,
    fetch_public_apis,
    load_optout,
    load_seed,
    parse_apis_guru,
    parse_public_apis,
)
from apipulse.store import connect, load_apis, record_optouts, upsert_apis

SOURCES = ("seed", "apis.guru", "public-apis")


def _parse_sources(source: str) -> list[str]:
    names = [item.strip() for item in source.split(",")]
    for name in names:
        if name not in SOURCES:
            raise ValueError(f"unknown source: {name}")
    return names


def run(
    settings: Settings,
    *,
    source: str = "seed,apis.guru,public-apis",
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> str:
    names = _parse_sources(source)
    moment = now if now is not None else datetime.now(UTC)
    records: list[ApiRecord] = []
    counts: list[str] = []
    for name in names:
        if name == "seed":
            batch = load_seed(settings.data_dir / "seed.json")
        elif name == "apis.guru":
            batch = parse_apis_guru(fetch_apis_guru(client))
        else:
            batch = parse_public_apis(fetch_public_apis(client))
        counts.append(f"{name}={len(batch)}")
        records.extend(batch)
    optout = load_optout(settings.data_dir / "optout.json")
    kept, removed = apply_optout(dedupe_by_host(records), optout)
    conn = connect(settings)
    try:
        summary = upsert_apis(conn, kept, seen_at=moment)
        record_optouts(conn, removed, recorded_at=moment)
        active = len(load_apis(conn))
    finally:
        conn.close()
    return (
        f"inventory: {' '.join(counts)} -> {active} active, {len(removed)} removed (opt-out), "
        f"{summary.deactivated} deactivated"
    )

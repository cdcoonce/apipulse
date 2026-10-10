from datetime import UTC, datetime

from apipulse.config import Settings
from apipulse.inventory import apply_optout, dedupe_by_host, load_optout, load_seed
from apipulse.store import connect, load_apis, record_optouts, upsert_apis


def run(settings: Settings, *, source: str = "seed", now: datetime | None = None) -> str:
    if source != "seed":
        raise ValueError(f"unknown source: {source}")
    moment = now if now is not None else datetime.now(UTC)
    records = load_seed(settings.data_dir / "seed.json")
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
        f"inventory: {active} active, {len(removed)} removed (opt-out), "
        f"{summary.deactivated} deactivated"
    )

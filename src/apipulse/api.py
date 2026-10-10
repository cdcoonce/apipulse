# Field names in the /v1 payloads are a public contract.
# Any rename or removal requires a new path prefix (/v2).
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException

from apipulse.config import Settings
from apipulse.rollup import WINDOW_DAYS, empty_rollup
from apipulse.store import connect, load_api, load_rollup, read_probes


def build_detail(settings: Settings, api_id: str, *, now: datetime | None = None) -> dict | None:
    moment = now or datetime.now(UTC)
    conn = connect(settings)
    try:
        found = load_api(conn, api_id)
        rollup = load_rollup(conn, api_id) or empty_rollup(api_id)
    finally:
        conn.close()
    if found is None:
        return None
    record, active = found
    latest_per_day: dict[str, dict] = {}
    for row in read_probes(settings, since=moment - timedelta(days=WINDOW_DAYS)):
        if row.api_id == api_id:
            latest_per_day[row.probed_at.astimezone(UTC).date().isoformat()] = {
                "date": row.probed_at.astimezone(UTC).date().isoformat(),
                "outcome": row.outcome,
                "status": row.status,
                "elapsed_ms": row.elapsed_ms,
            }
    history = [latest_per_day[d] for d in sorted(latest_per_day, reverse=True)]
    last_probe = None
    if rollup.last_probe_at is not None:
        last_probe = {
            "at": rollup.last_probe_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "outcome": rollup.last_outcome,
            "status": rollup.last_status,
        }
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "tags": list(record.tags),
        "base_url": record.base_url,
        "spec_url": record.spec_url,
        "active": active,
        "probe_target": rollup.probe_target,
        "reachable_30d": rollup.reachable_30d,
        "working_30d": rollup.working_30d,
        "days_observed_30d": rollup.days_observed_30d,
        "latency": {"p50_ms": rollup.p50_ms, "p95_ms": rollup.p95_ms},
        "last_probe": last_probe,
        "history": history,
    }


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI()

    @app.get("/v1/apis/{api_id}")
    def detail(api_id: str) -> dict:
        payload = build_detail(settings, api_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown api id")
        return payload

    return app

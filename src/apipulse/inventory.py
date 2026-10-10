import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ApiRecord:
    id: str
    name: str
    description: str
    tags: tuple[str, ...]
    base_url: str
    spec_url: str | None
    declared_auth: str | None
    signup_url: str | None
    source: str


def load_seed(path: Path) -> list[ApiRecord]:
    raw = json.loads(path.read_text())
    return [
        ApiRecord(
            id=item["id"].lower(),
            name=item["name"],
            description=item.get("description", ""),
            tags=tuple(item.get("tags", ())),
            base_url=item["base_url"],
            spec_url=item.get("spec_url"),
            declared_auth=item.get("declared_auth"),
            signup_url=item.get("signup_url"),
            source=item.get("source", "seed"),
        )
        for item in raw
    ]


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def apply_optout(
    records: list[ApiRecord], optout: set[str]
) -> tuple[list[ApiRecord], list[ApiRecord]]:
    kept: list[ApiRecord] = []
    removed: list[ApiRecord] = []
    for record in records:
        if record.id in optout or host_of(record.base_url) in optout:
            removed.append(record)
        else:
            kept.append(record)
    return kept, removed


def dedupe_by_host(records: list[ApiRecord]) -> list[ApiRecord]:
    seen: set[str] = set()
    unique: list[ApiRecord] = []
    for record in records:
        host = host_of(record.base_url)
        if host not in seen:
            seen.add(host)
            unique.append(record)
    return unique


def load_optout(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {entry.lower() for entry in json.loads(path.read_text())}

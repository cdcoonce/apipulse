import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from apipulse.prober import USER_AGENT


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


APIS_GURU_INDEX_URL = "https://api.apis.guru/v2/list.json"


def parse_apis_guru(index: dict) -> list[ApiRecord]:
    records: list[ApiRecord] = []
    for key, entry in index.items():
        if not isinstance(entry, dict):
            continue
        versions = entry.get("versions")
        if not isinstance(versions, dict) or not versions:
            continue
        preferred = entry.get("preferred")
        version = versions[preferred] if preferred in versions else next(iter(versions.values()))
        if not isinstance(version, dict):
            continue
        spec_url = version.get("swaggerUrl") or version.get("openapiUrl")
        if not spec_url:
            continue
        info = version.get("info")
        if not isinstance(info, dict):
            info = {}
        records.append(
            ApiRecord(
                id=key.lower().replace(":", "-"),
                name=info.get("title") or key,
                description=(info.get("description") or "")[:500],
                tags=tuple(info.get("x-apisguru-categories", ())),
                base_url="https://" + key.split(":", 1)[0],
                spec_url=spec_url,
                declared_auth=None,
                signup_url=None,
                source="apis.guru",
            )
        )
    return records


def fetch_apis_guru(client: httpx.Client | None = None) -> dict:
    owned = client is None
    http = httpx.Client() if client is None else client
    try:
        response = http.get(APIS_GURU_INDEX_URL, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response.json()
    finally:
        if owned:
            http.close()


PUBLIC_APIS_README_URL = (
    "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
)

_LINK = re.compile(r"^\[(?P<text>.*)\]\((?P<url>[^)\s]+)\)$")


def parse_public_apis(markdown: str) -> list[ApiRecord]:
    records: list[ApiRecord] = []
    category = ""
    for line in markdown.splitlines():
        if line.startswith("### "):
            category = line[4:].strip()
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) < 3:
            continue
        match = _LINK.match(cells[0])
        if match is None:
            continue
        url = match["url"]
        if not url.startswith(("http://", "https://")):
            continue
        parts = urlsplit(url)
        auth = cells[2].replace("`", "").strip()
        records.append(
            ApiRecord(
                id=host_of(url),
                name=match["text"].strip("*_ ") or url,
                description=cells[1],
                tags=(category.lower(),) if category else (),
                base_url=f"{parts.scheme}://{(parts.hostname or '').lower()}",
                spec_url=None,
                declared_auth=None if not auth or auth.lower() == "no" else auth,
                signup_url=url,
                source="public-apis",
            )
        )
    return records


def fetch_public_apis(client: httpx.Client | None = None) -> str:
    owned = client is None
    http = httpx.Client() if client is None else client
    try:
        response = http.get(PUBLIC_APIS_README_URL, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response.text
    finally:
        if owned:
            http.close()

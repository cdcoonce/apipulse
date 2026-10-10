import json

import httpx
import yaml

from apipulse.prober import USER_AGENT

TARGET_TTL_DAYS = 30

_CHEAP_SUFFIXES = ("/health", "/status", "/ping", "/version")


def _score(path: str) -> int:
    if path == "/" or path.endswith(_CHEAP_SUFFIXES):
        return 0
    return len([s for s in path.split("/") if s])


def _is_parameterless(path: str, item: dict, operation: dict) -> bool:
    if "{" in path:
        return False
    for params in (item.get("parameters"), operation.get("parameters")):
        if not isinstance(params, list):
            continue
        for param in params:
            if (
                isinstance(param, dict)
                and param.get("required")
                and param.get("in") in ("query", "header", "path")
            ):
                return False
    return True


def _server_base(spec: dict, base_url: str) -> str:
    if "openapi" in spec:
        servers = spec.get("servers")
        first = servers[0] if isinstance(servers, list) and servers else None
        url = first.get("url") if isinstance(first, dict) else None
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url.removesuffix("/")
        return base_url.removesuffix("/")
    host = spec.get("host")
    if isinstance(host, str) and host:
        schemes = spec.get("schemes")
        scheme = schemes[0] if isinstance(schemes, list) and schemes else "https"
        base_path = spec.get("basePath")
        base_path = base_path if isinstance(base_path, str) else ""
        return f"{scheme}://{host}{base_path}".removesuffix("/")
    return base_url.removesuffix("/")


def _select(spec: object, base_url: str) -> str:
    if not isinstance(spec, dict) or not ("openapi" in spec or "swagger" in spec):
        return base_url
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return base_url
    candidates = []
    for path, item in paths.items():
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        operation = item.get("get")
        if isinstance(operation, dict) and _is_parameterless(path, item, operation):
            candidates.append(path)
    if not candidates:
        return base_url
    best = min(candidates, key=lambda p: (_score(p), len(p), p))
    return _server_base(spec, base_url) + best


def select_target(spec: object, base_url: str) -> str:
    try:
        return _select(spec, base_url)
    except Exception:
        return base_url


def _parse(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return yaml.safe_load(text)


def fetch_spec(spec_url: str, client: httpx.Client | None = None) -> dict | None:
    owns_client = client is None
    http = httpx.Client(timeout=10.0) if client is None else client
    try:
        response = http.get(spec_url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        if not response.is_success:
            return None
        parsed = _parse(response.text)
    except Exception:
        return None
    finally:
        if owns_client:
            http.close()
    return parsed if isinstance(parsed, dict) else None

import json

import httpx

from apipulse.prober import USER_AGENT
from apipulse.target_selector import fetch_spec, select_target

BASE = "https://fallback.example.com"


def _spec() -> dict:
    return {
        "openapi": "3.0.0",
        "servers": [{"url": "https://api.example.com/v2"}],
        "paths": {
            "/": {"get": {}},
            "/health": {"get": {}},
            "/users/{id}": {"get": {}},
            "/users": {"get": {"parameters": [{"name": "page", "in": "query", "required": True}]}},
        },
    }


def test_root_path_wins_and_is_joined_to_server_base():
    assert select_target(_spec(), BASE) == "https://api.example.com/v2/"


def test_without_root_the_health_path_wins():
    spec = _spec()
    del spec["paths"]["/"]
    assert select_target(spec, BASE) == "https://api.example.com/v2/health"


def test_fewest_segments_beats_deeper_paths():
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://api.example.com"}],
        "paths": {"/v1/users/list": {"get": {}}, "/items": {"get": {}}},
    }
    assert select_target(spec, BASE) == "https://api.example.com/items"


def test_only_parameterized_operations_fall_back_to_base_url():
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/users/{id}": {"get": {}},
            "/search": {"get": {"parameters": [{"name": "q", "in": "query", "required": True}]}},
        },
    }
    assert select_target(spec, BASE) == BASE


def test_swagger_2_builds_the_server_base_from_host_and_base_path():
    spec = {
        "swagger": "2.0",
        "host": "petstore.example.com",
        "basePath": "/api",
        "schemes": ["https"],
        "paths": {"/pets": {"get": {}}},
    }
    assert select_target(spec, BASE) == "https://petstore.example.com/api/pets"


def test_swagger_2_without_host_uses_base_url():
    spec = {"swagger": "2.0", "basePath": "/api", "paths": {"/pets": {"get": {}}}}
    assert select_target(spec, BASE) == BASE + "/pets"


def test_malformed_specs_return_base_url_without_raising():
    assert select_target("not a dict", BASE) == BASE
    assert select_target({"openapi": "3.0.0"}, BASE) == BASE
    assert select_target({"openapi": "3.0.0", "paths": {"/x": "junk"}}, BASE) == BASE


def test_required_header_on_path_level_params_excludes_a_path():
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://api.example.com/"}],
        "paths": {
            "/secure": {
                "parameters": [{"name": "X-Key", "in": "header", "required": True}],
                "get": {},
            },
            "/open": {
                "get": {"parameters": [{"name": "q", "in": "query", "required": False}]},
            },
        },
    }
    assert select_target(spec, BASE) == "https://api.example.com/open"


YAML_SPEC = "openapi: 3.0.0\npaths:\n  /:\n    get: {}\n"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_spec_parses_json_and_sends_user_agent():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, text=json.dumps(_spec()))

    spec = fetch_spec("https://a.example.com/openapi.json", client=_client(handler))
    assert spec == _spec()
    assert seen == [USER_AGENT]


def test_fetch_spec_falls_back_to_yaml():
    client = _client(lambda r: httpx.Response(200, text=YAML_SPEC))
    spec = fetch_spec("https://a.example.com/openapi.yaml", client=client)
    assert spec is not None and "/" in spec["paths"]


def test_fetch_spec_returns_none_on_404_non_dict_and_errors():
    assert (
        fetch_spec("https://a.example.com/x", client=_client(lambda r: httpx.Response(404))) is None
    )
    assert (
        fetch_spec(
            "https://a.example.com/x", client=_client(lambda r: httpx.Response(200, text="[]"))
        )
        is None
    )

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert fetch_spec("https://a.example.com/x", client=_client(boom)) is None

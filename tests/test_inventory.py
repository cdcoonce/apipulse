import json
from pathlib import Path

from apipulse.inventory import (
    ApiRecord,
    apply_optout,
    dedupe_by_host,
    host_of,
    load_optout,
    load_seed,
)


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data))
    return path


def test_load_seed_reads_full_records(tmp_path):
    seed = _write(
        tmp_path / "seed.json",
        [
            {
                "id": "a",
                "name": "A",
                "description": "d",
                "tags": ["x", "y"],
                "base_url": "https://a.example.com",
                "spec_url": "https://a.example.com/spec",
                "declared_auth": "none",
                "signup_url": "https://a.example.com/signup",
                "source": "manual",
            },
            {
                "id": "b",
                "name": "B",
                "description": "dd",
                "tags": ["z"],
                "base_url": "https://b.example.com",
                "spec_url": None,
                "declared_auth": None,
                "signup_url": None,
                "source": "seed",
            },
        ],
    )
    records = load_seed(seed)
    assert records[0] == ApiRecord(
        id="a",
        name="A",
        description="d",
        tags=("x", "y"),
        base_url="https://a.example.com",
        spec_url="https://a.example.com/spec",
        declared_auth="none",
        signup_url="https://a.example.com/signup",
        source="manual",
    )
    assert records[1].tags == ("z",)
    assert len(records) == 2


def test_load_seed_applies_defaults_and_lowercases_id(tmp_path):
    seed = _write(
        tmp_path / "seed.json",
        [{"id": "MiXed", "name": "M", "base_url": "https://m.example.com"}],
    )
    (rec,) = load_seed(seed)
    assert rec.id == "mixed"
    assert rec.description == ""
    assert rec.tags == ()
    assert rec.spec_url is None
    assert rec.declared_auth is None
    assert rec.signup_url is None
    assert rec.source == "seed"


def test_host_of_lowercases_and_strips_www():
    assert host_of("https://WWW.Example.com/x?y") == "example.com"


def _rec(id: str, base_url: str) -> ApiRecord:
    return ApiRecord(
        id=id,
        name=id,
        description="",
        tags=(),
        base_url=base_url,
        spec_url=None,
        declared_auth=None,
        signup_url=None,
        source="seed",
    )


def test_apply_optout_removes_by_id_and_host_in_input_order():
    by_id = _rec("gone", "https://one.example.com")
    keep = _rec("keep", "https://two.example.com")
    by_host = _rec("other", "https://www.three.example.com/v1")
    kept, removed = apply_optout([by_id, keep, by_host], {"gone", "three.example.com"})
    assert kept == [keep]
    assert removed == [by_id, by_host]


def test_dedupe_by_host_keeps_first_and_preserves_order():
    a = _rec("a", "https://x.example.com")
    b = _rec("b", "https://y.example.com")
    c = _rec("c", "https://www.x.example.com/other")
    assert dedupe_by_host([a, b, c]) == [a, b]


def test_load_optout_lowercases_and_missing_file_is_empty(tmp_path):
    assert load_optout(tmp_path / "nope.json") == set()
    path = _write(tmp_path / "optout.json", ["Foo", "Bar.Example.com"])
    assert load_optout(path) == {"foo", "bar.example.com"}

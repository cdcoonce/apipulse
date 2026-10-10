import json
from pathlib import Path

from apipulse.inventory import host_of, load_optout, load_seed, parse_public_apis

DATA = Path(__file__).parents[1] / "data"


def test_committed_seed_is_well_formed():
    records = load_seed(DATA / "seed.json")
    assert len(records) >= 6
    assert len({r.id for r in records}) == len(records)
    assert len({host_of(r.base_url) for r in records}) == len(records)
    assert all(r.id == r.id.lower() and r.description for r in records)


def test_committed_optout_is_empty():
    assert load_optout(DATA / "optout.json") == set()


def test_apis_guru_fixture_parses_with_six_keys():
    path = Path(__file__).parent / "fixtures" / "apis_guru_list.json"
    assert len(json.loads(path.read_text())) == 6


def test_public_apis_fixture_parses_to_six_unique_records():
    path = Path(__file__).parent / "fixtures" / "public_apis_readme.md"
    records = parse_public_apis(path.read_text())
    assert len(records) == 6
    assert len({r.id for r in records}) == 6

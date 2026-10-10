from pathlib import Path

from apipulse.inventory import host_of, load_optout, load_seed

DATA = Path(__file__).parents[1] / "data"


def test_committed_seed_is_well_formed():
    records = load_seed(DATA / "seed.json")
    assert len(records) >= 6
    assert len({r.id for r in records}) == len(records)
    assert len({host_of(r.base_url) for r in records}) == len(records)
    assert all(r.id == r.id.lower() and r.description for r in records)


def test_committed_optout_is_empty():
    assert load_optout(DATA / "optout.json") == set()

"""Persistence + price-override tests (each uses a temp DB, no global state)."""
from __future__ import annotations

from cnc import store
from cnc.engine import apply_overrides, load
from cnc.geometry import metrics_from_stl_bytes
from cnc.service import QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl


def _payload():
    m = metrics_from_stl_bytes(cube_stl(40.0))
    return build_quote(m, QuoteRequest(material="AL6061", quantity=10, part_name="brk"))


def test_save_and_get_roundtrip(tmp_path):
    db = tmp_path / "q.db"
    qid = store.save_quote(_payload(), path=db)
    assert qid.startswith("Q")
    got = store.get_quote(qid, path=db)
    assert got is not None
    assert got["id"] == qid
    assert got["input"]["part_name"] == "brk"


def test_list_orders_newest_first(tmp_path):
    db = tmp_path / "q.db"
    ids = [store.save_quote(_payload(), path=db) for _ in range(3)]
    rows, total = store.list_quotes(path=db)
    assert len(rows) == 3 and total == 3
    assert rows[0]["id"] == ids[-1]          # newest first
    assert rows[0]["material"] == "AL6061"
    assert rows[0]["unit_price"] > 0


def test_get_missing_quote_returns_none(tmp_path):
    assert store.get_quote("Qdeadbeef", path=tmp_path / "q.db") is None


def test_override_changes_material_price(tmp_path):
    db = tmp_path / "q.db"
    store.set_override("material", "AL6061", "price_cny_per_kg", 999.0, path=db)
    ov = store.get_overrides(path=db)
    assert ov["material"]["AL6061"]["price_cny_per_kg"] == 999.0

    shop = apply_overrides(load(), ov)
    assert shop.material("AL6061").price_cny_per_kg == 999.0
    # the override must actually move the quote's material cost up
    m = metrics_from_stl_bytes(cube_stl(40.0))
    base = build_quote(m, QuoteRequest(material="AL6061", quantity=1))
    bumped = build_quote(m, QuoteRequest(material="AL6061", quantity=1), shop=shop)
    assert bumped["quote"]["requested"]["material_cny"] > base["quote"]["requested"]["material_cny"]


def test_override_machine_rate(tmp_path):
    db = tmp_path / "q.db"
    store.set_override("machine", "mill_3axis", "rate_cny_per_hour", 120.0, path=db)
    shop = apply_overrides(load(), store.get_overrides(path=db))
    assert shop.machine("mill_3axis").rate_cny_per_hour == 120.0


def test_override_upsert_replaces(tmp_path):
    db = tmp_path / "q.db"
    store.set_override("material", "SUS304", "price_cny_per_kg", 30.0, path=db)
    store.set_override("material", "SUS304", "price_cny_per_kg", 33.0, path=db)
    ov = store.get_overrides(path=db)
    assert ov["material"]["SUS304"]["price_cny_per_kg"] == 33.0


def test_apply_overrides_ignores_unknown_fields(tmp_path):
    db = tmp_path / "q.db"
    # 'label' is not in the override allow-list and must be ignored, not crash.
    store.set_override("material", "AL6061", "price_cny_per_kg", 40.0, path=db)
    shop = apply_overrides(load(), {"material": {"AL6061": {"label": "x", "price_cny_per_kg": 40.0}}})
    assert shop.material("AL6061").price_cny_per_kg == 40.0
    assert shop.material("AL6061").label != "x"

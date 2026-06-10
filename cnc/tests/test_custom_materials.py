"""Operator-added materials: the human layer for the material catalog.

An admin can add a brand-new material (e.g. AL2024, magnesium) from the panel —
no code change, no restart. It merges into the catalog before the market/override
layers, so the new material quotes, accepts price tweaks, and reverts cleanly,
exactly like a builtin. Validation keeps a bad payload from reaching costing.
"""
from __future__ import annotations

import warnings

import pytest

from cnc.engine import apply_custom_materials, load
from cnc.engine.shopdata import validate_material


def _good(**over) -> dict:
    d = {"label": "铝 2024 (Aluminum 2024)", "category": "metal",
         "density_g_cm3": 2.78, "price_cny_per_kg": 48.0, "machinability": 1.1}
    d.update(over)
    return d


# ---- validation ----
def test_validate_material_builds_a_usable_material():
    m = validate_material("AL2024", _good(tensile_mpa=470, scrap_credit_frac=0.25,
                                          finish_ok=["bead_blast"]),
                          valid_finishes={"none", "bead_blast", "anodize_clear"})
    assert m.key == "AL2024" and m.category == "metal" and m.tensile_mpa == 470.0
    assert "none" in m.finish_ok and "bead_blast" in m.finish_ok   # none auto-added


def test_validate_material_rejects_bad_payloads():
    for bad in (
        {"key": "X", "d": _good(category="wood")},                 # bad category
        {"key": "X", "d": _good(density_g_cm3=-1)},                # non-positive
        {"key": "X", "d": _good(price_cny_per_kg=0)},
        {"key": "X", "d": {"category": "metal", "density_g_cm3": 2.7,
                           "price_cny_per_kg": 35, "machinability": 1}},  # missing label
        {"key": "bad key!", "d": _good()},                         # bad key chars
        {"key": "X", "d": _good(scrap_credit_frac=1.5)},           # out of range
    ):
        with pytest.raises(ValueError):
            validate_material(bad["key"], bad["d"])


def test_finish_ok_intersected_with_real_catalog():
    # a typo'd finish key is silently dropped, not propagated
    m = validate_material("X", _good(finish_ok=["anodize_clear", "made_up_finish"]),
                          valid_finishes={"none", "anodize_clear"})
    assert "anodize_clear" in m.finish_ok and "made_up_finish" not in m.finish_ok


def test_apply_custom_materials_merges_and_skips_corrupt():
    shop = load()
    merged = apply_custom_materials(shop, {
        "AL2024": _good(),
        "BROKEN": {"label": "x", "category": "metal"},    # missing required → skipped
    })
    assert "AL2024" in merged.materials
    assert "BROKEN" not in merged.materials                # corrupt row dropped
    assert set(load().materials) <= set(merged.materials)  # builtins preserved


# ---- end-to-end through the API ----
pytest.importorskip("httpx")
warnings.filterwarnings("ignore")
from starlette.testclient import TestClient  # noqa: E402

from cnc.api import build_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "mat.db"))
    monkeypatch.setenv("CNC_ADMIN_PASSWORD", "pw")
    from cnc import api
    api._login_fails.clear()
    return TestClient(build_app())


def _h(client):
    tok = client.post("/api/login", json={"username": "admin", "password": "pw"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_added_material_quotes_and_is_price_maintainable(client):
    h = _h(client)
    assert client.get("/api/admin/materials").status_code == 401   # admin-only
    r = client.put("/api/admin/materials", headers=h, json={
        "key": "AL2024", "label": "铝 2024", "category": "metal", "density_g_cm3": 2.78,
        "price_cny_per_kg": 48.0, "machinability": 1.1, "tensile_mpa": 470,
        "finish_ok": ["bead_blast"]})
    assert r.status_code == 200 and r.json()["edited"] is False
    # appears in the public material catalog (the quote form's dropdown source)
    assert client.get("/api/materials").json()["materials"]["AL2024"]["price_cny_per_kg"] == 48.0
    # it actually quotes
    import trimesh
    sb = trimesh.creation.box((80, 60, 25)).export(file_type="stl")
    q = client.post("/api/quote", files={"file": ("p.stl", sb)},
                    data={"params": '{"material":"AL2024","quantity":10}'})
    assert q.status_code == 200 and q.json()["quote"]["requested"]["unit_price_cny"] > 0
    # per-field price tweak (the normal price tab) works on a custom material
    assert client.put("/api/admin/price", headers=h, json={
        "kind": "material", "key": "AL2024", "field": "price_cny_per_kg", "value": 52}).status_code == 200
    assert client.get("/api/materials").json()["materials"]["AL2024"]["price_cny_per_kg"] == 52.0


def test_custom_material_collisions_and_deletion(client):
    h = _h(client)
    # cannot shadow a builtin
    assert client.put("/api/admin/materials", headers=h, json={
        "key": "AL6061", "label": "x", "category": "metal", "density_g_cm3": 2.7,
        "price_cny_per_kg": 35, "machinability": 1}).status_code == 400
    # bad payload → 400
    assert client.put("/api/admin/materials", headers=h, json={
        "key": "MG", "label": "镁", "category": "metal", "density_g_cm3": -1,
        "price_cny_per_kg": 40, "machinability": 0.9}).status_code == 400
    # add, then delete
    client.put("/api/admin/materials", headers=h, json={
        "key": "MG", "label": "镁 AZ31", "category": "metal", "density_g_cm3": 1.78,
        "price_cny_per_kg": 60, "machinability": 0.9})
    assert "MG" in client.get("/api/materials").json()["materials"]
    assert client.request("DELETE", "/api/admin/materials/MG", headers=h).json()["deleted"] == 1
    assert "MG" not in client.get("/api/materials").json()["materials"]
    # builtin cannot be deleted
    assert client.request("DELETE", "/api/admin/materials/AL6061", headers=h).status_code == 400
    # all of it is audited
    actions = [a["action"] for a in client.get("/api/admin/audit", headers=h).json()["audit"]]
    assert "save_material" in actions and "delete_material" in actions

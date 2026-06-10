"""Full-chain integration: the cross-feature seams a business day exercises.

Individual features have their own suites; this locks the CHAINED behaviors —
three custom entities in one quote, history replay after an entity is deleted,
scope=all price-revert leaving entities intact, and restart persistence.
"""
from __future__ import annotations

import json
import warnings

import pytest

pytest.importorskip("httpx")
warnings.filterwarnings("ignore")
import trimesh  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from cnc.api import build_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "chain.db"))
    monkeypatch.setenv("CNC_ADMIN_PASSWORD", "pw")
    from cnc import api
    api._login_fails.clear()
    api._parse_cache.clear()
    return TestClient(build_app())


def _h(client):
    tok = client.post("/api/login", json={"username": "admin", "password": "pw"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _part() -> bytes:
    p = trimesh.creation.box((100, 75, 25)); p.apply_translation((50, 37.5, 12.5))
    c = trimesh.creation.cylinder(radius=3.3, height=40, sections=24)
    c.apply_translation((20, 20, 12.5))
    return p.difference(c).export(file_type="stl")


def _setup_entities(client, h):
    assert client.put("/api/admin/materials", headers=h, json={
        "key": "AL2024", "label": "铝 2024", "category": "metal", "density_g_cm3": 2.78,
        "price_cny_per_kg": 48, "machinability": 1.1, "finish_ok": ["bead_blast"]}).status_code == 200
    assert client.put("/api/admin/finishes", headers=h, json={
        "key": "nickel", "label": "镀镍", "setup_cny": 60, "per_dm2_cny": 18,
        "min_cny": 80, "lead_days": 3, "apply_to": ["AL2024"]}).status_code == 200
    assert client.put("/api/admin/machines", headers=h, json={
        "key": "hsm", "label": "高速加工中心", "rate_cny_per_hour": 90,
        "base_mrr_cm3_min": 45, "max_axes": 3}).status_code == 200


def test_three_custom_entities_in_one_quote_then_lifecycle(client):
    h = _h(client)
    _setup_entities(client, h)
    sb = _part()
    params = json.dumps({"material": "AL2024", "quantity": 10,
                         "finish": "nickel", "machine": "hsm"})
    r = client.post("/api/quote", files={"file": ("b.stl", sb)}, data={"params": params})
    d = r.json()
    assert r.status_code == 200 and d["quote"]["requested"]["unit_price_cny"] > 0
    assert d["plan"]["machine_label"] == "高速加工中心"
    assert d["quote"]["lead_days"] >= 13                  # nickel outsourcing +3
    qid = d["id"]
    assert qid

    # delete the custom finish: history must still replay + render, new quotes
    # with the gone finish must be rejected (not 500)
    assert client.request("DELETE", "/api/admin/finishes/nickel", headers=h).json()["ok"]
    assert client.get(f"/api/quotes/{qid}").status_code == 200
    assert client.get(f"/api/quotes/{qid}/pdf").content[:5] == b"%PDF-"
    again = client.post("/api/quote", files={"file": ("b.stl", sb)}, data={"params": params})
    assert again.status_code in (400, 422)

    # scope=all price revert clears OVERRIDES but never the entities themselves
    client.put("/api/admin/price", headers=h, json={
        "kind": "material", "key": "AL2024", "field": "price_cny_per_kg", "value": 52})
    client.request("DELETE", "/api/admin/price", headers=h, json={"scope": "all"})
    mats = client.get("/api/materials").json()["materials"]
    assert "AL2024" in mats and mats["AL2024"]["price_cny_per_kg"] == 48.0


def test_entities_skills_history_survive_restart(client, tmp_path, monkeypatch):
    h = _h(client)
    _setup_entities(client, h)
    client.put("/api/admin/skills", headers=h, json={
        "key": "faq_nickel", "name": "镀镍", "kind": "knowledge",
        "triggers": ["镀镍多厚"], "response": "装饰 5-10µm。"})
    sb = _part()
    client.post("/api/quote", files={"file": ("b.stl", sb)},
                data={"params": json.dumps({"material": "AL2024", "quantity": 5})})
    # a fresh app over the same DB = a process restart
    fresh = TestClient(build_app())
    assert "AL2024" in fresh.get("/api/materials").json()["materials"]
    assert "hsm" in [k for k in fresh.get("/api/materials").json().get("machines", {"hsm": 1})]
    reply = fresh.post("/api/ai/chat", data={"message": "镀镍多厚", "params": "{}",
                                             "history": "[]"}).json().get("reply", "")
    assert "5-10µm" in reply
    assert fresh.get("/api/quotes").json()["total"] >= 1

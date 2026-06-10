"""AI write tools: approval gating + the approve endpoint."""
from __future__ import annotations

import warnings

import pytest

from cnc.ai.agent import run_agent
from cnc.ai.tools import AgentContext, get_tool, tool_schemas
from cnc.engine import load
from cnc.geometry import metrics_from_stl_bytes
from cnc.tests.fixtures import cube_stl


def _ctx(admin=False):
    sb = cube_stl(50.0)
    return AgentContext(shop=load(), metrics=metrics_from_stl_bytes(sb), mesh_stl=sb,
                        params={"material": "AL6061", "quantity": 5, "finish": "none"},
                        is_admin=admin)


def test_write_tools_are_admin_only_in_schema():
    assert "set_price" not in {t["name"] for t in tool_schemas(is_admin=False)}
    assert "set_price" in {t["name"] for t in tool_schemas(is_admin=True)}
    assert get_tool("set_price").requires_approval


def test_set_price_request_pends_not_executes():
    out = run_agent("把 AL6061 改价到 42", _ctx(admin=True))
    assert out["pending"] and out["pending"]["tool"] == "set_price"
    assert out["pending"]["arguments"]["value"] == 42.0
    assert not any(a["tool"] == "set_price" for a in out["actions"])   # not executed


def test_non_admin_cannot_invoke_write():
    # write tools aren't offered to non-admins, so the AI can't propose one
    out = run_agent("把 AL6061 改价到 42", _ctx(admin=False))
    assert out["pending"] is None
    assert "管理员" in out["reply"]      # help text points to admin


# ---- approve endpoint ----
pytest.importorskip("httpx")
warnings.filterwarnings("ignore")
from starlette.testclient import TestClient  # noqa: E402

from cnc.api import build_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "aiw.db"))
    monkeypatch.setenv("CNC_ADMIN_PASSWORD", "pw")
    from cnc import api
    api._login_fails.clear()
    return TestClient(build_app())


def _token(client):
    return client.post("/api/login", json={"username": "admin", "password": "pw"}).json()["token"]


def test_approve_requires_admin(client):
    r = client.post("/api/ai/approve",
                    json={"tool": "set_price", "arguments": {"key": "AL6061", "value": 40}})
    assert r.status_code == 401


def test_approve_executes_price_change(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    r = client.post("/api/ai/approve", headers=h, json={
        "tool": "set_price",
        "arguments": {"kind": "material", "key": "AL6061", "field": "price_cny_per_kg", "value": 41}})
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/api/materials").json()["materials"]["AL6061"]["price_cny_per_kg"] == 41.0


def test_approve_rejects_non_approvable_tool(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    assert client.post("/api/ai/approve", headers=h,
                       json={"tool": "get_quote", "arguments": {}}).status_code == 400


def test_approve_validates_value(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    r = client.post("/api/ai/approve", headers=h, json={
        "tool": "set_price",
        "arguments": {"kind": "material", "key": "AL6061", "field": "price_cny_per_kg", "value": -5}})
    assert r.json()["ok"] is False and r.json()["error"] == "out_of_range"


def test_ai_session_persistence(client):
    r = client.post("/api/ai/session", json={"id": "ai_x", "title": "t",
                                             "messages": [{"role": "user", "content": "hi"}]})
    assert r.json()["ok"]
    assert any(s["id"] == "ai_x" for s in client.get("/api/ai/sessions").json()["sessions"])
    got = client.get("/api/ai/session/ai_x").json()
    assert got["messages"][0]["content"] == "hi"
    assert client.get("/api/ai/session/nope").status_code == 404


def test_ai_cost_guard_requires_login_when_live(client, monkeypatch):
    # simulate a live LLM provider → chat must require a login token
    class _Live:
        name = "openai"
        available = True
    monkeypatch.setattr("cnc.ai.get_provider", lambda: _Live())
    monkeypatch.delenv("AI_PUBLIC", raising=False)
    import cnc.api as A
    A._ai_calls.clear(); A._ai_calls_day.clear()
    assert client.post("/api/ai/chat", data={"message": "hi", "params": "{}"}).status_code == 401
    # with admin token it works
    tok = _token(client)
    A._ai_calls.clear(); A._ai_calls_day.clear()
    r = client.post("/api/ai/chat", data={"message": "你好", "params": "{}"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    # AI_PUBLIC=1 opens it without login
    monkeypatch.setenv("AI_PUBLIC", "1")
    A._ai_calls.clear(); A._ai_calls_day.clear()
    assert client.post("/api/ai/chat", data={"message": "hi", "params": "{}"}).status_code == 200


def test_admin_actions_are_audited(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    client.put("/api/admin/price", headers=h, json={"kind": "material", "key": "AL6061",
                                                    "field": "price_cny_per_kg", "value": 39})
    client.post("/api/ai/approve", headers=h, json={"tool": "set_price",
        "arguments": {"kind": "material", "key": "AL6061", "field": "price_cny_per_kg", "value": 38}})
    audit = client.get("/api/admin/audit", headers=h).json()["audit"]
    actions = [a["action"] for a in audit]
    assert "set_price" in actions and any(a.startswith("ai_approve") for a in actions)
    assert all(a["actor"] == "admin" for a in audit)
    assert client.get("/api/admin/audit").status_code == 401   # admin-only


def test_audit_records_before_and_after_value(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    r1 = client.put("/api/admin/price", headers=h, json={"kind": "material", "key": "AL6061",
                                                         "field": "price_cny_per_kg", "value": 99})
    assert r1.json()["before"] == 35.0          # base default captured
    client.put("/api/admin/price", headers=h, json={"kind": "material", "key": "AL6061",
                                                    "field": "price_cny_per_kg", "value": 42})
    details = [a["detail"] for a in client.get("/api/admin/audit", headers=h).json()["audit"]]
    assert any("99→42" in d for d in details)    # before→after trail is auditable
    assert any("35→99" in d for d in details)


def test_price_revert_undoes_override(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    base = client.get("/api/materials").json()["materials"]["AL6061"]["price_cny_per_kg"]
    client.put("/api/admin/price", headers=h, json={"kind": "material", "key": "AL6061",
                                                    "field": "price_cny_per_kg", "value": 99})
    assert client.get("/api/materials").json()["materials"]["AL6061"]["price_cny_per_kg"] == 99
    r = client.request("DELETE", "/api/admin/price", headers=h,
                       json={"kind": "material", "key": "AL6061", "field": "price_cny_per_kg"})
    assert r.json()["ok"] and r.json()["reverted"] == 1
    assert client.get("/api/materials").json()["materials"]["AL6061"]["price_cny_per_kg"] == base
    assert client.request("DELETE", "/api/admin/price", json={"scope": "all"}).status_code == 401


def test_admin_config_exposes_full_maintainable_field_set(client):
    # every whitelisted field must be visible in the config so the panel can edit it
    h = {"Authorization": f"Bearer {_token(client)}"}
    cfg = client.get("/api/admin/config", headers=h).json()
    al = cfg["materials"]["AL6061"]
    assert {"tensile_mpa", "stock_lead_days", "machinability"} <= set(al)
    assert "lead_days" in cfg["finishes"]["anodize_clear"]
    assert "max_axes" in cfg["machines"]["mill_3axis"]
    for biz_field in ("daily_capacity_hours", "tool_wear_cny_per_hour", "crate_cny"):
        assert biz_field in cfg["business"]
    assert "inspection_min_per_feature" in cfg["capp"]


def test_admin_config_and_business_finish_overrides(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    cfg = client.get("/api/admin/config", headers=h).json()
    assert {"materials", "machines", "finishes", "business"} <= set(cfg)
    assert "margin" in cfg["business"] and "anodize_clear" in cfg["finishes"]
    # finishing cost is now runtime-maintainable
    assert client.put("/api/admin/price", headers=h, json={
        "kind": "finish", "key": "bead_blast", "field": "per_dm2_cny", "value": 12}).status_code == 200
    # business params (margin/tax/deburr) are runtime-maintainable and audited
    assert client.put("/api/admin/price", headers=h, json={
        "kind": "business", "key": "", "field": "margin", "value": 0.42}).status_code == 200
    assert client.get("/api/admin/config", headers=h).json()["business"]["margin"] == 0.42
    # non-overridable fields rejected
    assert client.put("/api/admin/price", headers=h, json={
        "kind": "business", "key": "", "field": "quantity_breaks", "value": 5}).status_code == 400
    assert client.get("/api/admin/config").status_code == 401   # admin-only


def test_all_process_params_maintainable(client):
    h = {"Authorization": f"Bearer {_token(client)}"}
    cfg = client.get("/api/admin/config", headers=h).json()
    assert {"tiers", "capp", "cutting"} <= set(cfg)
    assert "lead_time_tiers" in cfg["tiers"] and "AL6061" in cfg["cutting"]
    P = lambda **b: client.put("/api/admin/price", headers=h, json=b).status_code
    assert P(kind="business", key="", field="lead_time_tiers.express.factor", value=1.5) == 200
    assert P(kind="business", key="", field="tolerance_classes.ultra.machining_factor", value=1.4) == 200
    assert P(kind="capp", key="", field="programming_min_base", value=40) == 200
    assert P(kind="cutting", key="AL6061", field="vc_rough", value=400) == 200
    assert P(kind="material", key="AL6061", field="form_factor", value=2.0) == 200
    # invalid paths rejected
    assert P(kind="business", key="", field="lead_time_tiers.express.label", value=1) == 400
    assert P(kind="capp", key="", field="margin", value=1) == 400
    assert P(kind="cutting", key="AL6061", field="bogus", value=1) == 400


def test_cutting_override_flows_into_quote(client):
    # the estimator no longer reads the DB itself — the API layer must thread
    # overridden feeds into the plan, or this silently regresses.
    import trimesh
    h = {"Authorization": f"Bearer {_token(client)}"}
    sb = trimesh.creation.box((60, 40, 20)).export(file_type="stl")
    quote = lambda: client.post("/api/quote", files={"file": ("p.stl", sb)},
                                data={"params": '{"material":"AL6061","quantity":5}'}).json()
    base = quote()["plan"]["times"]["roughing_min"]
    client.put("/api/admin/price", headers=h, json={
        "kind": "cutting", "key": "AL6061", "field": "vc_rough", "value": 30})  # crawl speed
    slow = quote()["plan"]["times"]["roughing_min"]
    assert slow > base * 2          # slower cutting speed → much longer roughing


def test_drill_cycle_params_maintainable_via_tools_pseudokey(client):
    # all drill/peck/tap cycle constants live in cutting.json tools — and are
    # runtime-maintainable under kind="cutting", key="tools" (no hardcoding).
    h = {"Authorization": f"Bearer {_token(client)}"}
    cfg = client.get("/api/admin/config", headers=h).json()
    assert {"peck_trigger_ratio", "peck_depth_ratio", "deep_feed_derate",
            "peck_overhead_s", "point_allowance_ratio",
            "hole_approach_s"} <= set(cfg["cutting"]["tools"])
    r = client.put("/api/admin/price", headers=h, json={
        "kind": "cutting", "key": "tools", "field": "peck_overhead_s", "value": 2.0})
    assert r.status_code == 200 and r.json()["before"] == 0.4   # audited before-value
    assert client.get("/api/admin/config", headers=h).json()["cutting"]["tools"]["peck_overhead_s"] == 2.0
    # whitelist enforced; revert restores the default
    assert client.put("/api/admin/price", headers=h, json={
        "kind": "cutting", "key": "tools", "field": "bogus", "value": 1}).status_code == 400
    client.request("DELETE", "/api/admin/price", headers=h,
                   json={"kind": "cutting", "key": "tools", "field": "peck_overhead_s"})
    assert client.get("/api/admin/config", headers=h).json()["cutting"]["tools"]["peck_overhead_s"] == 0.4

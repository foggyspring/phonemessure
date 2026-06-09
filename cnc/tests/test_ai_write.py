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

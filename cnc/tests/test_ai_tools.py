"""AI tool registry + dispatch against an AgentContext."""
from __future__ import annotations

from cnc.ai.tools import AgentContext, dispatch, get_tool, tool_schemas
from cnc.engine import load
from cnc.geometry import metrics_from_stl_bytes
from cnc.tests.fixtures import cube_stl


def _ctx(**kw):
    sb = cube_stl(50.0)
    base = dict(shop=load(), metrics=metrics_from_stl_bytes(sb), mesh_stl=sb,
                params={"material": "AL6061", "quantity": 5, "finish": "none"})
    base.update(kw)
    return AgentContext(**base)


def test_read_tools_registered():
    names = {t["name"] for t in tool_schemas()}
    assert {"get_quote", "compare_materials", "suggest_cheaper_material", "analyze_dfm"} <= names
    # every schema is a JSON-schema object
    assert all(t["parameters"]["type"] == "object" for t in tool_schemas())


def test_get_quote_overrides_material_and_qty():
    r = dispatch("get_quote", {"material": "SUS304", "quantity": 50}, _ctx())
    assert "SUS304" in r["data"].__str__() or "不锈钢" in r["summary"]
    assert r["data"]["unit_price_cny"] > 0 and r["data"]["lead_days"] == 10


def test_compare_returns_sorted_rows():
    r = dispatch("compare_materials", {}, _ctx())
    prices = [x["unit_price_cny"] for x in r["data"]]
    assert prices == sorted(prices)


def test_dfm_tool_returns_findings_list():
    r = dispatch("analyze_dfm", {}, _ctx())
    assert isinstance(r["data"], list)


def test_no_part_context_is_handled():
    r = dispatch("get_quote", {}, AgentContext(shop=load(), params={"material": "AL6061"}))
    assert r.get("error") == "no_part"


def test_unknown_tool():
    assert dispatch("nope", {}, _ctx())["error"] == "unknown_tool"


def test_admin_tools_hidden_without_admin():
    # no admin tools registered yet, but the filter must not expose admin ones
    assert all(not get_tool(t["name"]).admin for t in tool_schemas(is_admin=False))

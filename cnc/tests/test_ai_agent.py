"""Agent loop: provider ↔ tools ↔ summary."""
from __future__ import annotations

from cnc.ai.agent import run_agent
from cnc.ai.tools import AgentContext
from cnc.engine import load
from cnc.geometry import metrics_from_stl_bytes
from cnc.tests.fixtures import cube_stl


def _ctx(admin=False):
    sb = cube_stl(50.0)
    return AgentContext(shop=load(), metrics=metrics_from_stl_bytes(sb), mesh_stl=sb,
                        params={"material": "AL6061", "quantity": 5, "finish": "none"},
                        is_admin=admin)


def test_quote_request_runs_tool_and_replies():
    out = run_agent("SUS304 50件多少钱", _ctx())
    assert out["actions"] and out["actions"][0]["tool"] == "get_quote"
    assert "169" in out["actions"][0]["summary"] or "¥" in out["reply"]
    assert out["pending"] is None and out["provider"] == "mock"


def test_analyze_runs_multiple_tools():
    out = run_agent("帮我分析这个零件", _ctx())
    tools = [a["tool"] for a in out["actions"]]
    assert "get_quote" in tools and "analyze_dfm" in tools
    assert out["reply"].startswith("✅")


def test_history_is_threaded():
    out = run_agent("报价", _ctx(), history=[{"role": "user", "content": "hi"},
                                            {"role": "assistant", "content": "你好"}])
    assert out["history"][0]["content"] == "hi"
    assert out["history"][-1]["role"] == "assistant"


def test_chitchat_no_tools():
    out = run_agent("你好", _ctx())
    assert not out["actions"] and "报价助手" in out["reply"]


def test_analyze_part_report():
    from cnc.ai.agent import analyze_part
    r = analyze_part(_ctx())
    tools = [s["tool"] for s in r["sections"]]
    assert tools == ["get_quote", "analyze_dfm", "compare_materials", "suggest_cheaper_material"]
    assert "置信度" in r["recommendation"]

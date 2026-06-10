"""AI skill library: a default, human-maintainable capability/knowledge base.

Covers (a) the default library shape, (b) the MockProvider being driven by it
(not hardcoded intents), (c) human intervention — edit triggers, disable a
skill, add a custom FAQ — and (d) the admin CRUD endpoints with audit.
"""
from __future__ import annotations

import warnings

import pytest

from cnc.ai import skills
from cnc.ai.provider import MockProvider
from cnc.ai.skills import load_skills, match, validate_custom

_TOOLS = [{"name": n} for n in (
    "get_quote", "compare_materials", "suggest_cheaper_material", "analyze_dfm",
    "explain_quote", "set_price", "record_actual_time")]


def _ask(text, tools=_TOOLS):
    return MockProvider().chat([{"role": "user", "content": text}], tools)


# ---- default library ----
def test_default_library_has_actions_and_knowledge():
    lib = load_skills()
    kinds = {s["kind"] for s in lib}
    assert {"action", "analyze", "knowledge"} <= kinds
    # the core capabilities exist as skills, not hardcoded
    keys = {s["key"] for s in lib}
    assert {"quote", "dfm", "compare", "cheaper", "explain", "analyze",
            "set_price"} <= keys
    # at least a few curated FAQ knowledge entries
    assert sum(1 for s in lib if s["kind"] == "knowledge") >= 5
    # sorted by descending priority
    pr = [s["priority"] for s in lib]
    assert pr == sorted(pr, reverse=True)


def test_provider_is_driven_by_skill_library():
    # the same behavior the old hardcoded intents gave — now from data
    t = _ask("SUS304 这个零件 50 件多少钱")
    names = [c.name for c in t.tool_calls]
    assert "get_quote" in names
    assert t.tool_calls[0].arguments.get("material") == "SUS304"
    assert t.tool_calls[0].arguments.get("quantity") == 50


def test_knowledge_skill_answers_without_a_tool():
    t = _ask("公差怎么选比较好")
    assert not t.tool_calls and t.done
    assert "±0.1" in t.text          # the curated tolerance FAQ fired


def test_disabled_skill_stops_matching():
    lib = load_skills({"dfm": {"enabled": False}})
    sel, _ = match("有什么工艺风险", lib, tool_names={"analyze_dfm"}, is_admin=False)
    assert not any(s["key"] == "dfm" for s in sel)


def test_custom_trigger_word_routes_to_builtin():
    lib = load_skills({"quote": {"triggers": ["几多钱"]}})
    sel, _ = match("这个几多钱", lib, tool_names={"get_quote"}, is_admin=False)
    assert [s["key"] for s in sel] == ["quote"]
    # the old default trigger no longer matches (it was replaced)
    sel2, _ = match("多少钱", lib, tool_names={"get_quote"}, is_admin=False)
    assert not sel2


def test_add_custom_knowledge_skill():
    custom = validate_custom({"key": "faq_lead", "name": "交期", "kind": "knowledge",
                              "triggers": ["多久能做好"], "response": "标准约10天。"})
    lib = load_skills({"faq_lead": custom})
    sel, _ = match("这个多久能做好", lib, tool_names=set(), is_admin=False)
    assert sel and sel[0]["response"] == "标准约10天。"


def test_admin_only_skill_hidden_from_non_admin():
    lib = load_skills()
    sel_user, _ = match("帮我改价到 40", lib, tool_names={"set_price"}, is_admin=False)
    assert not any(s["key"] == "set_price" for s in sel_user)
    sel_admin, _ = match("帮我改价到 40", lib, tool_names={"set_price"}, is_admin=True)
    assert any(s["key"] == "set_price" for s in sel_admin)


def test_validate_custom_rejects_bad_payloads():
    with pytest.raises(ValueError):
        validate_custom({"key": "quote", "kind": "knowledge", "triggers": ["x"], "response": "y"})
    with pytest.raises(ValueError):
        validate_custom({"key": "x", "kind": "bogus", "triggers": ["x"]})
    with pytest.raises(ValueError):
        validate_custom({"key": "x", "kind": "knowledge", "triggers": []})
    with pytest.raises(ValueError):
        validate_custom({"key": "x", "kind": "action", "triggers": ["x"], "action": ""})


def test_override_only_patches_editable_fields_on_builtin():
    # a malicious override trying to rebind a builtin's action is ignored
    lib = load_skills({"quote": {"action": "set_price", "name": "盗版报价"}})
    q = next(s for s in lib if s["key"] == "quote")
    assert q["action"] == "get_quote"     # action not editable on builtins
    assert q["name"] == "盗版报价"          # name is editable


# ---- admin CRUD endpoint ----
pytest.importorskip("httpx")
warnings.filterwarnings("ignore")
from starlette.testclient import TestClient  # noqa: E402

from cnc.api import build_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "sk.db"))
    monkeypatch.setenv("CNC_ADMIN_PASSWORD", "pw")
    from cnc import api
    api._login_fails.clear()
    return TestClient(build_app())


def _tok(client):
    return client.post("/api/login", json={"username": "admin", "password": "pw"}).json()["token"]


def test_skill_crud_requires_admin_and_audits(client):
    assert client.get("/api/admin/skills").status_code == 401
    h = {"Authorization": f"Bearer {_tok(client)}"}
    assert client.get("/api/admin/skills", headers=h).json()["skills"]
    # edit a builtin trigger
    assert client.put("/api/admin/skills", headers=h,
                      json={"key": "quote", "triggers": ["报价", "几多钱"]}).status_code == 200
    # add a custom knowledge skill
    assert client.put("/api/admin/skills", headers=h, json={
        "key": "faq_x", "name": "X", "kind": "knowledge",
        "triggers": ["xyz"], "response": "hi"}).status_code == 200
    lib = {s["key"]: s for s in client.get("/api/admin/skills", headers=h).json()["skills"]}
    assert lib["quote"]["triggers"] == ["报价", "几多钱"] and "faq_x" in lib
    # bad payload rejected
    assert client.put("/api/admin/skills", headers=h,
                      json={"key": "bad", "kind": "weird", "triggers": ["x"]}).status_code == 400
    # delete custom + revert builtin
    assert client.request("DELETE", "/api/admin/skills/faq_x", headers=h).json()["ok"]
    client.request("DELETE", "/api/admin/skills/quote", headers=h)
    lib2 = {s["key"]: s for s in client.get("/api/admin/skills", headers=h).json()["skills"]}
    assert "faq_x" not in lib2 and "多少钱" in lib2["quote"]["triggers"]
    actions = [a["action"] for a in client.get("/api/admin/audit", headers=h).json()["audit"]]
    assert "save_skill" in actions and "delete_skill" in actions


def test_enriched_knowledge_entries_route_correctly():
    # the researched entries (ISO 2768 / corner-pocket / tap-drill / Ti / SS /
    # anodize / cost-design / roughness) must answer directly with their facts
    cases = {
        "未注公差按什么标准": "ISO 2768",
        "内角R角要多大": "1/3",
        "M6攻丝底孔打多大": "Ø5",
        "钛合金为什么贵": "6.7",          # thermal conductivity fact
        "不锈钢难加工吗": "加工硬化",
        "阳极氧化会影响尺寸吗": "MIL-A-8625",
        "怎么设计更省成本": "20-40%",
        "粗糙度Ra选多少": "3.2",
    }
    for q, fact in cases.items():
        t = _ask(q)
        assert not t.tool_calls and fact in t.text, (q, t.text[:50])


def test_specific_faq_outranks_generic_explain_but_not_vice_versa():
    # "钛合金为什么贵" contains 为什么 (explain trigger, prio 70) — the more
    # specific titanium FAQ (prio 75) must win; a generic "为什么这个价" must
    # still go to the explain tool, not any FAQ.
    t = _ask("钛合金为什么贵")
    assert not t.tool_calls and "导热" in t.text
    t2 = _ask("为什么这个价格这么高")
    assert [c.name for c in t2.tool_calls] == ["explain_quote"]


def test_round2_knowledge_entries_route_correctly():
    # GD&T / ISO 286 fits / plastics / heat-treat / reaming / edge-break
    cases = {
        "位置度和平面度怎么选": "57%",
        "轴和孔用什么配合": "H7/g6",
        "POM还是尼龙好": "0.22%",
        "T651和T6什么区别": "T651",
        "H7的孔怎么加工": "铰",
        "图纸上倒角怎么标": "C0.5",
    }
    for q, fact in cases.items():
        t = _ask(q)
        assert not t.tool_calls and fact in t.text, (q, t.text[:50])
    # specific hole-machining intent outranks the fits table when both match
    t = _ask("H7的孔怎么加工")
    assert "钻孔" in t.text and "铰" in t.text


def test_round3_knowledge_entries_route_correctly():
    # turning L/D / thread-mill-vs-tap / counterbore / metrology 10:1
    cases = {
        "细长轴怎么车": "L/D",
        "盲孔螺纹老断丝锥怎么办": "螺纹铣",
        "M6沉头孔多大": "Ø11",
        "卡尺能测±0.05吗": "10:1",
    }
    for q, fact in cases.items():
        t = _ask(q)
        assert not t.tool_calls and fact in t.text, (q, t.text[:50])
    # quote intents with bare 车削/攻丝 words still go to the quote tool
    for q in ("这个车削件多少钱", "攻丝4个孔报价"):
        t = _ask(q)
        assert any(c.name == "get_quote" for c in t.tool_calls), q

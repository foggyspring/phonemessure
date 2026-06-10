"""Guided research flow: the copilot as an analytical partner.

The flow must (a) ask engineer-grade clarifying questions, (b) carry the
clarified constraints into later conclusions (load-bearing rejects weak subs),
(c) find the quantity price elbow, (d) end in a decision brief — all
deterministic offline (MockProvider-independent: the flow drives tools itself).
"""
from __future__ import annotations

import warnings

import pytest

from cnc.ai.research import STAGES, new_state, step
from cnc.ai.tools import AgentContext
from cnc.engine import load
from cnc.geometry import metrics_from_stl_bytes
from cnc.tests.fixtures import cube_stl


def _ctx(material="AL7075", qty=10, **extra):
    sb = cube_stl(60.0)
    return AgentContext(shop=load(), metrics=metrics_from_stl_bytes(sb), mesh_stl=sb,
                        params={"material": material, "quantity": qty, "finish": "none", **extra})


def _run_flow(answers="是承力件，本批20件，30天内要", ctx=None):
    ctx = ctx or _ctx()
    st = None
    outs = [step("开始研究", st, ctx)]
    st = outs[-1]["state"]
    for msg in (answers, "继续", "继续", "继续", "继续"):
        outs.append(step(msg, st, ctx))
        st = outs[-1]["state"]
        if outs[-1]["done"]:
            break
    return outs


def test_clarify_asks_engineer_questions_then_parses_answers():
    ctx = _ctx()
    first = step("开始研究", None, ctx)
    assert first["stage"] == "clarify"
    assert "承力" in first["reply"] and "数量" in first["reply"] and "交期" in first["reply"]
    second = step("是承力件，本批20件，30天内要", first["state"], ctx)
    a = second["state"]["answers"]
    assert a["load_bearing"] is True and a["batch"] == 20 and a["deadline_days"] == 30
    assert second["stage"] == "geometry"          # advanced once fully clarified


def test_skip_uses_defaults_with_explicit_assumptions():
    ctx = _ctx(qty=15)
    first = step("开始研究", None, ctx)
    second = step("跳过", first["state"], ctx)
    a = second["state"]["answers"]
    assert a["load_bearing"] is True and a["batch"] == 15   # falls back to params qty
    assert "默认假设" in second["state"]["findings"]["clarify"]


def test_load_bearing_constraint_changes_material_conclusion():
    # AL7075 load-bearing: the cheaper AL6061 (54% strength) must be REJECTED;
    # as a cosmetic part the same substitution is allowed. The clarified
    # constraint must change the conclusion — that's what makes it a partner.
    structural = _run_flow("是承力件，本批20件，30天内要")
    mat_s = next(o for o in structural if o["state"]["findings"].get("material"))
    assert "强度不足" in mat_s["state"]["findings"]["material"]["verdict"] \
        or mat_s["state"]["findings"]["material"]["dropped"]

    cosmetic = _run_flow("外观件，本批20件，30天内要")
    mat_c = next(o for o in cosmetic if o["state"]["findings"].get("material"))
    assert "省约" in mat_c["state"]["findings"]["material"]["verdict"]


def test_quantity_stage_finds_price_elbow_and_checks_deadline():
    outs = _run_flow("承力件，本批20件，30天内要")
    qf = next(o["state"]["findings"]["quantity"] for o in outs
              if o["state"]["findings"].get("quantity"))
    assert len(qf["curve"]) >= 3
    prices = [p for _, p in qf["curve"]]
    assert prices == sorted(prices, reverse=True)      # unit price falls with qty
    assert qf["elbow"] in [q for q, _ in qf["curve"]]
    assert qf["deadline"] == 30 and qf["lead_days"] is not None


def test_flow_ends_in_decision_brief_with_all_sections():
    outs = _run_flow()
    last = outs[-1]
    assert last["done"] and last["stage"] == "brief"
    for kw in ("决策简报", "研究前提", "几何与工艺", "选材结论", "批量与交期", "建议行动"):
        assert kw in last["reply"]
    # progress strip data is complete and ordered
    assert [s["key"] for s in last["progress"]["stages"]] == STAGES
    assert last["progress"]["current"] == len(STAGES)


def test_restart_resets_the_flow():
    ctx = _ctx()
    outs = _run_flow(ctx=ctx)
    again = step("重新开始研究", outs[-1]["state"], ctx)
    assert again["stage"] == "clarify" and again["state"]["findings"] == {}


def test_flow_without_part_degrades_gracefully():
    ctx = AgentContext(shop=load(), metrics=None, params={"material": "AL6061", "quantity": 5})
    st = step("开始研究", None, ctx)["state"]
    st = step("跳过", st, ctx)["state"]
    out = step("继续", st, ctx)          # geometry stage with no part
    assert "上传" in out["reply"]        # asks for a part instead of crashing
    assert out["stage"] == "geometry"    # stays put, retry offered


# ---- API endpoint ----
pytest.importorskip("httpx")
warnings.filterwarnings("ignore")
from starlette.testclient import TestClient  # noqa: E402

from cnc.api import build_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CNC_DB", str(tmp_path / "res.db"))
    monkeypatch.setenv("CNC_ADMIN_PASSWORD", "pw")
    from cnc import api
    api._login_fails.clear()
    return TestClient(build_app())


def test_research_endpoint_carries_state_across_turns(client):
    import json

    import trimesh
    sb = trimesh.creation.box((60, 50, 20)).export(file_type="stl")
    r1 = client.post("/api/ai/research", files={"file": ("p.stl", sb)},
                     data={"message": "开始研究", "params": '{"material":"AL6061","quantity":10}',
                           "state": "{}"})
    assert r1.status_code == 200 and r1.json()["stage"] == "clarify"
    r2 = client.post("/api/ai/research", files={"file": ("p.stl", sb)},
                     data={"message": "外观件 30件 20天",
                           "params": '{"material":"AL6061","quantity":10}',
                           "state": json.dumps(r1.json()["state"])})
    d = r2.json()
    assert d["stage"] == "geometry" and d["state"]["answers"]["batch"] == 30
    assert d["next_steps"]                      # guidance chips always offered


def test_skip_word_does_not_discard_explicit_info_in_same_message():
    # "是外观件，数量不知道" — the skip-word must only fill the REMAINING
    # blanks; the explicit 外观件 must survive (it flips the material verdict).
    ctx = _ctx()
    st = step("开始研究", None, ctx)["state"]
    out = step("是外观件，数量不知道，按默认吧", st, ctx)
    assert out["state"]["answers"]["load_bearing"] is False


def test_bare_continue_at_clarify_advances_with_defaults():
    # typing 继续 instead of clicking the 跳过 chip must not re-ask forever
    ctx = _ctx()
    st = step("开始研究", None, ctx)["state"]
    out = step("继续", st, ctx)
    assert out["stage"] == "geometry"


def test_tampered_state_never_crashes():
    # the state round-trips through the client — malformed structures must be
    # sanitized, not 500 (a tampered curve used to crash the brief stage)
    ctx = _ctx()
    hostile = [
        {"stage": "evil", "answers": {}, "findings": {}},
        {"stage": "clarify", "answers": [1, 2], "findings": {}},
        {"stage": "quantity", "answers": {"batch": "abc"}, "findings": {}},
        {"stage": "brief", "answers": {}, "findings": {"quantity": {"curve": "notalist"}}},
        {"stage": "brief", "answers": {},
         "findings": {"quantity": {"curve": [[1], "x", [2, "y"], [3, 4.5]]}}},
        {"stage": "brief", "answers": None, "findings": None},
        [1, 2, 3],
    ]
    for st in hostile:
        out = step("继续", st, ctx)          # must not raise
        assert out["stage"] in STAGES


def test_deadline_conflict_with_outsourced_finish_warns_rush():
    # anodize adds outsourced days; a 5-day hard deadline must trigger the
    # rush-tier recommendation in both the quantity stage and the brief
    ctx = _ctx(material="AL6061", finish="anodize_clear")
    st = None
    outs = [step("开始研究", st, ctx)]
    for m in ("承力件 10件 5天内要", "继续", "继续", "继续", "继续"):
        outs.append(step(m, outs[-1]["state"], ctx))
        if outs[-1]["done"]:
            break
    qstage = next(o for o in outs if o["state"]["findings"].get("quantity"))
    assert "超出约束" in qstage["reply"] and "加急" in qstage["reply"]
    assert "加急" in outs[-1]["reply"]      # brief carries the action item

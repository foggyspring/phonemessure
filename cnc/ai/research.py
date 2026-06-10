"""Guided research flow — the copilot as an analytical PARTNER, not a Q&A box.

Instead of answering isolated questions, the assistant drives a structured
sourcing study over five stages, the way a manufacturing engineer would:

    clarify   需求澄清    — load-bearing? batch size? hard deadline?
    geometry  几何/DFM    — run the quote + DFM review, surface risks
    material  选材权衡    — trade study constrained by the clarified needs
    quantity  批量/交期   — find the price elbow, pick a delivery plan
    brief     决策简报    — synthesize everything into a decision document

Design constraints (same as the rest of the copilot):
  * Provider-independent: the flow engine is a deterministic state machine over
    the existing tool registry, so it works fully offline with the MockProvider
    and is unit-testable. A real LLM (at launch) can re-narrate each stage's
    reply but never owns the state.
  * Stateless server: the flow state travels with the request (like chat
    history) and is persisted client-side in the AI session.

Each step returns {reply, stage, progress, next_steps, state, done}; next_steps
are clickable suggestion chips — the "guidance" half of the partnership.
"""
from __future__ import annotations

import re

from .tools import AgentContext, dispatch

STAGES = ["clarify", "geometry", "material", "quantity", "brief"]
STAGE_LABELS = {"clarify": "需求澄清", "geometry": "几何/DFM", "material": "选材权衡",
                "quantity": "批量/交期", "brief": "决策简报"}

_SKIP = re.compile(r"跳过|不知道|不确定|skip|默认", re.I)


def new_state() -> dict:
    return {"stage": "clarify", "answers": {}, "findings": {}}


def _parse_clarify(text: str, answers: dict) -> dict:
    """Pull the three study constraints out of free text (deterministic; a real
    LLM can map richer phrasing onto the same keys at launch)."""
    low = text.lower()
    if "load_bearing" not in answers:
        if re.search(r"承力|受力|结构件|load", low):
            answers["load_bearing"] = not re.search(r"不承力|非承力|不受力|外观件", low)
        elif re.search(r"外观|装饰|不承力|非承力", low):
            answers["load_bearing"] = False
    m = re.search(r"(\d+)\s*(?:件|个|pcs|套)", text)
    if m and "batch" not in answers:
        answers["batch"] = int(m.group(1))
    m = re.search(r"(\d+)\s*(?:天|日|days?)", text)
    if m and "deadline_days" not in answers:
        answers["deadline_days"] = int(m.group(1))
    return answers


def _clarify_questions(answers: dict) -> list[str]:
    qs = []
    if "load_bearing" not in answers:
        qs.append("这是承力/结构件还是外观件？（决定能否用更弱但更省的材料）")
    if "batch" not in answers:
        qs.append("本批数量大约多少件？（决定编程摊销与价格甜点）")
    if "deadline_days" not in answers:
        qs.append("交期有硬约束吗？大约几天内要货？")
    return qs


def step(message: str, state: dict | None, ctx: AgentContext) -> dict:
    """Advance the research flow by one turn. Pure function of (message, state,
    part context) — no hidden server state."""
    if re.search(r"重新开始|重新研究|restart", message or "", re.I):
        state = new_state()
        message = ""
    state = dict(state or new_state())
    state.setdefault("answers", {})
    state.setdefault("findings", {})
    stage = state.get("stage") or "clarify"
    if stage not in STAGES:
        stage = "clarify"

    if stage == "clarify":
        if not _SKIP.search(message or ""):
            _parse_clarify(message or "", state["answers"])
        open_qs = [] if _SKIP.search(message or "") else _clarify_questions(state["answers"])
        if open_qs:
            state["stage"] = "clarify"
            return _resp(state,
                         reply="开始研究前，先确认几个影响结论的关键点：\n"
                               + "\n".join(f"· {q}" for q in open_qs)
                               + "\n（可一句话一起回答，或回复“跳过”按默认假设继续）",
                         next_steps=[{"label": "跳过，按默认继续", "message": "跳过"}])
        a = state["answers"]
        assumed = []
        if "load_bearing" not in a:
            a["load_bearing"] = True
            assumed.append("按承力件保守处理")
        if "batch" not in a:
            a["batch"] = int(ctx.params.get("quantity") or 10)
            assumed.append(f"批量按当前参数 {a['batch']} 件")
        if "deadline_days" not in a:
            a["deadline_days"] = None
            assumed.append("交期无硬约束")
        state["findings"]["clarify"] = (
            f"{'承力件' if a['load_bearing'] else '外观件'}；批量 {a['batch']} 件；"
            + (f"交期 ≤{a['deadline_days']} 天" if a.get("deadline_days") else "交期无硬约束")
            + (f"（默认假设：{'、'.join(assumed)}）" if assumed else ""))
        state["stage"] = "geometry"
        return _resp(state,
                     reply=f"已记录研究前提：{state['findings']['clarify']}\n"
                           "下一步做几何与可加工性评审。",
                     next_steps=[{"label": "开始几何/DFM 评审", "message": "继续"}])

    if stage == "geometry":
        q = dispatch("get_quote", {"quantity": state["answers"].get("batch")}, ctx)
        d = dispatch("analyze_dfm", {}, ctx)
        if q.get("error"):
            return _resp(state, reply=q["summary"], next_steps=[
                {"label": "重试", "message": "继续"}])
        risks = [x for x in (d.get("data") or []) if x.get("severity") in ("high", "medium")]
        qd = q.get("data") or {}
        conf = (qd.get("confidence") or {})
        lines = [f"几何/DFM 评审完成：{q.get('summary', '')}"]
        if risks:
            lines.append(f"发现 {len(risks)} 项需关注的工艺风险：" +
                         "、".join(r["title"] for r in risks[:4]))
        else:
            lines.append("无明显可加工性风险。")
        if conf:
            lines.append(f"报价置信度 {conf.get('score')}/100（{conf.get('level')}）。")
        state["findings"]["geometry"] = {
            "summary": q.get("summary", ""), "risks": [r["title"] for r in risks],
            "confidence": conf.get("score"), "unit_price": _unit_price(qd),
            "lead_days": _lead_days(qd)}
        state["stage"] = "material"
        lines.append("下一步做选材权衡——结合"
                     + ("承力要求（剔除强度不足的替代）" if state["answers"].get("load_bearing")
                        else "外观件定位（可大胆用更省材料）") + "。")
        return _resp(state, reply="\n".join(lines),
                     next_steps=[{"label": "开始选材权衡", "message": "继续"},
                                 {"label": "先细看 DFM 风险", "message": "DFM 风险详情"}])

    if stage == "material":
        sugg = dispatch("suggest_cheaper_material", {}, ctx)
        cands = list(sugg.get("data") or [])
        load_bearing = bool(state["answers"].get("load_bearing"))
        if load_bearing:
            kept = [c for c in cands if c.get("strength_ok", True)]
            dropped = [c for c in cands if not c.get("strength_ok", True)]
        else:
            kept, dropped = cands, []
        if kept:
            top = kept[0]
            verdict = (f"推荐替代：{top['label']}，省约 {top['savings_pct']}%"
                       + ("（强度等效，承力可用）" if load_bearing else ""))
        else:
            verdict = ("当前材料即为合理选择" +
                       ("（更省的替代均强度不足，承力件不建议）" if dropped else "，未发现更省替代"))
        state["findings"]["material"] = {
            "verdict": verdict,
            "dropped": [f"{c['label']}（强度仅 {int((c.get('strength_ratio') or 0)*100)}%）"
                        for c in dropped[:3]]}
        state["stage"] = "quantity"
        reply = f"选材权衡完成。{verdict}"
        if dropped:
            reply += "\n因承力要求剔除：" + "、".join(state["findings"]["material"]["dropped"])
        reply += "\n下一步分析批量价格甜点与交期方案。"
        return _resp(state, reply=reply,
                     next_steps=[{"label": "分析批量/交期", "message": "继续"}])

    if stage == "quantity":
        batch = int(state["answers"].get("batch") or 10)
        probes = sorted({1, max(1, batch // 2), batch, batch * 2, batch * 5})
        curve = []
        for qn in probes:
            r = dispatch("get_quote", {"quantity": qn}, ctx)
            up = _unit_price(r.get("data") or {})
            if up:
                curve.append((qn, up))
        elbow = _find_elbow(curve)
        qd = (dispatch("get_quote", {"quantity": batch}, ctx).get("data") or {})
        lead = _lead_days(qd)
        deadline = state["answers"].get("deadline_days")
        lines = ["批量/交期分析：",
                 "数量-单价曲线 " + "、".join(f"{q}件 ¥{u:.2f}" for q, u in curve) + "。"]
        if elbow and elbow != batch:
            lines.append(f"价格甜点在约 {elbow} 件——超过后摊销收益明显变平；"
                         f"当前批量 {batch} 件{'已在甜点之后，合理' if batch >= elbow else f'若能提到 {elbow} 件更划算'}。")
        if deadline and lead and lead > deadline:
            lines.append(f"标准交期 {lead} 天超出约束 {deadline} 天，需选加急档（有溢价）或放宽交期。")
        elif lead:
            lines.append(f"标准交期 {lead} 天" + (f"，满足 {deadline} 天约束。" if deadline else "。"))
        state["findings"]["quantity"] = {"curve": curve, "elbow": elbow,
                                          "lead_days": lead, "deadline": deadline}
        state["stage"] = "brief"
        lines.append("可以出决策简报了。")
        return _resp(state, reply="\n".join(lines),
                     next_steps=[{"label": "生成决策简报", "message": "继续"}])

    # stage == "brief"
    f = state["findings"]
    a = state["answers"]
    geo = f.get("geometry") or {}
    mat = f.get("material") or {}
    qty = f.get("quantity") or {}
    lines = ["决策简报 Decision brief",
             f"一、研究前提：{f.get('clarify', '—')}",
             f"二、几何与工艺：{geo.get('summary', '—')}"
             + (f"；风险：{'、'.join(geo['risks'])}" if geo.get("risks") else "；无明显风险"),
             f"三、选材结论：{mat.get('verdict', '—')}",
             "四、批量与交期：" + ("、".join(f"{q}件 ¥{u:.2f}" for q, u in qty.get("curve", [])) or "—")
             + (f"；建议批量 ≥{qty['elbow']} 件" if qty.get("elbow") else "")
             + (f"；标准交期 {qty['lead_days']} 天" if qty.get("lead_days") else ""),
             "五、建议行动：按上述选材与批量生成正式报价单，存档后发客户确认；"
             + ("交期紧张请同时确认加急档。" if (qty.get("deadline") and qty.get("lead_days")
                                              and qty["lead_days"] > qty["deadline"]) else
                "如需调整任一前提（承力/批量/交期），告诉我即可重新推演对应环节。")]
    state["stage"] = "brief"
    return _resp(state, reply="\n".join(lines), done=True,
                 next_steps=[{"label": "按结论生成正式报价", "message": "按研究结论报价"},
                             {"label": "重新研究", "message": "重新开始研究"}])


def _resp(state: dict, *, reply: str, next_steps: list | None = None,
          done: bool = False) -> dict:
    idx = STAGES.index(state.get("stage", "clarify"))
    return {"reply": reply, "stage": state["stage"],
            "stage_label": STAGE_LABELS[state["stage"]],
            "progress": {"current": idx + 1, "total": len(STAGES),
                         "stages": [{"key": s, "label": STAGE_LABELS[s],
                                     "done": STAGES.index(s) < idx or (done and s == state["stage"])}
                                    for s in STAGES]},
            "next_steps": next_steps or [], "state": state, "done": done}


def _unit_price(tool_data: dict) -> float | None:
    # the get_quote TOOL returns flat data: {unit_price_cny, lead_days, ...}
    try:
        return float(tool_data["unit_price_cny"])
    except (KeyError, TypeError, ValueError):
        return None


def _lead_days(tool_data: dict) -> int | None:
    try:
        return int(tool_data["lead_days"])
    except (KeyError, TypeError, ValueError):
        return None


def _find_elbow(curve: list[tuple[int, float]]) -> int | None:
    """Quantity where the unit price stops dropping meaningfully (<5% per step)."""
    if len(curve) < 2:
        return None
    for (q0, p0), (q1, p1) in zip(curve, curve[1:]):
        if p0 > 0 and (p0 - p1) / p0 < 0.05:
            return q0
    return curve[-1][0]

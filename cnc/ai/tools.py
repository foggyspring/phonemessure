"""Tool registry: the engine capabilities the AI can call on the user's behalf.

Each tool has a JSON-schema (so a real LLM can call it) + a handler that runs
against an AgentContext (the current part + form params). Handlers return
{"summary": str, "data": ...}; the summary is what the assistant narrates.

Read-only tools run freely. Write tools (price changes, calibration) are flagged
``requires_approval``/``admin`` and gated by the agent loop (Intervention Point).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..service import QuoteRequest, build_quote


@dataclass
class AgentContext:
    shop: object
    metrics: object | None = None
    mesh_stl: bytes | None = None
    params: dict = field(default_factory=dict)
    price_sources: dict = field(default_factory=dict)
    calibration_factors: dict = field(default_factory=dict)
    is_admin: bool = False


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, AgentContext], dict]
    requires_approval: bool = False
    admin: bool = False


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def tool_schemas(*, is_admin: bool = False) -> list[dict]:
    """Schemas to hand the LLM — write/admin tools only when allowed."""
    out = []
    for t in _REGISTRY.values():
        if t.admin and not is_admin:
            continue
        out.append({"name": t.name, "description": t.description, "parameters": t.parameters})
    return out


def dispatch(name: str, args: dict, ctx: AgentContext) -> dict:
    t = _REGISTRY.get(name)
    if t is None:
        return {"summary": f"未知工具 {name}", "data": None, "error": "unknown_tool"}
    if t.admin and not ctx.is_admin:
        return {"summary": "该操作需要管理员登录。", "data": None, "error": "forbidden"}
    try:
        return t.handler(args or {}, ctx)
    except Exception as exc:  # never crash the agent on a tool error
        return {"summary": f"工具 {name} 执行出错：{exc}", "data": None, "error": str(exc)}


# ---------------------------------------------------------- helpers --------
def _need_part(ctx: AgentContext) -> dict | None:
    if ctx.metrics is None:
        return {"summary": "请先上传零件模型（或填写手动尺寸）后再让我估算。", "data": None,
                "error": "no_part"}
    return None


def _run_quote(ctx: AgentContext, overrides: dict | None = None, *, compare=False) -> dict:
    p = {**ctx.params, **(overrides or {})}
    req = QuoteRequest.from_payload(p)
    return build_quote(ctx.metrics, req, ctx.shop, mesh_stl=ctx.mesh_stl,
                       backend=str(p.get("backend", "auto")), price_sources=ctx.price_sources,
                       calibration_factors=ctx.calibration_factors, compare=compare)


def _fmt(v: float) -> str:
    return f"¥{v:,.2f}"


# ---------------------------------------------------------- read tools -----
def _t_get_quote(args: dict, ctx: AgentContext) -> dict:
    if (e := _need_part(ctx)):
        return e
    ov = {}
    for k in ("material", "quantity", "finish", "tolerance", "surface_finish", "lead_time"):
        if args.get(k) is not None:
            ov[k] = args[k]
    p = _run_quote(ctx, ov)
    q, r, g = p["quote"], p["quote"]["requested"], p["geometry"]
    s = (f"{p['input']['material_label']} ×{r['quantity']}：单价 {_fmt(r['unit_price_cny'])}，"
         f"净额 {_fmt(q['net_total_cny'])}，含税 {_fmt(q['total_incl_tax_cny'])}，"
         f"交期 {q['lead_days']} 天；置信度 {p['confidence']['score']}/100。")
    return {"summary": s, "data": {"unit_price_cny": r["unit_price_cny"],
            "total_incl_tax_cny": q["total_incl_tax_cny"], "lead_days": q["lead_days"],
            "confidence": p["confidence"], "dims_mm": g["dims_mm"]}}


def _t_compare_materials(args: dict, ctx: AgentContext) -> dict:
    if (e := _need_part(ctx)):
        return e
    p = _run_quote(ctx, {}, compare=True)
    rows = p.get("material_comparison") or []
    top = rows[:4]
    s = "材料单价对比（便宜在前）：" + "、".join(
        f"{r['label'].split(' ')[0]} {_fmt(r['unit_price_cny'])}" for r in top)
    return {"summary": s, "data": rows}


def _t_suggest_cheaper(args: dict, ctx: AgentContext) -> dict:
    if (e := _need_part(ctx)):
        return e
    p = _run_quote(ctx, {})
    sugg = p.get("material_suggestions") or []
    if not sugg:
        return {"summary": "当前材料已是同类中较省的，没有更便宜的等效替代。", "data": []}
    s = "更省材料（如性能允许）：" + "、".join(
        f"{x['label'].split(' ')[0]} 省 {x['savings_pct']}%" for x in sugg)
    return {"summary": s, "data": sugg}


def _t_analyze_dfm(args: dict, ctx: AgentContext) -> dict:
    if (e := _need_part(ctx)):
        return e
    p = _run_quote(ctx, {})
    dfm = p.get("dfm") or []
    risks = [d for d in dfm if d["severity"] in ("high", "medium")]
    if not risks:
        s = "未发现明显可加工性风险（仅常规提示）。"
    else:
        s = "可加工性提示：" + "；".join(f"[{d['severity']}] {d['title']} — {d['suggestion']}" for d in risks)
    return {"summary": s, "data": dfm}


def _t_list_materials(args: dict, ctx: AgentContext) -> dict:
    mats = [{"key": k, "label": m.label, "category": m.category,
             "price_cny_per_kg": m.price_cny_per_kg} for k, m in ctx.shop.materials.items()]
    s = "可选材料：" + "、".join(m["label"].split(" ")[0] for m in mats)
    return {"summary": s, "data": mats}


def register_builtin_tools() -> None:
    register(Tool("get_quote", "对当前零件按可选材料/数量/公差/表面/交期估价并返回单价、含税总价、交期、置信度。",
                  {"type": "object", "properties": {
                      "material": {"type": "string", "description": "材料牌号，如 AL6061/SUS304"},
                      "quantity": {"type": "integer"},
                      "finish": {"type": "string"}, "tolerance": {"type": "string"},
                      "surface_finish": {"type": "string"}, "lead_time": {"type": "string"}}},
                  _t_get_quote))
    register(Tool("compare_materials", "对当前零件比较所有可用材料的单价（含密度/可加工性）。",
                  {"type": "object", "properties": {}}, _t_compare_materials))
    register(Tool("suggest_cheaper_material", "为当前零件推荐更便宜的同类材料及节省比例。",
                  {"type": "object", "properties": {}}, _t_suggest_cheaper))
    register(Tool("analyze_dfm", "对当前零件做可加工性(DFM)分析，列出风险与建议。",
                  {"type": "object", "properties": {}}, _t_analyze_dfm))
    register(Tool("list_materials", "列出可选材料及当日单价。",
                  {"type": "object", "properties": {}}, _t_list_materials))


register_builtin_tools()

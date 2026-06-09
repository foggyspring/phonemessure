"""Agent loop: drive the provider ↔ tools cycle to a final answer.

Calls the provider with the conversation + tool schemas; runs any tool calls
against the AgentContext, feeds the results back, and repeats until the provider
returns a turn with no tool calls (bounded by max_steps). Write tools flagged
``requires_approval`` are NOT executed here — they are returned as a pending
action for the UI to approve (Intervention Point), wired in a later iteration.
"""
from __future__ import annotations

from .provider import get_provider
from .tools import AgentContext, dispatch, get_tool, tool_schemas


def analyze_part(ctx: AgentContext) -> dict:
    """Full-AI analysis: run the standard read-only toolchain and synthesise a
    structured report + recommendation. Deterministic so it works with the mock;
    a real LLM can enrich the narration later."""
    sections = []
    for name in ("get_quote", "analyze_dfm", "compare_materials", "suggest_cheaper_material"):
        res = dispatch(name, {}, ctx)
        sections.append({"tool": name, "summary": res.get("summary", ""),
                         "data": res.get("data"), "error": res.get("error")})
    by = {s["tool"]: s for s in sections}

    recs: list[str] = []
    q = by["get_quote"].get("data") or {}
    if isinstance(q, dict) and q.get("confidence"):
        c = q["confidence"]
        recs.append(f"报价置信度 {c['score']}/100（{c['level']}）"
                    + ("；" + "、".join(c["reasons"]) if c.get("reasons") else ""))
    sugg = by["suggest_cheaper_material"].get("data") or []
    if sugg:
        recs.append(f"如性能允许，换 {sugg[0]['label'].split(' ')[0]} 可省约 {sugg[0]['savings_pct']}%。")
    dfm = by["analyze_dfm"].get("data") or []
    risks = [d for d in dfm if d.get("severity") in ("high", "medium")]
    if risks:
        recs.append(f"注意 {len(risks)} 项可加工性风险：" + "、".join(d["title"] for d in risks[:3]))
    else:
        recs.append("未见明显可加工性风险。")

    return {"sections": sections, "recommendation": " ".join(recs),
            "provider": get_provider().name}


def run_agent(message: str, ctx: AgentContext, *, history: list[dict] | None = None,
              max_steps: int = 4) -> dict:
    provider = get_provider()
    tools = tool_schemas(is_admin=ctx.is_admin)
    convo: list[dict] = list(history or [])
    convo.append({"role": "user", "content": message})

    actions: list[dict] = []
    pending: dict | None = None

    for _ in range(max_steps):
        turn = provider.chat(convo, tools)
        if not turn.tool_calls:
            convo.append({"role": "assistant", "content": turn.text})
            return {"reply": turn.text, "actions": actions, "pending": None,
                    "provider": provider.name, "history": convo}

        for tc in turn.tool_calls:
            tool = get_tool(tc.name)
            if tool is not None and tool.requires_approval:
                # Intervention Point: never auto-execute a write — surface it for
                # explicit approval (and it needs an admin to confirm).
                pending = {"tool": tc.name, "arguments": tc.arguments,
                           "description": tool.description, "admin": tool.admin}
                msg = ("请在下方确认该写操作后执行。" if ctx.is_admin
                       else "该操作会修改数据，需管理员登录并确认后才能执行。")
                return {"reply": msg, "actions": actions, "pending": pending,
                        "provider": provider.name, "history": convo}
            result = dispatch(tc.name, tc.arguments, ctx)
            actions.append({"tool": tc.name, "arguments": tc.arguments,
                            "summary": result.get("summary", ""),
                            "error": result.get("error"), "data": result.get("data")})
            convo.append({"role": "assistant", "content": f"[调用 {tc.name}]"})
            convo.append({"role": "tool", "name": tc.name,
                          "summary": result.get("summary", ""),
                          "content": result.get("summary", "")})

    # ran out of steps — summarise what we have
    turn = provider.chat(convo, tools)
    return {"reply": turn.text or "已完成。", "actions": actions, "pending": None,
            "provider": provider.name, "history": convo}

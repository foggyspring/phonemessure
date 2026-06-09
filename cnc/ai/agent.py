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
            if tool is not None and tool.requires_approval and not ctx.is_admin:
                # surface as a pending action instead of executing
                pending = {"tool": tc.name, "arguments": tc.arguments,
                           "description": tool.description}
                return {"reply": "该操作需要确认/管理员权限，请审批后执行。",
                        "actions": actions, "pending": pending,
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

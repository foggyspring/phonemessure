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

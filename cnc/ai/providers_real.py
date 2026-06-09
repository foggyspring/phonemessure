"""Real LLM provider adapters — inactive until configured at launch.

Selected by env (AI_PROVIDER). Each adapter reports ``available`` and the agent
falls back to the MockProvider whenever the SDK/key is missing or a call fails,
so wiring a real model is a deploy-time switch with zero code change here.

AnthropicProvider uses the official ``anthropic`` SDK and Claude tool-use
(model defaults to claude-opus-4-8). OpenAIProvider talks to any
OpenAI-compatible /chat/completions endpoint over plain HTTP.
"""
from __future__ import annotations

import json
import os

from .provider import AssistantTurn, LLMProvider, ToolCall

_SYSTEM = (
    "你是 CNC 自动报价系统的 AI 助手。你可以调用提供的工具，对用户当前上传的零件"
    "进行报价、材料对比、更省方案、DFM 可加工性分析等，并用简洁中文回答。"
    "涉及改价/录入实测工时等写操作时，按工具定义请求，由系统走管理员审批，不要假设已执行。"
)


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": m.get("content", "") or "（空）"})
        elif role == "assistant":
            out.append({"role": "assistant", "content": m.get("content", "") or "（调用工具）"})
        elif role == "tool":
            out.append({"role": "user",
                        "content": f"[工具 {m.get('name')} 结果] {m.get('summary', '')}"})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "你好"})
    return out


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self):
        self.model = os.environ.get("AI_MODEL", "claude-opus-4-8")
        self._client = None
        self.available = False
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return
        try:
            import anthropic
            self._client = anthropic.Anthropic()
            self.available = True
        except Exception:
            self.available = False

    def chat(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        if not self.available:
            return AssistantTurn(text="（AI 未配置）", done=True)
        a_tools = [{"name": t["name"], "description": t["description"],
                    "input_schema": t["parameters"]} for t in tools]
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=4096, system=_SYSTEM,
                tools=a_tools, messages=_to_anthropic_messages(messages),
            )
        except Exception as exc:  # transient API error → graceful, fall through to mock-like reply
            return AssistantTurn(text=f"（AI 服务暂时不可用：{exc}）", done=True)
        text = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
                 for b in resp.content if b.type == "tool_use"]
        return AssistantTurn(text=text, tool_calls=calls, done=resp.stop_reason != "tool_use")


class OpenAIProvider(LLMProvider):
    """Any OpenAI-compatible /chat/completions endpoint (function calling)."""
    name = "openai"

    def __init__(self):
        self.model = os.environ.get("AI_MODEL", "gpt-4o-mini")
        self.base = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
        self.key = os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.available = bool(self.key)

    def chat(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        if not self.available:
            return AssistantTurn(text="（AI 未配置）", done=True)
        import urllib.request
        oai_tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
            for t in tools]
        msgs = [{"role": "system", "content": _SYSTEM}]
        for m in messages:
            if m.get("role") == "tool":
                msgs.append({"role": "user", "content": f"[工具 {m.get('name')} 结果] {m.get('summary','')}"})
            else:
                msgs.append({"role": m.get("role", "user"), "content": m.get("content", "") or "…"})
        body = json.dumps({"model": self.model, "messages": msgs, "tools": oai_tools}).encode()
        req = urllib.request.Request(f"{self.base}/chat/completions", data=body, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            msg = data["choices"][0]["message"]
            calls = [ToolCall(id=tc.get("id", f"c{i}"), name=tc["function"]["name"],
                              arguments=json.loads(tc["function"].get("arguments") or "{}"))
                     for i, tc in enumerate(msg.get("tool_calls") or [])]
            return AssistantTurn(text=msg.get("content") or "", tool_calls=calls, done=not calls)
        except Exception as exc:
            return AssistantTurn(text=f"（AI 服务暂时不可用：{exc}）", done=True)

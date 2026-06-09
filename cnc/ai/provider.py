"""LLM provider abstraction + a deterministic offline MockProvider.

A provider turns a message history + available tool schemas into an
``AssistantTurn`` (free text and/or tool calls). The agent loop executes any
tool calls, appends their results, and calls the provider again until it returns
a turn with no tool calls (``done``).

The MockProvider emulates an agent with rule-based intent parsing so the whole
copilot works offline and is deterministically testable. Real providers
(Anthropic / OpenAI-compatible) are added as drop-in adapters and selected by
env at launch; until then ``get_provider`` returns the mock.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = True


class LLMProvider:
    name = "base"
    available = False

    def chat(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        raise NotImplementedError


# ----------------------------------------------------------------- mock ----
_MATERIALS = {
    "al6061": "AL6061", "6061": "AL6061", "铝": "AL6061", "aluminum": "AL6061", "aluminium": "AL6061",
    "al7075": "AL7075", "7075": "AL7075",
    "sus304": "SUS304", "304": "SUS304", "不锈钢": "SUS304", "stainless": "SUS304",
    "sus316": "SUS316", "316": "SUS316",
    "brass": "BRASS_C360", "黄铜": "BRASS_C360",
    "copper": "COPPER_C110", "紫铜": "COPPER_C110", "铜": "COPPER_C110",
    "titanium": "TITANIUM_TC4", "钛": "TITANIUM_TC4", "tc4": "TITANIUM_TC4",
    "pom": "POM", "abs": "ABS", "pa6": "PA6", "尼龙": "PA6",
}


def _detect_material(text: str) -> str | None:
    low = text.lower()
    for kw, key in _MATERIALS.items():
        if kw in low:
            return key
    return None


def _detect_quantity(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:件|个|pcs|pc|qty|数量)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:数量|qty|quantity|x|×)\s*(\d+)", text, re.I)
    return int(m.group(1)) if m else None


class MockProvider(LLMProvider):
    """Offline rule-based agent emulator (default until a real LLM is wired)."""
    name = "mock"
    available = True

    def chat(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        names = {t["name"] for t in tools}
        last = messages[-1] if messages else {"role": "user", "content": ""}

        # Phase 2: tool results are in — summarise and finish.
        if last.get("role") == "tool":
            summaries = [m.get("summary") or m.get("content", "")
                         for m in messages if m.get("role") == "tool"
                         and _after_last_user(messages, m)]
            text = "已完成：\n" + "\n".join(f"· {s}" for s in summaries if s)
            return AssistantTurn(text=text, done=True)

        # Phase 1: plan tool calls from the latest user message.
        user = last.get("content", "") if last.get("role") == "user" else ""
        low = user.lower()
        calls: list[ToolCall] = []
        mat = _detect_material(user)
        qty = _detect_quantity(user)
        args = {}
        if mat:
            args["material"] = mat
        if qty:
            args["quantity"] = qty

        def call(name, a=None):
            calls.append(ToolCall(id=f"c{len(calls)}", name=name, arguments=a or {}))

        wants_explain = any(k in low for k in ["为什么", "为啥", "解释", "怎么算", "explain", "凭什么", "贵在"])
        wants_quote = any(k in low for k in ["报价", "多少钱", "价格", "quote", "price", "cost"])
        wants_compare = any(k in low for k in ["对比", "比较", "compare"])
        wants_cheaper = any(k in low for k in ["便宜", "更省", "省钱", "cheaper", "save"])
        wants_dfm = any(k in low for k in ["dfm", "可加工", "工艺", "风险", "问题", "manufactur"])
        wants_analyze = any(k in low for k in ["分析", "评估", "analyz", "review", "看看", "检查"])
        wants_setprice = any(k in low for k in ["改价", "设置价格", "set price", "update price", "调价"])

        wants_calib = any(k in low for k in ["实测", "实际工时", "反标定", "校准工时", "calibrat"])
        if wants_calib and "record_actual_time" in names:
            mm = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|min)", user, re.I)
            call("record_actual_time", {"material": mat or args.get("material", ""),
                                        "actual_min": float(mm.group(1)) if mm else 0})
        elif wants_setprice and "set_price" in names:
            call("set_price", _parse_setprice(user))
        elif wants_analyze and "get_quote" in names:
            if "get_quote" in names:
                call("get_quote", args)
            if "analyze_dfm" in names:
                call("analyze_dfm", {})
            if "suggest_cheaper_material" in names:
                call("suggest_cheaper_material", {})
        elif wants_explain and "explain_quote" in names:
            call("explain_quote", {})
        else:
            if wants_quote and "get_quote" in names:
                call("get_quote", args)
            if wants_compare and "compare_materials" in names:
                call("compare_materials", {})
            if wants_cheaper and "suggest_cheaper_material" in names:
                call("suggest_cheaper_material", {})
            if wants_dfm and "analyze_dfm" in names:
                call("analyze_dfm", {})

        if calls:
            return AssistantTurn(text="", tool_calls=calls, done=False)

        return AssistantTurn(text=_help_text(), done=True)


def _parse_setprice(text: str) -> dict:
    mat = _detect_material(text)
    # the price follows a keyword (到/为/=/to/at) — avoids grabbing digits that
    # are part of the material name (e.g. the 6061 in AL6061).
    m = re.search(r"(?:到|为|=|改成|调到|至|to|at)\s*¥?\s*(\d+(?:\.\d+)?)", text, re.I)
    if not m:
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        m = nums[-1] if nums else None
        val = float(m) if m else 0.0
    else:
        val = float(m.group(1))
    return {"kind": "material", "key": mat or "AL6061",
            "field": "price_cny_per_kg", "value": val}


def _after_last_user(messages: list[dict], target: dict) -> bool:
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return messages.index(target) > last_user


def _help_text() -> str:
    return ("我是报价助手，可以帮你：报价（如“SUS304 50件多少钱”）、"
            "对比材料、找更省方案、做 DFM 分析，或一键“分析这个零件”。管理员还可让我改价/录入实测工时。")


# ----------------------------------------------------------- selection ----
def get_provider() -> LLMProvider:
    """Select the provider by env; fall back to mock when none is configured."""
    kind = os.environ.get("AI_PROVIDER", "mock").lower()
    import logging
    _log = logging.getLogger("cnc.ai")
    if kind in ("anthropic", "claude"):
        try:
            from .providers_real import AnthropicProvider
            p = AnthropicProvider()
            if p.available:
                return p
            _log.warning("AI_PROVIDER=anthropic but unavailable (no SDK/key) — using mock")
        except Exception as exc:
            _log.warning("anthropic provider init failed: %s — using mock", exc)
    elif kind in ("openai", "compatible"):
        try:
            from .providers_real import OpenAIProvider
            p = OpenAIProvider()
            if p.available:
                return p
            _log.warning("AI_PROVIDER=openai but no key (AI_API_KEY) — using mock")
        except Exception as exc:
            _log.warning("openai provider init failed: %s — using mock", exc)
    return MockProvider()

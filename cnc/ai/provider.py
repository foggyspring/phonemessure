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

        # Phase 1: plan from the latest user message — DATA-DRIVEN by the skill
        # library (data/skills.json + runtime overrides), not hardcoded intents,
        # so operators can tune triggers / add FAQ knowledge / disable a skill.
        user = last.get("content", "") if last.get("role") == "user" else ""
        mat = _detect_material(user)
        qty = _detect_quantity(user)
        args = {}
        if mat:
            args["material"] = mat
        if qty:
            args["quantity"] = qty

        chosen, _excl = _match_skills(user, names, is_admin="set_price" in names)

        # A knowledge skill answers directly (curated shop FAQ) — no tool call.
        for s in chosen:
            if s["kind"] == "knowledge":
                return AssistantTurn(text=s["response"], done=True)

        calls: list[ToolCall] = []

        def call(name, a=None):
            calls.append(ToolCall(id=f"c{len(calls)}", name=name, arguments=a or {}))

        for s in chosen:
            if s["kind"] == "analyze":
                for a in s.get("actions", []):
                    if a in names:
                        call(a, args if a == "get_quote" else {})
            elif s["kind"] == "action":
                act = s["action"]
                if act == "record_actual_time":
                    mm = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|min)", user, re.I)
                    call(act, {"material": mat or args.get("material", ""),
                               "actual_min": float(mm.group(1)) if mm else 0})
                elif act == "set_price":
                    call(act, _parse_setprice(user))
                elif act == "get_quote":
                    call(act, args)
                else:
                    call(act, {})

        if calls:
            return AssistantTurn(text="", tool_calls=calls, done=False)

        return AssistantTurn(text=_help_text(), done=True)


def _match_skills(user: str, tool_names: set, *, is_admin: bool):
    """Resolve the effective skill library (defaults + runtime overrides) and
    match it against the user message. Best-effort: never let a store/skill
    error break the chat — fall back to an empty match (help text)."""
    from . import skills as _skills
    try:
        from .. import store
        overrides = store.get_skill_overrides()
    except Exception:
        overrides = {}
    try:
        lib = _skills.load_skills(overrides)
        return _skills.match(user, lib, tool_names=set(tool_names), is_admin=is_admin)
    except Exception:
        return [], False


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
_provider_cache: dict[tuple, LLMProvider] = {}


def get_provider() -> LLMProvider:
    """Select the provider by env; fall back to mock when none is configured.

    Memoized per env config — a single chat request resolves the provider 2+
    times (cost guard + agent) and the real providers build an SDK client each
    construction. Tests monkeypatch env, so key on the config, not a singleton.
    """
    kind = os.environ.get("AI_PROVIDER", "mock").lower()
    cache_key = (kind, os.environ.get("AI_API_KEY", ""), os.environ.get("AI_BASE_URL", ""))
    hit = _provider_cache.get(cache_key)
    if hit is not None:
        return hit
    p = _build_provider(kind)
    _provider_cache[cache_key] = p
    return p


def _build_provider(kind: str) -> LLMProvider:
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

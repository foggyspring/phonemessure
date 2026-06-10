"""AI skill library — the assistant's maintainable capability/knowledge base.

A *skill* is provider-independent data, not code:

    {key, name, kind, triggers[], action|actions|response, exclusive, admin,
     priority, enabled, builtin}

  kind="action"     → fire one tool (get_quote / analyze_dfm / set_price …)
  kind="analyze"    → fire a chain of tools (full analysis)
  kind="knowledge"  → answer directly with `response` (a curated FAQ / shop tip)

The MockProvider matches user text against the enabled skills (offline,
deterministic, testable); a real LLM at launch can take the same library as a
system-prompt / few-shot knowledge pack and choose itself. Either way the
library is the single place operators tune the assistant.

Precedence: the static default library (data/skills.json) is the base; runtime
overrides (store) layer on top — operators can edit triggers/response, disable a
builtin (never silently deleted), or add brand-new custom skills. Mirrors the
price-override pattern: base immutable, overrides patch, fully revertible.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_SKILLS_PATH = Path(__file__).resolve().parent.parent / "data" / "skills.json"

# Fields an operator may edit on any skill (builtin or custom). action/actions
# are intentionally NOT editable on builtins — they bind to real tools, and a
# typo there would silently break a capability; a custom skill sets them once.
_EDITABLE = {"name", "triggers", "response", "exclusive", "priority", "enabled", "admin"}
_KINDS = {"action", "analyze", "knowledge"}


@lru_cache(maxsize=1)
def _defaults() -> dict[str, dict]:
    raw = json.loads(_SKILLS_PATH.read_text("utf-8"))
    return {s["key"]: s for s in raw["skills"]}


def _coerce(skill: dict) -> dict:
    """Normalize a skill dict to safe types/shape (defensive — overrides and
    custom skills come from operator input)."""
    out = dict(skill)
    out["triggers"] = [str(t).strip().lower() for t in (out.get("triggers") or []) if str(t).strip()]
    out["enabled"] = bool(out.get("enabled", True))
    out["exclusive"] = bool(out.get("exclusive", False))
    out["admin"] = bool(out.get("admin", False))
    out["builtin"] = bool(out.get("builtin", False))
    try:
        out["priority"] = int(out.get("priority", 50))
    except (TypeError, ValueError):
        out["priority"] = 50
    out["kind"] = out["kind"] if out.get("kind") in _KINDS else "knowledge"
    out["name"] = str(out.get("name") or out.get("key") or "skill")
    out["response"] = str(out.get("response") or "")
    if out["kind"] == "analyze":
        out["actions"] = [str(a) for a in (out.get("actions") or [])]
    elif out["kind"] == "action":
        out["action"] = str(out.get("action") or "")
    return out


def load_skills(overrides: dict | None = None) -> list[dict]:
    """Effective skill library = defaults patched by overrides, sorted by
    descending priority. overrides = {key: partial-or-full skill dict}; a custom
    key (not in defaults) is added whole, a builtin key is patched field-wise.
    """
    merged: dict[str, dict] = {k: dict(v) for k, v in _defaults().items()}
    for key, patch in (overrides or {}).items():
        if not isinstance(patch, dict):
            continue
        if key in merged:                       # patch a builtin (editable fields only)
            merged[key].update({f: patch[f] for f in _EDITABLE if f in patch})
        else:                                    # a brand-new custom skill
            sk = dict(patch)
            sk["key"] = key
            sk["builtin"] = False
            merged[key] = sk
    skills = [_coerce(s) for s in merged.values()]
    skills.sort(key=lambda s: -s["priority"])
    return skills


def editable_fields() -> set[str]:
    return set(_EDITABLE)


def is_builtin(key: str) -> bool:
    return key in _defaults()


def validate_custom(skill: dict) -> dict:
    """Validate a NEW custom skill payload; raise ValueError on bad shape.
    Returns the normalized skill ready to persist."""
    key = str(skill.get("key") or "").strip()
    if not key or not key.replace("_", "").isalnum():
        raise ValueError("key 必须是字母/数字/下划线")
    if key in _defaults():
        raise ValueError(f"'{key}' 是内置技能，请用编辑而非新增")
    kind = skill.get("kind")
    if kind not in _KINDS:
        raise ValueError(f"kind 必须是 {_KINDS}")
    triggers = [str(t).strip() for t in (skill.get("triggers") or []) if str(t).strip()]
    if not triggers:
        raise ValueError("至少需要一个触发词")
    if kind == "knowledge" and not str(skill.get("response") or "").strip():
        raise ValueError("知识类技能需要 response 话术")
    if kind == "action" and not str(skill.get("action") or "").strip():
        raise ValueError("action 类技能需要绑定 action 工具名")
    return _coerce({**skill, "key": key, "builtin": False})


def match(text: str, skills: list[dict], *, tool_names: set[str], is_admin: bool):
    """Return (chosen_skills, exclusive_flag) for a user message.

    An exclusive skill (highest-priority hit) is returned alone; otherwise all
    additive hits are returned. Admin-only skills are skipped for non-admins.
    Knowledge skills are always exclusive in effect (they answer directly).
    """
    low = (text or "").lower()

    def hit(s: dict) -> bool:
        if not s["enabled"]:
            return False
        if s.get("admin") and not is_admin:
            return False
        if s["kind"] == "action" and s.get("action") not in tool_names:
            return False
        if s["kind"] == "analyze" and not any(a in tool_names for a in s.get("actions", [])):
            return False
        return any(t in low for t in s["triggers"])

    hits = [s for s in skills if hit(s)]            # already priority-sorted
    if not hits:
        return [], False
    top = hits[0]
    if top["exclusive"] or top["kind"] in ("analyze", "knowledge"):
        return [top], True
    additive = [s for s in hits if not s["exclusive"] and s["kind"] == "action"]
    return additive or [top], False

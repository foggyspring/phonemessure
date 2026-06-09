"""AI copilot layer: pluggable LLM provider + tool registry + agent loop.

Designed to run with a deterministic MockProvider today (no LLM API wired) and
swap in a real provider (Anthropic / OpenAI-compatible) at launch via env, with
graceful fallback to mock — the same pattern as the live material-price feed.
"""
from .provider import AssistantTurn, LLMProvider, MockProvider, ToolCall, get_provider

__all__ = ["LLMProvider", "MockProvider", "AssistantTurn", "ToolCall", "get_provider"]

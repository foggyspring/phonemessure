"""AI provider abstraction + MockProvider intent parsing."""
from __future__ import annotations

from cnc.ai import MockProvider, get_provider

TOOLS = [{"name": n} for n in
         ["get_quote", "compare_materials", "suggest_cheaper_material", "analyze_dfm", "set_price"]]


def _calls(text):
    return MockProvider().chat([{"role": "user", "content": text}], TOOLS)


def test_default_provider_is_mock():
    p = get_provider()
    assert p.name == "mock" and p.available


def test_quote_intent_extracts_material_and_qty():
    t = _calls("SUS304 50件多少钱")
    assert not t.done
    c = t.tool_calls[0]
    assert c.name == "get_quote" and c.arguments == {"material": "SUS304", "quantity": 50}


def test_compare_and_cheaper_intents():
    assert _calls("对比一下材料").tool_calls[0].name == "compare_materials"
    assert _calls("有没有更便宜的方案").tool_calls[0].name == "suggest_cheaper_material"


def test_analyze_triggers_multiple_tools():
    names = [c.name for c in _calls("帮我分析这个零件").tool_calls]
    assert "get_quote" in names and "analyze_dfm" in names


def test_set_price_parses_value_not_material_digits():
    c = _calls("把 AL6061 改价到 40").tool_calls[0]
    assert c.name == "set_price"
    assert c.arguments["key"] == "AL6061" and c.arguments["value"] == 40.0


def test_unknown_message_gives_help_no_tools():
    t = _calls("你好")
    assert t.done and not t.tool_calls and "报价助手" in t.text


def test_tool_results_get_summarised():
    msgs = [{"role": "user", "content": "报价"},
            {"role": "tool", "name": "get_quote", "summary": "AL6061 ×5 单价 ¥76.76"}]
    t = MockProvider().chat(msgs, TOOLS)
    assert t.done and "76.76" in t.text


def test_only_offered_tools_are_called():
    # if set_price isn't available, a price request won't fabricate it
    t = MockProvider().chat([{"role": "user", "content": "改价到 40"}],
                            [{"name": "get_quote"}])
    assert not any(c.name == "set_price" for c in t.tool_calls)


def test_real_adapters_inactive_without_config(monkeypatch):
    # adapters import cleanly and report unavailable when no key/SDK is configured
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from cnc.ai.providers_real import AnthropicProvider, OpenAIProvider
    assert AnthropicProvider().available is False
    assert OpenAIProvider().available is False


def test_get_provider_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from cnc.ai import get_provider
    assert get_provider().name == "mock"      # no key → graceful fallback


def test_openai_adapter_request_and_toolcall_parse(monkeypatch):
    # simulates the vectorengine (OpenAI-compatible) endpoint with an injected transport
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    monkeypatch.setenv("AI_BASE_URL", "https://api.vectorengine.ai/v1")
    monkeypatch.setenv("AI_MODEL", "gpt-5.5-pro")
    monkeypatch.setenv("AI_TEMPERATURE", "0.7")
    seen = {}

    def fake_http(url, headers, payload):
        seen.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "call_1", "function": {"name": "get_quote",
             "arguments": '{"material":"SUS304","quantity":50}'}}]}}]}

    from cnc.ai.providers_real import OpenAIProvider
    p = OpenAIProvider(http=fake_http)
    assert p.available
    tools = [{"name": "get_quote", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    turn = p.chat([{"role": "user", "content": "SUS304 50件"}], tools)
    assert seen["url"] == "https://api.vectorengine.ai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["payload"]["model"] == "gpt-5.5-pro" and seen["payload"]["temperature"] == 0.7
    assert seen["payload"]["tools"][0]["function"]["name"] == "get_quote"
    assert turn.tool_calls[0].name == "get_quote"
    assert turn.tool_calls[0].arguments == {"material": "SUS304", "quantity": 50}
    assert not turn.done


def test_openai_adapter_plain_text(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    from cnc.ai.providers_real import OpenAIProvider
    p = OpenAIProvider(http=lambda u, h, pl: {"choices": [{"message": {"content": "你好，我是助手。"}}]})
    turn = p.chat([{"role": "user", "content": "你好"}], [])
    assert turn.text == "你好，我是助手。" and turn.done and not turn.tool_calls


def test_openai_adapter_graceful_on_error(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    from cnc.ai.providers_real import OpenAIProvider

    def boom(u, h, pl):
        raise RuntimeError("HTTP 403 Forbidden")
    turn = OpenAIProvider(http=boom).chat([{"role": "user", "content": "hi"}], [])
    assert turn.done and "不可用" in turn.text


def test_get_provider_selects_openai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    from cnc.ai import get_provider
    assert get_provider().name == "openai"

import json
from pathlib import Path

import httpx2 as httpx
import pytest

from app.config import Settings
from app.llm import DEFAULT_CONTEXT_TOKENS, LLMClient, LLMError, estimate_tokens


def make_client(handler, **overrides) -> LLMClient:
    settings = Settings(
        db_path=Path("x.db"),
        questions_dir=Path("questions"),
        auto_import=False,
        app_lang="en",
        llm_base_url="http://llm.test",
        **overrides,
    )
    return LLMClient(settings, transport=httpx.MockTransport(handler))


def chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_disabled_without_base_url(settings):
    assert not settings.llm_enabled


def test_from_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://host:11434/")
    monkeypatch.setenv("LLM_CAPABILITIES", "Vision, tools")
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "4096")
    settings = Settings.from_env()
    assert settings.llm_enabled
    assert settings.llm_base_url == "http://host:11434"
    assert settings.llm_capabilities == {"vision", "tools"}
    assert settings.llm_context_tokens == 4096


def test_list_models_sorted():
    client = make_client(
        lambda request: httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
    )
    assert client.list_models() == ["a", "b"]


def test_chat_returns_text_and_strips_think():
    client = make_client(lambda request: chat_reply("<think>hmm\nmore</think>\nHello"))
    assert client.chat("m", [{"role": "user", "content": "hi"}]) == "Hello"


def test_chat_with_schema_sends_response_format_and_parses():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return chat_reply('{"ok": true}')

    client = make_client(handler, llm_api_key="secret")
    assert client.chat("m", [], json_schema={"type": "object"}) == {"ok": True}
    assert seen["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert seen["model"] == "m"


def test_chat_invalid_json_raises():
    client = make_client(lambda request: chat_reply("not json"))
    with pytest.raises(LLMError):
        client.chat("m", [], json_schema={"type": "object"})


def test_chat_unexpected_shape_raises():
    client = make_client(lambda request: httpx.Response(200, json={"oops": 1}))
    with pytest.raises(LLMError):
        client.chat("m", [])


def test_unreachable_raises_llm_error():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(LLMError, match="cannot reach"):
        make_client(handler).list_models()


def test_http_error_raises_llm_error():
    client = make_client(lambda request: httpx.Response(500))
    with pytest.raises(LLMError, match="500"):
        client.list_models()


def test_model_info_from_ollama_probe_is_cached():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "capabilities": ["completion", "vision"],
                "model_info": {"llama.context_length": 32768},
            },
        )

    client = make_client(handler)
    info = client.model_info("llava")
    assert info.vision and info.context_tokens == 32768
    client.model_info("llava")
    assert calls == ["/api/show"]


def test_model_info_probe_without_vision():
    client = make_client(lambda request: httpx.Response(200, json={"capabilities": ["completion"]}))
    info = client.model_info("m")
    assert not info.vision
    assert info.context_tokens == DEFAULT_CONTEXT_TOKENS


def test_model_info_falls_back_to_env_when_probe_missing():
    client = make_client(
        lambda request: httpx.Response(404),
        llm_capabilities=frozenset({"vision"}),
        llm_context_tokens=16000,
    )
    info = client.model_info("m")
    assert info.vision and info.context_tokens == 16000


def test_model_info_default_is_text_only_small_context():
    info = make_client(lambda request: httpx.Response(404)).model_info("m")
    assert not info.vision and info.context_tokens == DEFAULT_CONTEXT_TOKENS


def test_estimate_tokens():
    assert estimate_tokens("a" * 400) == 101
    assert estimate_tokens("", images=2) == 3001

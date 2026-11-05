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


def openai_only(handler):
    """A non-Ollama server: /api/show does not exist."""
    return lambda request: (
        httpx.Response(404) if request.url.path == "/api/show" else handler(request)
    )


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
    client = make_client(openai_only(lambda request: chat_reply("<think>hmm\nmore</think>\nHello")))
    assert client.chat("m", [{"role": "user", "content": "hi"}]) == "Hello"


def test_chat_with_schema_sends_response_format_and_parses():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return chat_reply('{"ok": true}')

    client = make_client(openai_only(handler), llm_api_key="secret")
    assert client.chat("m", [], json_schema={"type": "object"}) == {"ok": True}
    assert seen["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert seen["model"] == "m"


def test_chat_invalid_json_raises():
    client = make_client(openai_only(lambda request: chat_reply("not json")))
    with pytest.raises(LLMError):
        client.chat("m", [], json_schema={"type": "object"})


def test_chat_unexpected_shape_raises():
    client = make_client(openai_only(lambda request: httpx.Response(200, json={"oops": 1})))
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


@pytest.mark.parametrize("raw", ['"qwen2.5:7b"', "'qwen2.5:7b'", "  qwen2.5:7b  "])
def test_from_env_strips_quotes(monkeypatch, raw):
    monkeypatch.setenv("LLM_BASE_URL", '"http://host:11434"')
    monkeypatch.setenv("LLM_MODEL", raw)
    settings = Settings.from_env()
    assert settings.llm_model == "qwen2.5:7b"
    assert settings.llm_base_url == "http://host:11434"


def test_preferred_model():
    client = make_client(lambda request: httpx.Response(404), llm_model="b")
    assert client.preferred_model(["a", "b"]) == "b"
    assert client.preferred_model(["a", "c"]) == "a"
    assert client.preferred_model([]) == "b"


def ollama_server(seen: list, content: str = '{"ok": 1}', context: int = 32768):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"capabilities": ["vision"], "model_info": {"q.context_length": context}}
            )
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    return handler


def test_ollama_uses_native_chat_with_num_ctx_and_format():
    seen = []
    client = make_client(ollama_server(seen))
    prompt = "x" * 40000  # ~10k tokens
    assert client.chat("m", [{"role": "user", "content": prompt}], {"type": "object"}) == {"ok": 1}
    path, body = seen[0]
    assert path == "/api/chat" and body["format"] == {"type": "object"}
    assert body["stream"] is False
    assert body["options"]["num_ctx"] == 12288  # 10001 + 2048 reserve, rounded up to 1024


def test_ollama_num_ctx_is_clamped():
    seen = []
    client = make_client(ollama_server(seen, content="hi", context=8192))
    client.chat("m", [{"role": "user", "content": "x" * 400000}])
    assert seen[0][1]["options"]["num_ctx"] == 8192
    client.chat("m", [{"role": "user", "content": "short"}])
    assert seen[1][1]["options"]["num_ctx"] == 4096  # minimum


def test_ollama_images_are_converted():
    seen = []
    client = make_client(ollama_server(seen, content="hi"))
    parts = [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]
    client.chat("m", [{"role": "user", "content": parts}])
    message = seen[0][1]["messages"][0]
    assert message == {"role": "user", "content": "look", "images": ["QUJD"]}


def test_think_splitter_handles_tags_split_across_chunks():
    from app.llm import ThinkSplitter

    seen = []
    splitter = ThinkSplitter(lambda kind, text: seen.append((kind, text)))
    for chunk in ["<thi", "nk>plan", " more</th", 'ink>{"a"', ": 1}"]:
        splitter.feed(chunk)
    splitter.flush()
    joined = {}
    for kind, text in seen:
        joined[kind] = joined.get(kind, "") + text
    assert joined == {"thinking": "plan more", "output": '{"a": 1}'}


def test_stream_ollama_ndjson_with_thinking():
    lines = [
        {"message": {"thinking": "let me"}},
        {"message": {"content": '{"ok"'}},
        {"message": {"content": ": 2}"}, "done": True},
    ]

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": []})
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    seen = []
    client = make_client(handler)
    result = client.chat("m", [], {"type": "object"}, on_text=lambda k, t: seen.append((k, t)))
    assert result == {"ok": 2}
    assert seen[0][0] == "meta" and "num_ctx" in seen[0][1]
    assert ("thinking", "let me") in seen
    assert "".join(t for k, t in seen if k == "output") == '{"ok": 2}'


def test_stream_openai_sse_with_think_tags():
    events = [
        {"choices": [{"delta": {"content": "<think>hm"}}]},
        {"choices": [{"delta": {"content": "</think>Hi"}}]},
        {"choices": [{"delta": {"content": " there"}}]},
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, content=body.encode())

    seen = []
    client = make_client(openai_only(handler))
    assert client.chat("m", [], on_text=lambda k, t: seen.append((k, t))) == "Hi there"
    assert ("thinking", "hm") in seen
    assert "".join(t for k, t in seen if k == "output") == "Hi there"


def test_stream_unreachable_raises_llm_error():
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(404)
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(LLMError, match="cannot reach"):
        make_client(handler).chat("m", [], on_text=lambda k, t: None)


def test_temperature_is_sent_on_both_apis():
    seen = []
    make_client(ollama_server(seen, content="hi")).chat("m", [], temperature=0.3)
    assert seen[0][1]["options"]["temperature"] == 0.3

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return chat_reply("hi")

    make_client(openai_only(handler)).chat("m", [], temperature=0.3)
    assert bodies[0]["temperature"] == 0.3


def test_language_rules():
    from app.prompts import language_name, language_rule

    assert "Deutsch" in language_rule("de") and language_rule("Deutsch") == language_rule("de")
    assert language_name("German") == "German" and language_name("de") == "German"
    assert language_rule("fr") == "Write every text in fr only."

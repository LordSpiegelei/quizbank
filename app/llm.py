"""Optional client for a local LLM behind an OpenAI-compatible API (Ollama, LM Studio, vLLM …).

Everything here is opt-in: without `LLM_BASE_URL` there is no client and the UI hides all LLM
features. Routes get the client through the `get_llm` dependency so tests can replace it."""

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx
from fastapi import HTTPException, Request

from app.config import Settings

DEFAULT_CONTEXT_TOKENS = 8192  # assumed when neither the server nor the env tells us
IMAGE_TOKENS = 1500  # rough cost of one image
MIN_CONTEXT_TOKENS = 4096
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    """The LLM is unreachable or answered something unusable; `str()` is shown to the user."""


class LLMReplyError(LLMError):
    """The server answered, but not with usable content: worth one retry with a hint."""


@dataclass(frozen=True)
class ModelInfo:
    vision: bool
    context_tokens: int
    ollama: bool = False  # the server answered /api/show: use its native chat API


def estimate_tokens(text: str, images: int = 0) -> int:
    """Rough token count (characters / 4, plus a flat cost per image); good enough to refuse
    material that clearly does not fit."""
    return len(text) // 4 + 1 + images * IMAGE_TOKENS


def _split_content(content: Any) -> tuple[str, list[str]]:
    """Text and base64 images of an OpenAI-style message content (a string or a parts list)."""
    if isinstance(content, str):
        return content, []
    texts, images = [], []
    for part in content:
        if part.get("type") == "text":
            texts.append(part["text"])
        elif part.get("type") == "image_url":
            images.append(part["image_url"]["url"].split(",", 1)[-1])  # strip "data:…;base64,"
    return "\n\n".join(texts), images


def _to_ollama(message: dict[str, Any]) -> dict[str, Any]:
    text, images = _split_content(message["content"])
    return {"role": message["role"], "content": text} | ({"images": images} if images else {})


def context_size(messages: list[dict[str, Any]], reserve: int, limit: int) -> int:
    """num_ctx for Ollama: prompt plus reserved output, rounded up to 1024, at least
    MIN_CONTEXT_TOKENS, at most the model's context."""
    used = reserve
    for message in messages:
        text, images = _split_content(message["content"])
        used += estimate_tokens(text, len(images))
    size = -(-used // 1024) * 1024
    return max(min(size, limit), min(MIN_CONTEXT_TOKENS, limit))


class LLMClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        headers = (
            {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        )
        self._http = httpx.Client(
            base_url=settings.llm_base_url or "",
            headers=headers,
            timeout=settings.llm_timeout,
            transport=transport,
        )
        self._info: dict[str, ModelInfo] = {}

    @property
    def default_model(self) -> str | None:
        return self.settings.llm_model

    def preferred_model(self, models: list[str]) -> str | None:
        """The configured default if the server has it, else the first listed model."""
        if self.default_model in models or not models:
            return self.default_model
        return models[0]

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"HTTP {exc.response.status_code} from {exc.request.url}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"cannot reach the LLM at {self.settings.llm_base_url}") from exc
        return response

    def list_models(self) -> list[str]:
        data = self._request("GET", "/v1/models", timeout=10).json()
        return sorted(model["id"] for model in data.get("data", []))

    def model_info(self, model: str) -> ModelInfo:
        """Capabilities of `model`: probe Ollama's /api/show (cached), else env, else text-only."""
        if model not in self._info:
            self._info[model] = self._probe(model)
        return self._info[model]

    def _probe(self, model: str) -> ModelInfo:
        settings = self.settings
        vision = "vision" in settings.llm_capabilities
        context = settings.llm_context_tokens or DEFAULT_CONTEXT_TOKENS
        try:
            response = self._http.post("/api/show", json={"model": model})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return ModelInfo(vision, context)  # not Ollama (or down): env values only
        vision = "vision" in data.get("capabilities", [])
        if settings.llm_context_tokens is None:
            for key, value in (data.get("model_info") or {}).items():
                if key.endswith(".context_length") and isinstance(value, int):
                    context = value
        return ModelInfo(vision, context, ollama=True)

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any] | None = None,
        reserve_tokens: int = 2048,
    ) -> Any:
        """One completion. With `json_schema` the reply is constrained to it and returned parsed.

        Ollama gets its native API: its OpenAI endpoint runs with the server's default context
        (often 2-4k tokens) and silently cuts longer prompts from the front, so we set
        `num_ctx` to what the prompt plus `reserve_tokens` of output needs."""
        info = self.model_info(model)
        if info.ollama:
            content = self._chat_ollama(model, messages, json_schema, reserve_tokens, info)
        else:
            content = self._chat_openai(model, messages, json_schema)
        content = _THINK_RE.sub("", content or "").strip()
        if json_schema is None:
            return content
        try:
            return json.loads(content)
        except ValueError as exc:
            raise LLMReplyError("the LLM did not return valid JSON") from exc

    def _chat_ollama(
        self,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any] | None,
        reserve_tokens: int,
        info: ModelInfo,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": [_to_ollama(m) for m in messages],
            "stream": False,
            "options": {"num_ctx": context_size(messages, reserve_tokens, info.context_tokens)},
        }
        if json_schema is not None:
            body["format"] = json_schema
        data = self._request("POST", "/api/chat", json=body).json()
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMReplyError("unexpected response from the LLM") from exc

    def _chat_openai(
        self, model: str, messages: list[dict[str, Any]], json_schema: dict[str, Any] | None
    ) -> str:
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": json_schema, "strict": True},
            }
        data = self._request("POST", "/v1/chat/completions", json=body).json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMReplyError("unexpected response from the LLM") from exc


def get_llm(request: Request) -> LLMClient:
    """FastAPI dependency; 404 when no LLM is configured."""
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status_code=404)
    return llm

"""Exam builder: turn parameters and source material into a draft exam with a local LLM.

Pure logic lives here (material checks, batching, prompts, validation); `jobs.py` runs it in the
background and `routes/builder.py` is the web UI."""

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.llm import LLMClient, LLMError, LLMReplyError, ModelInfo, estimate_tokens
from app.prompts import language_name, render_prompt
from app.schemas import QUESTION_FIELDS, QuestionIn, format_validation_error

BATCH_SIZE = 5  # questions per LLM call
MAX_QUESTIONS = 100
PROMPT_OVERHEAD_TOKENS = 1500
TOKENS_PER_QUESTION = 600  # room reserved for the output
MAX_IMAGES = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024
TEXT_EXTENSIONS = (
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml",
    ".html", ".htm", ".tex", ".log",
)  # fmt: skip
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
QUESTION_TYPES = ("single", "multiple", "open")

_questions_adapter = TypeAdapter(list[QuestionIn])


class MaterialError(ValueError):
    """Source material the chosen model cannot take; carries a locale key and its parameters."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


class GenerationError(LLMError):
    """A batch stayed invalid after the repair retry; `errors` are the validation lines."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


@dataclass
class Material:
    text: str = ""
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (original name, content)


def accepted_extensions(info: ModelInfo) -> tuple[str, ...]:
    """File extensions the model can read: text always, images only with vision."""
    return TEXT_EXTENSIONS + (tuple(IMAGE_TYPES) if info.vision else ())


def read_material(
    pasted: str, files: list[tuple[str, bytes]], info: ModelInfo, model: str
) -> Material:
    """Pasted text plus uploaded files. Anything the model could not really read is refused,
    never silently dropped (no PDF/Office extraction: that would need guessing)."""
    parts = [pasted.strip()] if pasted.strip() else []
    images: list[tuple[str, bytes]] = []
    for name, content in files:
        suffix = Path(name).suffix.lower()
        if suffix in TEXT_EXTENSIONS:
            try:
                parts.append(f"### {name}\n{content.decode('utf-8-sig').strip()}")
            except UnicodeDecodeError:
                raise MaterialError("builder.err_not_utf8", name=name) from None
        elif suffix in IMAGE_TYPES:
            if not info.vision:
                raise MaterialError("builder.err_no_vision", name=name, model=model)
            if len(content) > MAX_IMAGE_BYTES:
                raise MaterialError("builder.err_image_size", name=name, mb=MAX_IMAGE_BYTES >> 20)
            images.append((name, content))
        else:
            raise MaterialError("builder.err_format", name=name, model=model)
    if len(images) > MAX_IMAGES:
        raise MaterialError("builder.err_too_many_images", n=MAX_IMAGES)
    return Material("\n\n".join(parts), images)


def output_reserve(count: int) -> int:
    """Tokens that must stay free for one batch's prompt overhead and reply."""
    return PROMPT_OVERHEAD_TOKENS + min(count, BATCH_SIZE) * TOKENS_PER_QUESTION


def context_usage(text: str, images: int, count: int) -> int:
    return estimate_tokens(text, images) + output_reserve(count)


def check_context(material: Material, count: int, info: ModelInfo) -> None:
    used = context_usage(material.text, len(material.images), count)
    if used > info.context_tokens:
        raise MaterialError("builder.err_context", used=used, limit=info.context_tokens)


def plan_types(single: int, multiple: int, open_: int) -> list[str]:
    return ["single"] * single + ["multiple"] * multiple + ["open"] * open_


def question_schema() -> dict[str, Any]:
    """One flat schema for all types (union schemas are poorly supported by local servers);
    `normalize_batch` keeps only the fields of each question's own type."""
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": list(QUESTION_TYPES)},
                        "topic": text,
                        "tags": {"type": "array", "items": text},
                        "source": text,
                        "scenario": text,
                        "question": text,
                        "points": {"type": "number"},
                        "options": {"type": "array", "items": text},
                        "correct": {"type": "array", "items": {"type": "integer"}},
                        "explanation": text,
                        "option_explanations": {"type": "array", "items": text},
                        "model_answer": text,
                        "rubric": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"criterion": text, "points": {"type": "number"}},
                                "required": ["criterion", "points"],
                            },
                        },
                    },
                    "required": ["type", "topic", "question"],
                },
            }
        },
        "required": ["questions"],
    }


def normalize_batch(
    reply: Any, batch_types: list[str], options: int, ids: list[str]
) -> list[dict[str, Any]]:
    """Validated question dicts (with ids) from a model reply, or ValueError with lines the
    model can act on. Fields foreign to a question's type are dropped, empty values too."""
    items = reply.get("questions") if isinstance(reply, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError('reply must be {"questions": [ ... ]}')
    wanted = sorted(batch_types)
    got = sorted(str(item.get("type")) for item in items)
    if got != wanted:
        raise ValueError(f"expected question types {wanted}, got {got}")
    cleaned = []
    for item, question_id in zip(items, ids, strict=True):
        keep = QUESTION_FIELDS[item["type"]]
        data = {k: v for k, v in item.items() if k in keep and v not in (None, "", [])}
        cleaned.append(data | {"id": question_id})
    try:
        questions = _questions_adapter.validate_python(cleaned)
    except ValidationError as exc:
        lines = format_validation_error(exc)
        raise ValueError("; ".join(f"questions{line}" for line in lines)) from exc
    for index, question in enumerate(questions):
        choices = getattr(question, "options", None)
        if choices is not None and len(choices) != options:
            raise ValueError(f"questions[{index}]: expected {options} options, got {len(choices)}")
    return [q.model_dump(mode="json", exclude_none=True) for q in questions]


def _user_message(prompt: str, images: list[tuple[str, bytes]]) -> dict[str, Any]:
    if not images:
        return {"role": "user", "content": prompt}
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for name, content in images:
        mime = IMAGE_TYPES[Path(name).suffix.lower()]
        url = f"data:{mime};base64,{base64.b64encode(content).decode()}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": parts}


def generate_batch(
    llm: LLMClient,
    model: str,
    params: dict[str, Any],
    material: Material,
    batch_types: list[str],
    existing: list[dict[str, Any]],
    ids: list[str],
    fixed_topic: str | None = None,
) -> list[dict[str, Any]]:
    """One LLM call for `batch_types`, with one repair retry that feeds the problem back.
    Raises GenerationError when the reply stays unusable, LLMError when the server fails."""
    prompt = render_prompt(
        "generate.txt",
        types=batch_types,
        counts={kind: batch_types.count(kind) for kind in QUESTION_TYPES if kind in batch_types},
        options=params["options"],
        topics=params.get("topics") or [],
        used_topics=sorted({q["topic"] for q in existing}),
        fixed_topic=fixed_topic,
        instructions=params.get("instructions") or "",
        avoid=[q["question"] for q in existing],
        material=material.text,
        images=material.images,
        language=language_name(params["language"]),
    )
    messages = [_user_message(prompt, material.images)]
    problem = ""
    for _ in range(2):
        reply = None
        try:
            reply = llm.chat(model, messages, question_schema())
            return normalize_batch(reply, batch_types, params["options"], ids)
        except (LLMReplyError, ValueError) as exc:
            problem = str(exc)
            if reply is not None:
                messages = [*messages, {"role": "assistant", "content": json.dumps(reply)}]
            hint = f"That reply is unusable: {problem}. Reply again with valid JSON only."
            messages = [*messages, {"role": "user", "content": hint}]
    raise GenerationError(f"the LLM gave no usable questions ({problem})", [problem])


def load_images(directory: Path, names: list[str]) -> list[tuple[str, bytes]]:
    files = [(name, directory / name) for name in names]
    return [(name, path.read_bytes()) for name, path in files if path.is_file()]

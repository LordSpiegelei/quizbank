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
from app.schemas import QUESTION_FIELDS, QuestionIn

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

question_adapter = TypeAdapter(QuestionIn)


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


def plan_slots(types: list[str], topic: str | None = None) -> list[dict[str, Any]]:
    """The plan a draft is generated from: one slot per question still to write."""
    return [{"type": kind, "topic": topic} for kind in types]


def question_schema(slots: list[dict[str, Any]]) -> dict[str, Any]:
    """One flat schema for all types (union schemas are poorly supported by local servers);
    `accept_batch` keeps only the fields of each question's own type. Count and types are
    limited to the batch, so a constrained server cannot run away with extra questions."""
    text = {"type": "string"}
    kinds = [kind for kind in QUESTION_TYPES if any(s["type"] == kind for s in slots)]
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "maxItems": len(slots),
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": kinds},
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


def _error_lines(exc: ValidationError) -> str:
    # loc starts with the union tag (the question type); leave it out
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'][1:]) or 'question'}: {e['msg']}" for e in exc.errors()
    )


def accept_batch(
    reply: Any, slots: list[dict[str, Any]], options: int | None, ids: list[str]
) -> tuple[list[tuple[int, dict[str, Any]]], list[str]]:
    """Fill as many slots as the reply allows: each item is validated on its own, each slot
    takes the first valid unused item of its type (and gets the slot's topic, if it has one),
    extra items are dropped. Returns ((slot index, question with id), problems); raises
    ValueError only if the reply is not a question list at all."""
    items = reply.get("questions") if isinstance(reply, dict) else None
    if not isinstance(items, list):
        raise ValueError('reply must be {"questions": [ ... ]}')
    problems: list[str] = []
    valid: dict[str, list[dict[str, Any]]] = {}
    for n, item in enumerate(items):
        kind = item.get("type") if isinstance(item, dict) else None
        if kind not in QUESTION_FIELDS:
            problems.append(f"questions[{n}]: unknown type {kind!r}")
            continue
        keep = QUESTION_FIELDS[kind]
        data = {k: v for k, v in item.items() if k in keep and v not in (None, "", [])}
        try:
            question = question_adapter.validate_python(data | {"id": "new"})
        except ValidationError as exc:
            problems.append(f"questions[{n}]: {_error_lines(exc)}")
            continue
        choices = getattr(question, "options", None)
        if options and choices is not None and len(choices) != options:
            problems.append(f"questions[{n}]: expected {options} options, got {len(choices)}")
            continue
        valid.setdefault(kind, []).append(question.model_dump(mode="json", exclude_none=True))

    filled: list[tuple[int, dict[str, Any]]] = []
    for index, slot in enumerate(slots):
        if valid.get(slot["type"]):
            question = valid[slot["type"]].pop(0)
            if slot.get("topic"):
                question["topic"] = slot["topic"]
            filled.append((index, question | {"id": ids[len(filled)]}))
    if extra := sum(len(rest) for rest in valid.values()):
        problems.append(f"{extra} extra question(s) dropped")
    if missing := len(slots) - len(filled):
        problems.append(f"{missing} of {len(slots)} requested question(s) missing")
    return filled, problems


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
    slots: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    ids: list[str],
) -> tuple[list[tuple[int, dict[str, Any]]], list[str]]:
    """One LLM call for `slots`; see `accept_batch` for the result. Only a reply that fills no
    slot at all gets the repair retry (with the problems fed back). Raises GenerationError
    when that stays empty too, LLMError when the server fails."""
    prompt = render_prompt(
        "generate.txt",
        slots=slots,
        options=params.get("options"),
        topics=params.get("topics") or [],
        used_topics=sorted({q["topic"] for q in existing}),
        instructions=params.get("instructions") or "",
        avoid=[q["question"] for q in existing],
        material=material.text,
        images=material.images,
        language=language_name(params["language"]),
    )
    messages = [_user_message(prompt, material.images)]
    problems: list[str] = []
    for _ in range(2):
        reply = None
        try:
            reply = llm.chat(
                model, messages, question_schema(slots), reserve_tokens=output_reserve(len(slots))
            )
            filled, problems = accept_batch(reply, slots, params.get("options"), ids)
        except (LLMReplyError, ValueError) as exc:
            filled, problems = [], [str(exc)]
        if filled:
            return filled, problems
        if reply is not None:
            messages = [*messages, {"role": "assistant", "content": json.dumps(reply)}]
        hint = f"That reply is unusable: {'; '.join(problems)}. Reply again with valid JSON only."
        messages = [*messages, {"role": "user", "content": hint}]
    raise GenerationError(f"the LLM gave no usable questions ({problems[0]})", problems)


def load_images(directory: Path, names: list[str]) -> list[tuple[str, bytes]]:
    files = [(name, directory / name) for name in names]
    return [(name, path.read_bytes()) for name, path in files if path.is_file()]

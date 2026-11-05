"""Builders for exam-file JSON and shared helpers used across tests."""

import json
from pathlib import Path

from sqlmodel import Session

from app.importer import ImportResult, import_exam
from app.schemas import parse_exam

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample-exam.json"


def single(**overrides) -> dict:
    return {
        "id": "q1",
        "type": "single",
        "topic": "Thema",
        "question": "Frage?",
        "options": ["A", "B", "C"],
        "correct": [1],
    } | overrides


def open_q(**overrides) -> dict:
    return {
        "id": "o1",
        "type": "open",
        "topic": "Fallstudie",
        "question": "Analysieren Sie …",
        "points": 5,
    } | overrides


def exam(*questions: dict, **overrides) -> str:
    data = {"format_version": 1, "course": "Kurs", "title": "Klausur"} | overrides
    data["questions"] = list(questions) or [single()]
    return json.dumps(data)


def import_file(session: Session, *questions: dict, **overrides) -> ImportResult:
    """Import an exam built by `exam()` straight into the database."""
    return import_exam(session, parse_exam(exam(*questions, **overrides)))


def upload(client, *files: tuple[str, bytes | str]):
    """POST files to the browser import form."""
    return client.post(
        "/import", files=[("files", (name, content, "application/json")) for name, content in files]
    )


def db(client) -> Session:
    """A session on the test app's database."""
    return Session(client.app.state.engine)


class FakeLLM:
    """Stands in for `LLMClient`: replies are queued, calls are recorded."""

    default_model = "fake"

    def __init__(self, *replies, models=("fake", "other"), vision=False, context_tokens=8192):
        self.replies = list(replies)
        self.models = models if isinstance(models, Exception) else list(models)
        self.calls: list[dict] = []
        self.vision = vision
        self.context_tokens = context_tokens

    def preferred_model(self, models):
        return self.default_model if self.default_model in models or not models else models[0]

    def list_models(self) -> list[str]:
        if isinstance(self.models, Exception):
            raise self.models
        return self.models

    def model_info(self, model: str):
        from app.llm import ModelInfo

        return ModelInfo(self.vision, self.context_tokens)

    def chat(self, model, messages, json_schema=None, reserve_tokens=2048):
        self.calls.append(
            {"model": model, "messages": messages, "schema": json_schema, "reserve": reserve_tokens}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

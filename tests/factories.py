"""Builders for exam-file JSON used across tests."""

import json
from pathlib import Path

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

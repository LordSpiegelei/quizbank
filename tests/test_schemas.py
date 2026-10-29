import pytest

from app.schemas import SCHEMA_PATH, ExamFileError, json_schema, parse_exam
from tests.factories import EXAMPLE, exam, open_q, single


def errors_for(raw: str) -> list[str]:
    with pytest.raises(ExamFileError) as exc_info:
        parse_exam(raw)
    return exc_info.value.errors


@pytest.mark.parametrize("path", sorted(EXAMPLE.parent.glob("*.json")), ids=lambda p: p.name)
def test_example_files_are_valid(path) -> None:
    exam_file = parse_exam(path.read_bytes())
    assert {q.type for q in exam_file.questions} == {"single", "multiple", "open"}


def test_committed_json_schema_is_current() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == json_schema(), (
        "run `uv run python -m app.schemas` to regenerate schema/exam.schema.json"
    )


def test_defaults() -> None:
    exam_file = parse_exam(exam())
    assert exam_file.multiple_scoring == "all_or_nothing"
    assert exam_file.questions[0].points == 1
    assert exam_file.questions[0].tags == []


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (single(correct=[5]), "questions[0].correct: index 5 out of range"),
        (single(correct=[-1]), "questions[0].correct: index -1 out of range"),
        (single(correct=[0, 1]), "questions[0].correct: List should have at most 1 item"),
        (single(type="multiple", correct=[1, 1]), "questions[0].correct: duplicate index"),
        (single(correct=[]), "questions[0].correct: List should have at least 1 item"),
        (single(options=["A"]), "questions[0].options: List should have at least 2 items"),
        (single(options=list("ABCDEFGHIJK")), "questions[0].options: List should have at most"),
        (
            single(option_explanations=["a"]),
            "questions[0].option_explanations: expected 3 entries (one per option), got 1",
        ),
        (single(corect=[1]), "questions[0].corect: Extra inputs are not permitted"),
        (single(topic=""), "questions[0].topic: String should have at least 1 character"),
        (single(type="essay"), "questions[0]: Input tag 'essay'"),
        (
            open_q(rubric=[{"criterion": "x", "points": 6}]),
            "questions[0].rubric: rubric points add up to more than the question's points (5)",
        ),
    ],
)
def test_question_errors(question: dict, expected: str) -> None:
    assert any(line.startswith(expected) for line in errors_for(exam(question))), expected


def test_error_path_points_at_the_right_question() -> None:
    errors = errors_for(exam(single(), single(id="q2", correct=[7])))
    assert errors == ["questions[1].correct: index 7 out of range"]


def test_duplicate_ids() -> None:
    errors = errors_for(exam(single(), single(id="q2"), single()))
    assert errors == ["questions: duplicate id 'q1' in questions[0] and questions[2]"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"format_version": 2}, "format_version: Input should be 1"),
        ({"course": ""}, "course: String should have at least 1 character"),
        ({"time_limit_minutes": 0}, "time_limit_minutes: Input should be greater than 0"),
        ({"multiple_scoring": "half"}, "multiple_scoring: Input should be"),
    ],
)
def test_exam_level_errors(overrides: dict, expected: str) -> None:
    assert any(line.startswith(expected) for line in errors_for(exam(**overrides)))


def test_invalid_json() -> None:
    assert errors_for("{nope")[0].startswith("Invalid JSON")

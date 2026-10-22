"""Import format for exam files (format_version 1).

Run `uv run python -m app.schemas` to regenerate schema/exam.schema.json.
"""

import json
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "exam.schema.json"

MIN_OPTIONS, MAX_OPTIONS = 2, 10

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RubricItem(_Strict):
    criterion: NonEmptyStr
    points: float = Field(ge=0)


# Cross-field checks below read earlier fields via `info.data`, so field order matters:
# `options` before `correct`/`option_explanations`, `points` (base) before `rubric`.
class _QuestionBase(_Strict):
    id: NonEmptyStr = Field(description="Unique within the file; re-imports match on it.")
    topic: NonEmptyStr = Field(description="Main topic, used for filters and statistics.")
    tags: list[NonEmptyStr] = []
    source: str | None = Field(default=None, description='E.g. "Foliensatz 2, Folie 14".')
    scenario: str | None = None
    question: NonEmptyStr
    points: float = Field(default=1, gt=0)
    explanation: str | None = None


class _ChoiceQuestion(_QuestionBase):
    options: list[NonEmptyStr] = Field(min_length=MIN_OPTIONS, max_length=MAX_OPTIONS)
    correct: list[int] = Field(min_length=1, description="0-based indices into options.")
    option_explanations: list[str] | None = None

    @field_validator("correct")
    @classmethod
    def _check_correct(cls, correct: list[int], info: ValidationInfo) -> list[int]:
        options = info.data.get("options")
        if options is not None:
            for index in correct:
                if not 0 <= index < len(options):
                    raise PydanticCustomError(
                        "index_out_of_range", "index {index} out of range", {"index": index}
                    )
        if len(set(correct)) != len(correct):
            raise PydanticCustomError("duplicate_index", "duplicate index")
        return correct

    @field_validator("option_explanations")
    @classmethod
    def _check_option_explanations(
        cls, explanations: list[str] | None, info: ValidationInfo
    ) -> list[str] | None:
        options = info.data.get("options")
        if explanations is not None and options is not None and len(explanations) != len(options):
            raise PydanticCustomError(
                "length_mismatch",
                "expected {expected} entries (one per option), got {actual}",
                {"expected": len(options), "actual": len(explanations)},
            )
        return explanations


class SingleChoiceQuestion(_ChoiceQuestion):
    type: Literal["single"]
    correct: list[int] = Field(
        min_length=1, max_length=1, description="Exactly one 0-based index into options."
    )


class MultipleChoiceQuestion(_ChoiceQuestion):
    type: Literal["multiple"]


class OpenQuestion(_QuestionBase):
    type: Literal["open"]
    model_answer: str | None = None
    rubric: list[RubricItem] | None = None

    @field_validator("rubric")
    @classmethod
    def _check_rubric(
        cls, rubric: list[RubricItem] | None, info: ValidationInfo
    ) -> list[RubricItem] | None:
        points = info.data.get("points")
        if rubric and points is not None and sum(item.points for item in rubric) > points:
            raise PydanticCustomError(
                "rubric_exceeds_points",
                "rubric points add up to more than the question's points ({points})",
                {"points": f"{points:g}"},
            )
        return rubric


QUESTION_MODELS = (SingleChoiceQuestion, MultipleChoiceQuestion, OpenQuestion)
QuestionIn = Annotated[
    SingleChoiceQuestion | MultipleChoiceQuestion | OpenQuestion, Field(discriminator="type")
]
_QUESTION_TAGS = {get_args(m.model_fields["type"].annotation)[0] for m in QUESTION_MODELS}


class ExamFile(_Strict):
    format_version: Literal[1]
    course: NonEmptyStr
    title: NonEmptyStr
    time_limit_minutes: int | None = Field(default=None, gt=0)
    pass_mark: float | None = Field(default=None, ge=0)
    multiple_scoring: Literal["all_or_nothing", "partial"] = "all_or_nothing"
    questions: list[QuestionIn] = Field(min_length=1)

    @field_validator("questions")
    @classmethod
    def _check_unique_ids(cls, questions: list[QuestionIn]) -> list[QuestionIn]:
        first_index: dict[str, int] = {}
        for index, question in enumerate(questions):
            if question.id in first_index:
                raise PydanticCustomError(
                    "duplicate_id",
                    "duplicate id '{id}' in questions[{first}] and questions[{index}]",
                    {"id": question.id, "first": first_index[question.id], "index": index},
                )
            first_index[question.id] = index
        return questions


class ExamFileError(ValueError):
    """Raised when an exam file cannot be parsed; `errors` are human-readable lines."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _format_loc(loc: tuple[int | str, ...]) -> str:
    path = ""
    previous: int | str | None = None
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        elif not (isinstance(previous, int) and part in _QUESTION_TAGS):
            # pydantic inserts the union tag right after the list index; leave it out
            path += f".{part}" if path else part
        previous = part
    return path


def format_validation_error(error: ValidationError) -> list[str]:
    """`questions[4].correct: index 5 out of range` style messages."""
    lines = []
    for item in error.errors():
        path = _format_loc(item["loc"])
        lines.append(f"{path}: {item['msg']}" if path else item["msg"])
    return lines


def parse_exam(raw: str | bytes) -> ExamFile:
    try:
        return ExamFile.model_validate_json(raw)
    except ValidationError as exc:
        raise ExamFileError(format_validation_error(exc)) from exc


def json_schema() -> str:
    return json.dumps(ExamFile.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(json_schema(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")

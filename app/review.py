"""AI review of answers that need review (see `scoring.needs_review`): the LLM only *suggests*
scores and feedback. They are stored beside the answer, never as `self_score`; the user confirms
them by saving the self-assessment form."""

from typing import Any

from sqlmodel import Session

from app.llm import LLMClient, LLMReplyError, OnText
from app.models import Answer, Attempt, utcnow
from app.prompts import language_name, render_prompt
from app.scoring import needs_review, self_score_from


def review_schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": count,
                "maxItems": count,
            },
            "feedback": {"type": "string"},
        },
        "required": ["scores", "feedback"],
        "additionalProperties": False,
    }


def _limits(snap: dict[str, Any]) -> list[float]:
    rubric = snap.get("rubric")
    return [item["points"] for item in rubric] if rubric else [snap["points"]]


def parse_review(reply: Any, limits: list[float]) -> tuple[list[float], str]:
    """Validated (scores, feedback) of a reply. Scores are clamped into 0..limit (models are
    sloppy about maxima); a wrong shape or count raises ValueError."""
    if not isinstance(reply, dict):
        raise ValueError("the reply must be a JSON object")
    scores, feedback = reply.get("scores"), reply.get("feedback")
    if not isinstance(scores, list) or len(scores) != len(limits):
        raise ValueError(f'"scores" must be a list of {len(limits)} number(s)')
    if any(isinstance(s, bool) or not isinstance(s, int | float) for s in scores):
        raise ValueError('"scores" must contain numbers only')
    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError('"feedback" must be a non-empty string')
    clamped = [
        round(min(max(float(s), 0), limit), 2) for s, limit in zip(scores, limits, strict=True)
    ]
    return clamped, feedback.strip()


def ai_review(
    session: Session,
    llm: LLMClient,
    model: str,
    attempt: Attempt,
    answer: Answer,
    language: str,
    on_text: OnText | None = None,
) -> None:
    """Ask `model` to assess `answer` and store the suggestion (`on_text` follows it live).
    One retry, with the problem spelled out, if the reply is unusable; LLMError (unreachable,
    still unusable) propagates and nothing is stored. Raises ValueError for answers that are
    not reviewable yet."""
    snap = answer.snapshot
    if attempt.finished_at is None:
        raise ValueError("review after submitting")
    if not needs_review(snap["type"]):
        raise ValueError("this question has a fixed answer key")
    limits = _limits(snap)
    prompt = render_prompt(
        "review.txt",
        q=snap,
        answer=(answer.text_answer or "").strip(),
        count=len(limits),
        language=language_name(language),
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    for attempt_no in range(2):
        try:
            reply = llm.chat(model, messages, review_schema(len(limits)), on_text=on_text)
            scores, feedback = parse_review(reply, limits)
            break
        except (ValueError, LLMReplyError) as exc:
            if attempt_no:
                raise LLMReplyError(f"the LLM gave no usable review ({exc})") from exc
            if on_text:
                on_text("meta", f"unusable reply ({exc}), asking again")
            hint = f"Your reply was unusable ({exc}). Reply again with valid JSON only."
            messages = [*messages, {"role": "user", "content": hint}]
    self_score_from(snap["points"], snap.get("rubric"), scores)  # same check as the form
    answer.ai_scores, answer.ai_feedback = scores, feedback
    answer.ai_model, answer.ai_reviewed_at = model, utcnow()
    session.commit()

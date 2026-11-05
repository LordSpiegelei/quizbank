import json
import logging
import queue
import threading
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from app.attempts import (
    AttemptLocked,
    NoQuestions,
    StartOptions,
    finish,
    open_attempt,
    restart,
    reveal,
    review_item,
    review_items,
    save_answer,
    self_assess,
    start_attempt,
)
from app.db import SessionDep, get_or_404
from app.forms import optional_number
from app.learning import FLAGS
from app.llm import LLMClient, LLMError, get_llm
from app.markdown import render_markdown, render_markdown_inline
from app.models import Answer, Attempt, Exam, Question, utcnow
from app.queries import live_questions
from app.review import ai_review
from app.routes.exams import exam_page
from app.scoring import needs_review
from app.templating import redirect, render

router = APIRouter()
logger = logging.getLogger(__name__)

# Texts the Alpine runner needs; everything else is rendered server-side.
QUIZ_TEXT_KEYS = (
    "quiz.saving",
    "quiz.saved",
    "quiz.save_failed",
    "quiz.finish_confirm",
    "quiz.finish_confirm_unanswered",
    "quiz.reveal_failed",
    "quiz.keys",
    "quiz.keys_options",
    "quiz.answered_of",
    "common.points",
)


@router.post("/exams/{exam_id}/attempts")
def start_attempt_form(
    request: Request,
    session: SessionDep,
    exam_id: int,
    options: Annotated[StartOptions, Form()],
):
    exam = get_or_404(session, Exam, exam_id)
    attempt = open_attempt(session, exam.id)
    if attempt is None:
        try:
            attempt = start_attempt(session, exam, options)
        except NoQuestions:
            error = request.app.state.t("start.no_questions")
            return exam_page(request, session, exam, status_code=422, error=error)
    return redirect(request, "attempt_page", attempt_id=attempt.id)


@router.post("/attempts/{attempt_id}/restart")
def restart_attempt(request: Request, session: SessionDep, attempt_id: int):
    """Discard the unfinished attempt and start a fresh one."""
    attempt = restart(session, get_or_404(session, Attempt, attempt_id))
    return redirect(request, "attempt_page", attempt_id=attempt.id)


def _solution(request: Request, attempt: Attempt, answer: Answer) -> dict[str, Any]:
    """Everything instant feedback shows once an answer is revealed (the review page's item)."""
    item, snap = review_item(answer, attempt.multiple_scoring), answer.snapshot
    return {
        "status": item.status,
        "status_label": request.app.state.t(f"review.status.{item.status}"),
        "score": item.result.score,
        "correct": [i for i, option in enumerate(item.options) if option.is_correct],
        "explanation": render_markdown(snap["explanation"]),
        "option_explanations": [render_markdown(option.explanation) for option in item.options],
        "source": snap["source"],
        "model_answer": render_markdown(snap["model_answer"]),
        "rubric": snap["rubric"] or [],
    }


def _flags(request: Request, question: Question | None) -> dict[str, Any]:
    """Star/disable state and where to set it; empty once the question left the exam file."""
    if question is None:
        return {"flags": None, "flagUrls": None}
    url = request.app.url_path_for
    return {
        "flags": {flag: getattr(question, flag) for flag in FLAGS},
        "flagUrls": {
            flag: str(url("set_flag_api", question_id=question.id, flag=flag)) for flag in FLAGS
        },
    }


def _client_question(
    request: Request, attempt: Attempt, answer: Answer, question: Question | None
) -> dict[str, Any]:
    """What the runner may see: solutions only for answers already revealed."""
    snap = answer.snapshot
    params = {"attempt_id": answer.attempt_id, "answer_id": answer.id}
    return {
        **_flags(request, question),
        "id": answer.id,
        "url": str(request.app.url_path_for("save_answer_api", **params)),
        "revealUrl": str(request.app.url_path_for("reveal_answer_api", **params)),
        "solution": _solution(request, attempt, answer) if answer.revealed else None,
        "type": snap["type"],
        "topic": snap["topic"],
        "points": snap["points"],
        "scenario": render_markdown(snap["scenario"]),
        "question": render_markdown(snap["question"]),
        "options": [render_markdown_inline(option) for option in snap["options"] or []],
        "selected": answer.selected or [],
        "text_answer": answer.text_answer or "",
    }


@router.get("/attempts/{attempt_id}", response_class=HTMLResponse)
def attempt_page(request: Request, session: SessionDep, attempt_id: int):
    attempt = get_or_404(session, Attempt, attempt_id)
    if attempt.finished_at is not None:
        return redirect(request, "attempt_review", attempt_id=attempt.id)
    now = utcnow()
    remaining = None
    if attempt.deadline is not None:
        remaining = int((attempt.deadline - now).total_seconds())
    t = request.app.state.t
    live = live_questions(session, attempt)
    quiz = {
        "feedback": attempt.feedback,
        "remaining": remaining,
        "elapsed": int((now - attempt.started_at).total_seconds()),
        "questions": [
            _client_question(request, attempt, answer, live.get(answer.question_id))
            for answer in attempt.answers
        ],
        # with each key's singular variant, if it has one (t returns a missing key as is)
        "texts": {k: t(k) for key in QUIZ_TEXT_KEYS for k in (key, f"{key}.one") if t(k) != k},
    }
    return render(request, "attempt.html", attempt=attempt, quiz=quiz)


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: list[int] | None = None
    text_answer: str | None = None


def _attempt_answer(session: Session, attempt_id: int, answer_id: int) -> tuple[Attempt, Answer]:
    attempt = get_or_404(session, Attempt, attempt_id)
    answer = get_or_404(session, Answer, answer_id)
    if answer.attempt_id != attempt.id:
        raise HTTPException(status_code=404)
    return attempt, answer


@router.put("/api/attempts/{attempt_id}/answers/{answer_id}", name="save_answer_api")
def save_answer_api(session: SessionDep, attempt_id: int, answer_id: int, body: AnswerIn):
    attempt, answer = _attempt_answer(session, attempt_id, answer_id)
    try:
        save_answer(session, attempt, answer, body.selected, body.text_answer)
    except AttemptLocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(status_code=204)


@router.post("/api/attempts/{attempt_id}/answers/{answer_id}/reveal", name="reveal_answer_api")
def reveal_answer_api(request: Request, session: SessionDep, attempt_id: int, answer_id: int):
    attempt, answer = _attempt_answer(session, attempt_id, answer_id)
    try:
        reveal(session, attempt, answer)
    except AttemptLocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _solution(request, attempt, answer)


@router.post("/attempts/{attempt_id}/finish")
def finish_attempt(request: Request, session: SessionDep, attempt_id: int):
    finish(session, get_or_404(session, Attempt, attempt_id))
    return redirect(request, "attempt_review", attempt_id=attempt_id)


@router.get("/attempts/{attempt_id}/review", response_class=HTMLResponse)
def attempt_review(request: Request, session: SessionDep, attempt_id: int):
    attempt = get_or_404(session, Attempt, attempt_id)
    if attempt.finished_at is None:
        return redirect(request, "attempt_page", attempt_id=attempt.id)
    return _review_page(request, session, attempt)


def _llm_models(request: Request, attempt: Attempt) -> list[str] | None:
    """Models offered for AI review; None when there is no LLM or nothing to review, an empty
    list when the server cannot be reached."""
    llm = request.app.state.llm
    if llm is None or not any(needs_review(a.snapshot["type"]) for a in attempt.answers):
        return None
    try:
        return llm.list_models()
    except LLMError:
        return []


def _review_page(
    request: Request, session: Session, attempt: Attempt, status_code: int = 200, **extra
) -> HTMLResponse:
    llm = request.app.state.llm
    models = _llm_models(request, attempt)
    return render(
        request,
        "review.html",
        status_code=status_code,
        attempt=attempt,
        exam=attempt.exam,
        items=review_items(attempt),
        questions=live_questions(session, attempt),
        llm_models=models,
        llm_default=llm.preferred_model(models) if llm and models else None,
        **extra,
    )


@router.post("/attempts/{attempt_id}/answers/{answer_id}/assess")
def self_assess_form(
    request: Request,
    session: SessionDep,
    attempt_id: int,
    answer_id: int,
    rubric: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies defaults)
    self_score: Annotated[str, Form()] = "",
):
    """One score per rubric criterion, or `self_score` for an open question without rubric."""
    attempt, answer = _attempt_answer(session, attempt_id, answer_id)
    try:
        scores = [optional_number(raw, float) or 0.0 for raw in rubric or [self_score]]
        self_assess(session, attempt, answer, scores)
    except ValueError:  # FormError is one too
        error = request.app.state.t("assess.invalid")
        return _review_page(request, session, attempt, status_code=422, error=error)
    fragment = f"q{answer.position + 1}"
    return redirect(request, "attempt_review", fragment=fragment, attempt_id=attempt.id)


class AIReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None


def _review_result(answer: Answer) -> dict[str, Any]:
    return {
        "scores": answer.ai_scores,
        "feedback_html": render_markdown(answer.ai_feedback or ""),
        "model": answer.ai_model,
    }


def _review_stream(
    engine: Any, llm: LLMClient, model: str, attempt_id: int, answer_id: int, language: str
) -> Iterator[str]:
    """NDJSON: {"kind", "text"} while the model writes, then {"result": …} or {"error": …}.
    The review runs in its own thread and session, the generator just relays its output."""
    items: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def work() -> None:
        try:
            with Session(engine) as session:
                attempt = session.get(Attempt, attempt_id)
                answer = session.get(Answer, answer_id)
                emit = lambda kind, text: items.put({"kind": kind, "text": text})  # noqa: E731
                ai_review(session, llm, model, attempt, answer, language, on_text=emit)
                items.put({"result": _review_result(answer)})
        except (ValueError, LLMError) as exc:
            items.put({"error": str(exc)})
        except Exception as exc:  # report, never hang the stream
            logger.exception("AI review failed")
            items.put({"error": f"unexpected error: {exc}"})
        finally:
            items.put(None)

    threading.Thread(target=work, name="quizbank-review", daemon=True).start()
    while (item := items.get()) is not None:
        yield json.dumps(item, ensure_ascii=False) + "\n"


@router.post("/api/attempts/{attempt_id}/answers/{answer_id}/ai-review", name="ai_review_api")
def ai_review_api(
    request: Request,
    session: SessionDep,
    llm: Annotated[LLMClient, Depends(get_llm)],
    attempt_id: int,
    answer_id: int,
    body: AIReviewIn,
    stream: bool = False,
):
    """Suggest scores and feedback for an answer; the user still confirms them by saving.
    With `stream`, the model's text is relayed live as NDJSON (see `_review_stream`)."""
    attempt, answer = _attempt_answer(session, attempt_id, answer_id)
    model = body.model or llm.default_model
    if not model:
        raise HTTPException(status_code=422, detail=request.app.state.t("ai.no_model"))
    if attempt.finished_at is None:
        raise HTTPException(status_code=409, detail="review after submitting")
    if not needs_review(answer.snapshot["type"]):
        raise HTTPException(status_code=422, detail="this question has a fixed answer key")
    language = request.app.state.settings.app_lang
    if stream:
        events = _review_stream(
            request.app.state.engine, llm, model, attempt.id, answer.id, language
        )
        return StreamingResponse(events, media_type="application/x-ndjson")
    try:
        ai_review(session, llm, model, attempt, answer, language)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return _review_result(answer)


@router.post("/attempts/{attempt_id}/delete")
def delete_attempt(request: Request, session: SessionDep, attempt_id: int):
    attempt = get_or_404(session, Attempt, attempt_id)
    exam_id = attempt.exam_id
    session.delete(attempt)
    session.commit()
    return redirect(request, "exam_detail", exam_id=exam_id)

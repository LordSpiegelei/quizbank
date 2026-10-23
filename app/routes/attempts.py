from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from app.attempts import (
    AttemptLocked,
    finish,
    open_attempt,
    restart,
    review_items,
    save_answer,
    start_exam_attempt,
)
from app.db import SessionDep, get_or_404
from app.markdown import render_markdown, render_markdown_inline
from app.models import Answer, Attempt, Exam, utcnow
from app.templating import redirect, render

router = APIRouter()

# Texts the Alpine runner needs; everything else is rendered server-side.
QUIZ_TEXT_KEYS = (
    "quiz.saving",
    "quiz.saved",
    "quiz.save_failed",
    "quiz.finish_confirm",
    "quiz.finish_confirm_unanswered",
)


@router.post("/exams/{exam_id}/attempts")
def start_attempt(request: Request, session: SessionDep, exam_id: int):
    exam = get_or_404(session, Exam, exam_id)
    attempt = open_attempt(session, exam.id) or start_exam_attempt(session, exam)
    return redirect(request, "attempt_page", attempt_id=attempt.id)


@router.post("/attempts/{attempt_id}/restart")
def restart_attempt(request: Request, session: SessionDep, attempt_id: int):
    """Discard the unfinished attempt and start a fresh one."""
    attempt = restart(session, get_or_404(session, Attempt, attempt_id))
    return redirect(request, "attempt_page", attempt_id=attempt.id)


def _client_question(request: Request, answer: Answer) -> dict[str, Any]:
    """What the runner may see before submitting: no solutions or explanations."""
    snap = answer.snapshot
    url = request.app.url_path_for(
        "save_answer_api", attempt_id=answer.attempt_id, answer_id=answer.id
    )
    return {
        "id": answer.id,
        "url": str(url),
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
    remaining = None
    if attempt.deadline is not None:
        remaining = int((attempt.deadline - utcnow()).total_seconds())
    t = request.app.state.t
    quiz = {
        "remaining": remaining,
        "questions": [_client_question(request, answer) for answer in attempt.answers],
        "texts": {key: t(key) for key in QUIZ_TEXT_KEYS},
    }
    return render(request, "attempt.html", attempt=attempt, quiz=quiz)


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: list[int] | None = None
    text_answer: str | None = None


@router.put("/api/attempts/{attempt_id}/answers/{answer_id}", name="save_answer_api")
def save_answer_api(session: SessionDep, attempt_id: int, answer_id: int, body: AnswerIn):
    attempt = get_or_404(session, Attempt, attempt_id)
    answer = get_or_404(session, Answer, answer_id)
    if answer.attempt_id != attempt.id:
        raise HTTPException(status_code=404)
    try:
        save_answer(session, attempt, answer, body.selected, body.text_answer)
    except AttemptLocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(status_code=204)


@router.post("/attempts/{attempt_id}/finish")
def finish_attempt(request: Request, session: SessionDep, attempt_id: int):
    finish(session, get_or_404(session, Attempt, attempt_id))
    return redirect(request, "attempt_review", attempt_id=attempt_id)


@router.get("/attempts/{attempt_id}/review", response_class=HTMLResponse)
def attempt_review(request: Request, session: SessionDep, attempt_id: int):
    attempt = get_or_404(session, Attempt, attempt_id)
    if attempt.finished_at is None:
        return redirect(request, "attempt_page", attempt_id=attempt.id)
    return render(
        request, "review.html", attempt=attempt, exam=attempt.exam, items=review_items(attempt)
    )


@router.post("/attempts/{attempt_id}/delete")
def delete_attempt(request: Request, session: SessionDep, attempt_id: int):
    attempt = get_or_404(session, Attempt, attempt_id)
    exam_id = attempt.exam_id
    session.delete(attempt)
    session.commit()
    return redirect(request, "exam_detail", exam_id=exam_id)

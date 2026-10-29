"""Learning state: stars, disabled flags, the wrong pool, and the resets."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from pydantic import BaseModel

from app.db import SessionDep, get_or_404
from app.learning import Flag, delete_history, remove_from_pool, reset_learning_state, set_flag
from app.models import Exam, Question
from app.templating import redirect, redirect_back

router = APIRouter()


@router.post("/questions/{question_id}/flags/{flag}")
def set_flag_form(
    request: Request,
    session: SessionDep,
    question_id: int,
    flag: Flag,
    value: Annotated[bool, Form()],
    next_url: Annotated[str, Form(alias="next")] = "",
):
    """Star/disable buttons on the exam and review pages; returns to where they were."""
    question = get_or_404(session, Question, question_id)
    set_flag(session, question, flag, value)
    return redirect_back(request, next_url, "exam_detail", exam_id=question.exam_id)


class FlagIn(BaseModel):
    value: bool


@router.post("/api/questions/{question_id}/flags/{flag}", name="set_flag_api")
def set_flag_api(session: SessionDep, question_id: int, flag: Flag, body: FlagIn):
    """The same for the quiz runner."""
    set_flag(session, get_or_404(session, Question, question_id), flag, body.value)
    return {flag: body.value}


@router.post("/questions/{question_id}/pool/remove")
def remove_from_pool_form(request: Request, session: SessionDep, question_id: int):
    question = get_or_404(session, Question, question_id)
    remove_from_pool(session, question)
    return redirect(request, "exam_detail", exam_id=question.exam_id)


@router.post("/exams/{exam_id}/learning/reset")
def reset_exam_learning(request: Request, session: SessionDep, exam_id: int):
    exam = get_or_404(session, Exam, exam_id)
    reset_learning_state(session, exam.id)
    return redirect(request, "exam_detail", exam_id=exam.id)


@router.post("/learning/reset")
def reset_all_learning(request: Request, session: SessionDep):
    reset_learning_state(session)
    return redirect(request, "course_list")


@router.post("/exams/{exam_id}/history/delete")
def delete_exam_history(request: Request, session: SessionDep, exam_id: int):
    exam = get_or_404(session, Exam, exam_id)
    delete_history(session, exam.id)
    return redirect(request, "exam_detail", exam_id=exam.id)

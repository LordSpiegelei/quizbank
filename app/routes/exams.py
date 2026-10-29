from dataclasses import dataclass
from typing import Annotated, Literal, get_args
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.attempts import askable, open_attempt
from app.db import SessionDep, get_or_404
from app.exporter import export_exam
from app.importer import ImportResult, comes_back_on_restart, import_exam
from app.learning import QuestionFilter, filter_counts
from app.models import Exam
from app.queries import finished_attempts, topic_counts
from app.schemas import ExamFile, ExamFileError, parse_exam
from app.scoring import POOL_EXIT_STREAK
from app.stats import history_chart, topic_stats
from app.templating import redirect, render

router = APIRouter()


def _delete_confirm(request: Request, exam: Exam) -> str:
    t = request.app.state.t
    text = t("exam.delete_confirm", title=exam.title)
    if comes_back_on_restart(request.app.state.settings, exam):
        text += " " + t("exam.reimport_hint", file=exam.source_file)
    return text


def exam_page(
    request: Request,
    session: Session,
    exam: Exam,
    status_code: int = 200,
    tab: str | None = None,
    question_filter: str = "all",
    **extra,
) -> HTMLResponse:
    """The exam page with its start dialog; also rendered when starting an attempt fails."""
    questions = exam.questions
    history = finished_attempts(session, exam.id)
    return render(
        request,
        "exam.html",
        status_code=status_code,
        exam=exam,
        question_count=len(questions),
        max_score=sum(q.points for q in questions),
        topics=sorted({q.topic for q in questions}, key=str.casefold),
        tags=sorted({tag for q in questions for tag in q.tags}, key=str.casefold),
        questions=questions,
        filter_counts=filter_counts(questions),
        pool_retryable=sum(askable(q, "retry_wrong") for q in questions),
        pool_exit_streak=POOL_EXIT_STREAK,
        open_attempt=open_attempt(session, exam.id),
        delete_confirm=_delete_confirm(request, exam),
        history=history,
        topic_stats=topic_stats(topic_counts(session, exam.id)),
        chart=history_chart(history),
        tabs=get_args(ExamTab),
        tab=tab,
        question_filter=question_filter,
        **extra,
    )


ExamTab = Literal["history", "stats", "questions"]


@router.get("/exams/{exam_id}", response_class=HTMLResponse)
def exam_detail(
    request: Request,
    session: SessionDep,
    exam_id: int,
    tab: ExamTab | None = None,
    question_filter: Annotated[QuestionFilter, Query(alias="filter")] = "all",
):
    """`tab` and `filter` restore the view a question action was taken from (shell.js keeps
    them in the URL)."""
    exam = get_or_404(session, Exam, exam_id)
    return exam_page(request, session, exam, tab=tab, question_filter=question_filter)


@router.get("/exams/{exam_id}/export")
def export_exam_file(session: SessionDep, exam_id: int):
    exam = get_or_404(session, Exam, exam_id)
    filename = exam.source_file or f"{exam.title}.json"
    return Response(
        export_exam(exam),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/exams/{exam_id}/delete")
def delete_exam(request: Request, session: SessionDep, exam_id: int):
    exam = get_or_404(session, Exam, exam_id)
    course_id = exam.course_id
    session.delete(exam)
    session.commit()
    return redirect(request, "course_detail", course_id=course_id)


@dataclass
class UploadOutcome:
    filename: str
    exam_file: ExamFile | None = None
    result: ImportResult | None = None
    errors: list[str] | None = None


def _import_upload(session: Session, filename: str, content: bytes) -> UploadOutcome:
    outcome = UploadOutcome(filename)
    try:
        outcome.exam_file = parse_exam(content)
    except ExamFileError as exc:
        outcome.errors = exc.errors
    else:
        outcome.result = import_exam(session, outcome.exam_file, source_file=filename)
    return outcome


@router.get("/import", response_class=HTMLResponse)
def import_form(request: Request):
    return render(request, "import.html")


@router.post("/import", response_class=HTMLResponse)
async def import_upload(request: Request, session: SessionDep):
    # Read the form directly: an empty file input arrives as "" or a nameless part.
    form = await request.form()
    uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
    if not uploads:
        error = request.app.state.t("import.no_files")
        return render(request, "import.html", status_code=400, error=error)

    outcomes = [
        await run_in_threadpool(_import_upload, session, upload.filename, await upload.read())
        for upload in uploads
    ]
    return render(request, "import.html", outcomes=outcomes)

from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, func, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.attempts import open_attempt
from app.db import SessionDep, get_or_404
from app.importer import ImportResult, import_exam
from app.models import Exam, Question
from app.queries import finished_attempts
from app.schemas import ExamFile, ExamFileError, parse_exam
from app.templating import redirect, render

router = APIRouter()


@router.get("/exams/{exam_id}", response_class=HTMLResponse)
def exam_detail(request: Request, session: SessionDep, exam_id: int):
    exam = get_or_404(session, Exam, exam_id)
    question_count, max_score = session.exec(
        select(func.count(), func.sum(Question.points)).where(Question.exam_id == exam.id)
    ).one()
    topics = session.exec(
        select(Question.topic)
        .distinct()
        .where(Question.exam_id == exam.id)
        .order_by(func.casefold(Question.topic))
    ).all()
    return render(
        request,
        "exam.html",
        exam=exam,
        question_count=question_count,
        max_score=max_score or 0,
        topics=topics,
        open_attempt=open_attempt(session, exam.id),
        history=finished_attempts(session, exam.id),
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

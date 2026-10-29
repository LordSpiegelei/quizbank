from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.attempts import open_attempt
from app.db import SessionDep, get_or_404
from app.importer import ImportResult, comes_back_on_restart, import_exam
from app.models import Exam
from app.queries import finished_attempts
from app.schemas import ExamFile, ExamFileError, parse_exam
from app.templating import redirect, render

router = APIRouter()


def _delete_confirm(request: Request, exam: Exam) -> str:
    t = request.app.state.t
    text = t("exam.delete_confirm", title=exam.title)
    if comes_back_on_restart(request.app.state.settings, exam):
        text += " " + t("exam.reimport_hint", file=exam.source_file)
    return text


def exam_page(
    request: Request, session: Session, exam: Exam, status_code: int = 200, **extra
) -> HTMLResponse:
    """The exam page with its start dialog; also rendered when starting an attempt fails."""
    questions = exam.questions
    return render(
        request,
        "exam.html",
        status_code=status_code,
        exam=exam,
        question_count=len(questions),
        max_score=sum(q.points for q in questions),
        topics=sorted({q.topic for q in questions}, key=str.casefold),
        tags=sorted({tag for q in questions for tag in q.tags}, key=str.casefold),
        open_attempt=open_attempt(session, exam.id),
        delete_confirm=_delete_confirm(request, exam),
        history=finished_attempts(session, exam.id),
        **extra,
    )


@router.get("/exams/{exam_id}", response_class=HTMLResponse)
def exam_detail(request: Request, session: SessionDep, exam_id: int):
    return exam_page(request, session, get_or_404(session, Exam, exam_id))


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

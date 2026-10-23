from typing import Annotated, Literal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, col, func, select

from app.db import SessionDep, get_or_404
from app.models import Attempt, Course, Exam, Question
from app.queries import last_finished_attempts
from app.templating import redirect, render

router = APIRouter()

SortKey = Literal["name", "recent"]


class FormError(ValueError):
    """Validation failure; carries a locale key so callers stay free of translation."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


def _valid_name(session: Session, name: str, course_id: int | None) -> str:
    name = name.strip()
    if not name:
        raise FormError("error.name_required")
    clash = session.exec(select(Course).where(Course.name == name)).first()
    if clash is not None and clash.id != course_id:
        raise FormError("error.name_taken")
    return name


def _optional_number[T: (int, float)](raw: str, kind: type[T]) -> T | None:
    value = raw.strip()
    if not value:
        return None
    try:
        number = kind(value.replace(",", "."))  # accept decimal comma and point
    except ValueError:
        raise FormError("error.not_a_number", value=value) from None
    if number < 0:
        raise FormError("error.not_a_number", value=value)
    return number


def _error_text(request: Request, exc: FormError) -> str:
    return request.app.state.t(exc.key, **exc.params)


def _list_page(
    request: Request, session: Session, q: str = "", sort: SortKey = "name", **extra
) -> HTMLResponse:
    last_practiced = func.max(Attempt.started_at)
    by_name = func.casefold(Course.name)
    stmt = (
        select(Course, func.count(func.distinct(Exam.id)), last_practiced)
        .outerjoin(Exam, col(Exam.course_id) == Course.id)
        .outerjoin(Attempt, col(Attempt.exam_id) == Exam.id)
        .where(by_name.contains(q.strip().casefold(), autoescape=True))
        .group_by(col(Course.id))
        .order_by(*([last_practiced.desc().nulls_last()] if sort == "recent" else []), by_name)
    )
    courses = [
        {"course": course, "exam_count": count, "last_practiced": last}
        for course, count, last in session.exec(stmt)
    ]
    return render(request, "courses.html", courses=courses, q=q, sort=sort, **extra)


@router.get("/", response_class=HTMLResponse)
def course_list(request: Request, session: SessionDep, q: str = "", sort: SortKey = "name"):
    return _list_page(request, session, q, sort)


@router.post("/courses")
def create_course(request: Request, session: SessionDep, name: Annotated[str, Form()] = ""):
    try:
        course = Course(name=_valid_name(session, name, None))
    except FormError as exc:
        error = _error_text(request, exc)
        return _list_page(request, session, status_code=400, error=error, new_name=name)
    session.add(course)
    session.commit()
    return redirect(request, "course_detail", course_id=course.id)


def _course_page(request: Request, session: Session, course: Course, **extra) -> HTMLResponse:
    exams = session.exec(
        select(Exam).where(Exam.course_id == course.id).order_by(func.casefold(Exam.title))
    ).all()
    exam_ids = [exam.id for exam in exams]
    question_counts = dict(
        session.exec(
            select(Question.exam_id, func.count())
            .where(col(Question.exam_id).in_(exam_ids))
            .group_by(col(Question.exam_id))
        ).all()
    )
    return render(
        request,
        "course.html",
        course=course,
        exams=exams,
        question_counts=question_counts,
        last_attempts=last_finished_attempts(session, exam_ids),
        **extra,
    )


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def course_detail(request: Request, session: SessionDep, course_id: int, saved: bool = False):
    return _course_page(request, session, get_or_404(session, Course, course_id), saved=saved)


@router.post("/courses/{course_id}")
def update_course(
    request: Request,
    session: SessionDep,
    course_id: int,
    name: Annotated[str, Form()] = "",
    default_time_limit: Annotated[str, Form()] = "",
    default_pass_mark: Annotated[str, Form()] = "",
):
    course = get_or_404(session, Course, course_id)
    try:
        values = {
            "name": _valid_name(session, name, course.id),
            "default_time_limit": _optional_number(default_time_limit, int),
            "default_pass_mark": _optional_number(default_pass_mark, float),
        }
    except FormError as exc:
        error = _error_text(request, exc)
        return _course_page(request, session, course, status_code=400, error=error)
    course.sqlmodel_update(values)
    session.commit()
    url = request.app.url_path_for("course_detail", course_id=course.id)
    return RedirectResponse(f"{url}?saved=1", status_code=303)


@router.post("/courses/{course_id}/delete")
def delete_course(request: Request, session: SessionDep, course_id: int):
    session.delete(get_or_404(session, Course, course_id))
    session.commit()
    return redirect(request, "course_list")

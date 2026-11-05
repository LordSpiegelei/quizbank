"""Exam builder: generate a draft exam with the LLM, review it, then import it."""

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session, col, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.builder import (
    MAX_QUESTIONS,
    MaterialError,
    accepted_extensions,
    check_context,
    read_material,
)
from app.db import SessionDep, get_or_404
from app.forms import FormError, optional_number
from app.importer import import_exam
from app.jobs import JobRunner, drafts_dir, remove_draft_files
from app.llm import LLMClient, LLMError, get_llm
from app.models import Course, Exam, GenerationJob
from app.schemas import (
    MAX_OPTIONS,
    MIN_OPTIONS,
    ExamFileError,
    QuestionIn,
    format_validation_error,
    parse_exam,
)
from app.templating import redirect, render

router = APIRouter()
LlmDep = Annotated[LLMClient, Depends(get_llm)]
_question_adapter = TypeAdapter(QuestionIn)

DEFAULT_FORM = {"single": "10", "multiple": "0", "open": "0", "options": "4"}


def get_jobs(request: Request) -> JobRunner:
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=404)
    return jobs


JobsDep = Annotated[JobRunner, Depends(get_jobs)]


def _models(llm: LLMClient) -> tuple[list[str], str | None]:
    try:
        return llm.list_models(), None
    except LLMError as exc:
        return [], str(exc)


def _exists(session: Session, course: str, title: str) -> bool:
    return (
        session.exec(
            select(Exam).join(Course).where(Course.name == course, Exam.title == title)
        ).first()
        is not None
    )


def _builder_page(
    request: Request,
    session: Session,
    llm: LLMClient,
    status_code: int = 200,
    form: dict[str, str] | None = None,
    **extra: Any,
):
    models, llm_error = _models(llm)
    values = DEFAULT_FORM | {"language": request.app.state.settings.app_lang} | (form or {})
    model = values.get("model") or llm.preferred_model(models) or ""
    default_missing = bool(models) and bool(llm.default_model) and llm.default_model not in models
    info = llm.model_info(model) if model and not llm_error else None
    return render(
        request,
        "builder.html",
        status_code=status_code,
        models=models,
        llm_error=llm_error,
        form=values | {"model": model},
        accept=",".join(accepted_extensions(info)) if info else "",
        info=info,
        default_missing=llm.default_model if default_missing else None,
        course_names=session.exec(select(Course.name).order_by(col(Course.name))).all(),
        drafts=session.exec(select(GenerationJob).order_by(col(GenerationJob.id).desc())).all(),
        **extra,
    )


@router.get("/builder", response_class=HTMLResponse)
def builder_form(request: Request, session: SessionDep, llm: LlmDep):
    return _builder_page(request, session, llm)


@router.get("/api/builder/models/{model:path}", name="builder_model_info")
def model_info(llm: LlmDep, model: str):
    try:
        info = llm.model_info(model)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {
        "vision": info.vision,
        "context_tokens": info.context_tokens,
        "accept": list(accepted_extensions(info)),
    }


def _number(raw: str, kind: type, default: int | float | None = None) -> Any:
    value = optional_number(raw, kind)
    return default if value is None else value


def _parse_params(session: Session, form: dict[str, str]) -> dict[str, Any]:
    """The generation parameters from the builder form; raises FormError."""
    course = form["course_new"].strip() or form["course_choice"].strip()
    if not course:
        raise FormError("builder.err_course")
    title = form["title"].strip()
    if not title:
        raise FormError("builder.err_title")
    single, multiple, open_ = (_number(form[k], int, 0) for k in ("single", "multiple", "open"))
    if not 1 <= single + multiple + open_ <= MAX_QUESTIONS:
        raise FormError("builder.err_count", max=MAX_QUESTIONS)
    options = _number(form["options"], int, 4)
    if not MIN_OPTIONS <= options <= MAX_OPTIONS:
        raise FormError("builder.err_options", min=MIN_OPTIONS, max=MAX_OPTIONS)
    if _exists(session, course, title):
        raise FormError("builder.err_exists", title=title)
    scoring = form["multiple_scoring"]
    return {
        "course": course,
        "title": title,
        "single": single,
        "multiple": multiple,
        "open": open_,
        "options": options,
        "topics": [line.strip() for line in form["topics"].splitlines() if line.strip()],
        "language": form["language"].strip() or "en",
        "time_limit_minutes": _number(form["time_limit"], int) or None,
        "pass_mark": _number(form["pass_mark"], float),
        "multiple_scoring": scoring
        if scoring in ("all_or_nothing", "partial")
        else "all_or_nothing",
        "instructions": form["instructions"].strip(),
    }


FORM_FIELDS = [
    "course_choice",
    "course_new",
    "title",
    "single",
    "multiple",
    "open",
    "options",
    "topics",
    "language",
    "time_limit",
    "pass_mark",
    "multiple_scoring",
    "instructions",
    "material",
    "model",
]


def _create_job(
    request: Request,
    session: Session,
    llm: LLMClient,
    jobs: JobRunner,
    form: dict[str, str],
    uploads: list[tuple[str, bytes]],
):
    """Validate the form and material, store the job and queue it (blocking: runs in a thread)."""
    t = request.app.state.t
    model = form["model"] or llm.default_model or ""
    try:
        if not model:
            raise FormError("ai.no_model")
        params = _parse_params(session, form)
        info = llm.model_info(model)
        source = read_material(form["material"], uploads, info, model)
        check_context(source, _total(params), info)
    except (FormError, MaterialError) as exc:
        error = t(exc.key, **exc.params)
        return _builder_page(request, session, llm, 422, form=form, error=error)
    except LLMError as exc:
        return _builder_page(request, session, llm, 422, form=form, error=str(exc))

    job = GenerationJob(params=params, model=model, material_text=source.text, total=_total(params))
    session.add(job)
    session.commit()
    if source.images:
        directory = drafts_dir(request.app.state.settings.db_path) / str(job.id)
        directory.mkdir(parents=True, exist_ok=True)
        names = []
        for index, (name, content) in enumerate(source.images):
            stored = f"img{index}{Path(name).suffix.lower()}"
            (directory / stored).write_bytes(content)
            names.append(stored)
        job.images = names
        session.commit()
    jobs.submit(job.id)
    return redirect(request, "draft_page", job_id=job.id)


def _total(params: dict[str, Any]) -> int:
    return params["single"] + params["multiple"] + params["open"]


@router.post("/builder")
async def builder_create(request: Request, session: SessionDep, llm: LlmDep, jobs: JobsDep):
    # Read the form directly: an empty file input arrives as "" or a nameless part.
    data = await request.form()
    form = {name: str(data.get(name) or "") for name in FORM_FIELDS}
    form["multiple_scoring"] = form["multiple_scoring"] or "all_or_nothing"
    uploads = [
        (upload.filename, await upload.read())
        for upload in data.getlist("files")
        if isinstance(upload, UploadFile) and upload.filename
    ]
    return await run_in_threadpool(_create_job, request, session, llm, jobs, form, uploads)


# -- drafts -------------------------------------------------------------------------------------


def _draft_page(
    request: Request, session: Session, job: GenerationJob, status_code: int = 200, **extra: Any
):
    questions = (job.draft or {}).get("questions", [])
    return render(
        request,
        "draft.html",
        status_code=status_code,
        job=job,
        questions=questions,
        question_json={q["id"]: json.dumps(q, indent=2, ensure_ascii=False) for q in questions},
        **extra,
    )


@router.get("/builder/drafts/{job_id}", response_class=HTMLResponse)
def draft_page(request: Request, session: SessionDep, llm: LlmDep, job_id: int):
    return _draft_page(request, session, get_or_404(session, GenerationJob, job_id))


@router.get("/api/builder/drafts/{job_id}", name="draft_status")
def draft_status(session: SessionDep, llm: LlmDep, job_id: int):
    job = get_or_404(session, GenerationJob, job_id)
    return {
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "regenerating": job.regenerating,
        "version": job.updated_at.isoformat(),
    }


def _idle_job(session: Session, job_id: int) -> GenerationJob:
    job = get_or_404(session, GenerationJob, job_id)
    if job.busy:
        raise HTTPException(status_code=409, detail="the draft is being generated")
    return job


def _back(request: Request, job_id: int):
    return redirect(request, "draft_page", job_id=job_id)


@router.post("/builder/drafts/{job_id}/cancel")
def draft_cancel(request: Request, session: SessionDep, llm: LlmDep, job_id: int):
    job = get_or_404(session, GenerationJob, job_id)
    if job.busy:
        job.cancel_requested = True
        session.add(job)
        session.commit()
    return _back(request, job_id)


@router.post("/builder/drafts/{job_id}/retry")
def draft_retry(request: Request, session: SessionDep, llm: LlmDep, jobs: JobsDep, job_id: int):
    job = _idle_job(session, job_id)
    if not job.regenerating:
        jobs.submit(job.id)
    return _back(request, job_id)


@router.post("/builder/drafts/{job_id}/discard")
def draft_discard(request: Request, session: SessionDep, llm: LlmDep, job_id: int):
    job = get_or_404(session, GenerationJob, job_id)
    session.delete(job)
    session.commit()
    remove_draft_files(drafts_dir(request.app.state.settings.db_path), job_id)
    return redirect(request, "builder_form")


def _replace_questions(session: Session, job: GenerationJob, questions: list[dict]) -> None:
    job.draft = {**(job.draft or {}), "questions": questions}
    job.progress = len(questions)
    session.add(job)
    session.commit()


@router.post("/builder/drafts/{job_id}/questions/{question_id}/drop")
def question_drop(
    request: Request, session: SessionDep, llm: LlmDep, job_id: int, question_id: str
):
    job = _idle_job(session, job_id)
    questions = [q for q in (job.draft or {}).get("questions", []) if q["id"] != question_id]
    _replace_questions(session, job, questions)
    return _back(request, job_id)


@router.post("/builder/drafts/{job_id}/questions/{question_id}/regenerate")
def question_regenerate(
    request: Request, session: SessionDep, llm: LlmDep, jobs: JobsDep, job_id: int, question_id: str
):
    job = _idle_job(session, job_id)
    if not any(q["id"] == question_id for q in (job.draft or {}).get("questions", [])):
        raise HTTPException(status_code=404)
    jobs.submit_regenerate(job.id, question_id)
    return _back(request, job_id)


@router.post("/builder/drafts/{job_id}/questions/{question_id}/edit")
def question_edit(
    request: Request,
    session: SessionDep,
    llm: LlmDep,
    job_id: int,
    question_id: str,
    text: Annotated[str, Form(alias="json")] = "",
):
    job = _idle_job(session, job_id)
    questions = list((job.draft or {}).get("questions", []))
    index = next((i for i, q in enumerate(questions) if q["id"] == question_id), None)
    if index is None:
        raise HTTPException(status_code=404)
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("a question must be a JSON object")
        question = _question_adapter.validate_python(data | {"id": question_id})
    except ValidationError as exc:
        errors = [f"questions[0]{line}" for line in format_validation_error(exc)]
    except ValueError as exc:  # includes JSON syntax errors
        errors = [str(exc)]
    else:
        questions[index] = question.model_dump(mode="json", exclude_none=True)
        _replace_questions(session, job, questions)
        return redirect(request, "draft_page", fragment=f"q-{question_id}", job_id=job_id)
    return _draft_page(
        request,
        session,
        job,
        422,
        edit_errors={question_id: errors},
        edit_texts={question_id: text},
    )


@router.post("/builder/drafts/{job_id}/import")
def draft_import(request: Request, session: SessionDep, llm: LlmDep, job_id: int):
    job = _idle_job(session, job_id)
    try:
        exam_file = parse_exam(json.dumps(job.draft or {}))
    except ExamFileError as exc:
        return _draft_page(request, session, job, 422, import_errors=exc.errors)
    if _exists(session, exam_file.course, exam_file.title):
        error = request.app.state.t("builder.err_exists", title=exam_file.title)
        return _draft_page(request, session, job, 422, error=error)
    result = import_exam(session, exam_file, source_file=None)
    session.delete(job)
    session.commit()
    remove_draft_files(drafts_dir(request.app.state.settings.db_path), job_id)
    return redirect(request, "exam_detail", exam_id=result.exam_id)

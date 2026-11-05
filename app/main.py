from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.db import make_engine, run_migrations
from app.importer import import_directory
from app.jobs import JobRunner, drafts_dir
from app.llm import LLMClient
from app.locales import get_translator
from app.routes import attempts, builder, courses, exams, questions
from app.templating import make_templates

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _prepare_database(settings: Settings) -> Engine:
    """Blocking startup work: migrate, then auto-import QUESTIONS_DIR."""
    engine = make_engine(settings.db_path)
    run_migrations(engine)
    if settings.auto_import and settings.questions_dir.is_dir():
        with Session(engine) as session:
            import_directory(session, settings.questions_dir)
    return engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = await run_in_threadpool(_prepare_database, settings)
        jobs = None
        if settings.llm_enabled:
            jobs = JobRunner(app.state.engine, lambda: app.state.llm, drafts_dir(settings.db_path))
            await run_in_threadpool(jobs.start)
        app.state.jobs = jobs
        yield
        if jobs is not None:
            await run_in_threadpool(jobs.stop)
        app.state.engine.dispose()

    app = FastAPI(title="quizbank", lifespan=lifespan)
    app.state.settings = settings
    app.state.t = get_translator(settings.app_lang)
    app.state.llm = LLMClient(settings) if settings.llm_enabled else None
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.state.templates = make_templates(
        app.state.t, app.url_path_for, settings.llm_enabled
    )  # after the static mount
    app.include_router(courses.router)
    app.include_router(exams.router)
    app.include_router(attempts.router)
    app.include_router(questions.router)
    if settings.llm_enabled:
        app.include_router(builder.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

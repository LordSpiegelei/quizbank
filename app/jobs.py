"""Background runner for exam-builder jobs: one worker thread, one job at a time.

Jobs live in the database (`GenerationJob`), the queue only holds ids, so a restart loses
nothing but the run itself: whatever was queued or running is marked failed and can be retried
(batches already generated are kept)."""

import logging
import queue
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.builder import (
    BATCH_SIZE,
    GenerationError,
    Material,
    generate_batch,
    load_images,
    outline,
    plan_slots,
    plan_types,
)
from app.llm import LLMClient, LLMError
from app.models import GenerationJob, utcnow

logger = logging.getLogger(__name__)

INTERRUPTED = "interrupted by a restart"


def drafts_dir(db_path: Path) -> Path:
    """Uploaded images of drafts, on the data volume next to the database."""
    return db_path.parent / "drafts"


def question_ids(job_id: int, start: int, count: int) -> list[str]:
    return [f"ai-{job_id}-{n:03}" for n in range(start + 1, start + count + 1)]


def new_draft(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": 1,
        "course": params["course"],
        "title": params["title"],
        "time_limit_minutes": params.get("time_limit_minutes"),
        "pass_mark": params.get("pass_mark"),
        "multiple_scoring": params.get("multiple_scoring", "all_or_nothing"),
        "questions": [],
    }


def remove_draft_files(directory: Path, job_id: int) -> None:
    shutil.rmtree(directory / str(job_id), ignore_errors=True)


class JobLog:
    """Events and live model output of a job, for the draft page. Written straight to the row
    without touching `updated_at`, which the page watches to know when to reload."""

    MAX_EVENTS = 300
    MAX_LIVE_CHARS = 8000
    FLUSH_SECONDS = 0.5

    def __init__(self, engine: Engine, job_id: int) -> None:
        self.engine = engine
        self.job_id = job_id
        self.live: list[dict[str, str]] = []
        self._flushed = 0.0

    def event(self, text: str, kind: str = "info") -> None:
        with Session(self.engine) as session:
            job = session.get(GenerationJob, self.job_id)
            if job is None:
                return
            n = job.log[-1]["n"] + 1 if job.log else 1
            entry = {"n": n, "at": utcnow().isoformat(), "kind": kind, "text": text}
            job.log = [*job.log, entry][-self.MAX_EVENTS :]
            session.add(job)
            session.commit()

    def on_text(self, kind: str, chunk: str) -> None:
        """`OnText` callback: "meta" becomes an event (and starts a new live text), the rest
        is appended to the live text and flushed every FLUSH_SECONDS."""
        if kind == "meta":
            self.event(chunk, "meta")
            self.live = []
            self.flush()
            return
        if self.live and self.live[-1]["kind"] == kind:
            self.live[-1]["text"] += chunk
        else:
            self.live.append({"kind": kind, "text": chunk})
        excess = sum(len(seg["text"]) for seg in self.live) - self.MAX_LIVE_CHARS
        while excess > 0 and self.live:
            first = self.live[0]
            if len(first["text"]) <= excess:
                excess -= len(first["text"])
                self.live.pop(0)
            else:
                first["text"] = first["text"][excess:]
                excess = 0
        if time.monotonic() - self._flushed >= self.FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        self._flushed = time.monotonic()
        with Session(self.engine) as session:
            job = session.get(GenerationJob, self.job_id)
            if job is not None:
                job.live = [dict(seg) for seg in self.live]
                session.add(job)
                session.commit()


def _type_summary(slots: list[dict[str, Any]]) -> str:
    counts = Counter(slot["type"] for slot in slots)
    return ", ".join(
        f"{counts[kind]} {kind}" for kind in ("single", "multiple", "open") if counts[kind]
    )


class JobRunner:
    """`threaded=False` runs submitted jobs inline (tests); otherwise a daemon thread does."""

    def __init__(
        self,
        engine: Engine,
        get_llm: Callable[[], LLMClient | None],
        directory: Path,
        threaded: bool = True,
    ) -> None:
        self.engine = engine
        self.get_llm = get_llm
        self.directory = directory
        self.threaded = threaded
        self._queue: queue.Queue[tuple[int, str | None] | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        """Fail what a restart interrupted, then start the worker."""
        with Session(self.engine) as session:
            for job in session.exec(select(GenerationJob)):
                if job.busy:
                    job.status, job.errors = "failed", [INTERRUPTED]
                if job.regenerating:
                    job.regenerating = []
                session.add(job)
            session.commit()
        if self.threaded:
            self._thread = threading.Thread(target=self._work, name="quizbank-jobs", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=5)

    # -- submitting --------------------------------------------------------------------------

    def submit(self, job_id: int) -> None:
        """Queue (or resume) generation of a whole draft."""
        self._set(job_id, status="queued", cancel_requested=False, errors=[])
        self._dispatch((job_id, None))

    def submit_regenerate(self, job_id: int, question_id: str) -> None:
        with Session(self.engine) as session:
            job = session.get(GenerationJob, job_id)
            if job is not None and question_id not in job.regenerating:
                job.regenerating = [*job.regenerating, question_id]
                job.errors = []
                job.updated_at = utcnow()
                session.add(job)
                session.commit()
        self._dispatch((job_id, question_id))

    def _dispatch(self, item: tuple[int, str | None]) -> None:
        if self.threaded:
            self._queue.put(item)
        else:
            self._run(*item)

    def _work(self) -> None:
        while (item := self._queue.get()) is not None:
            self._run(*item)

    def _run(self, job_id: int, question_id: str | None) -> None:
        try:
            if question_id is None:
                self.run_exam(job_id)
            else:
                self.run_regenerate(job_id, question_id)
        except Exception as exc:  # the worker must outlive any single job
            logger.exception("Builder job %s failed", job_id)
            self._fail(job_id, question_id, [f"unexpected error: {exc}"])

    # -- helpers -----------------------------------------------------------------------------

    def _set(self, job_id: int, **values: Any) -> None:
        with Session(self.engine) as session:
            job = session.get(GenerationJob, job_id)
            if job is not None:
                for key, value in values.items():
                    setattr(job, key, value)
                job.updated_at = utcnow()
                session.add(job)
                session.commit()

    def _fail(
        self,
        job_id: int,
        question_id: str | None,
        errors: list[str],
        log: JobLog | None = None,
    ) -> None:
        if log is not None:
            log.flush()
            for error in errors:
                log.event(error, "error")
        with Session(self.engine) as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if question_id is None:
                job.status = "failed"
            else:
                job.regenerating = [q for q in job.regenerating if q != question_id]
            job.errors = errors
            job.updated_at = utcnow()
            session.add(job)
            session.commit()

    def _llm(self) -> LLMClient:
        llm = self.get_llm()
        if llm is None:
            raise LLMError("no LLM configured")
        return llm

    def _material(self, job: GenerationJob) -> Material:
        images = load_images(self.directory / str(job.id), job.images)
        return Material(job.material_text, images)

    # -- the jobs ----------------------------------------------------------------------------

    def run_exam(self, job_id: int) -> None:
        """Generate the draft batch by batch from `params["plan"]`, the slots still to write.
        Slots a reply leaves unfilled move to the next batch; every batch fills at least one
        slot or fails the job, so this ends. Resumes where a failed run stopped."""
        with Session(self.engine) as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            params, model, material = job.params, job.model, self._material(job)
            draft = job.draft or new_draft(params)
            if "counts" not in params:  # a draft from before free mode: fixed counts only
                types = plan_types(params["single"], params["multiple"], params["open"])
                params = params | {
                    "counts": dict(
                        zip(
                            ("single", "multiple", "open"),
                            (params["single"], params["multiple"], params["open"]),
                            strict=True,
                        )
                    ),
                    "total": len(types),
                    "plan": plan_slots(types[len(draft["questions"]) :]),
                }
                job.params = params
            job.status, job.errors = "running", []
            job.progress = len(draft["questions"])
            job.updated_at = utcnow()
            session.add(job)
            session.commit()

        log = JobLog(self.engine, job_id)
        log.event(f"Started with {model}")
        llm = self._llm()
        if params.get("plan") is None:  # something was left to the AI: outline it first
            log.event("Planning the exam (outline)")
            try:
                plan = outline(llm, model, params, material, on_text=log.on_text)
            except GenerationError as exc:
                self._fail(job_id, None, [str(exc), *exc.errors[:3]], log)
                return
            except LLMError as exc:
                self._fail(job_id, None, [str(exc)], log)
                return
            log.flush()
            log.event(f"Outline: {len(plan)} questions ({_type_summary(plan)})")
            params = {**params, "plan": plan}
            self._set(job_id, params=params, total=len(plan))
        remaining = list(params["plan"])
        self._set(job_id, total=len(draft["questions"]) + len(remaining))
        while remaining:
            with Session(self.engine) as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    return
                if job.cancel_requested:
                    job.status, job.cancel_requested = "cancelled", False
                    job.updated_at = utcnow()
                    session.add(job)
                    session.commit()
                    log.event("Cancelled")
                    return
            done = len(draft["questions"])
            # one question type per batch, so the schema can require all of its fields
            kind = remaining[0]["type"]
            positions = [i for i, slot in enumerate(remaining) if slot["type"] == kind]
            positions = positions[:BATCH_SIZE]
            batch = [remaining[i] for i in positions]
            log.event(
                f"Writing {len(batch)} questions ({_type_summary(batch)}), {done} done so far"
            )
            try:
                filled, problems = generate_batch(
                    llm, model, params, material, batch, draft["questions"],
                    question_ids(job_id, done, len(batch)), on_text=log.on_text,
                )  # fmt: skip
            except GenerationError as exc:
                self._fail(job_id, None, [str(exc), *exc.errors[:3]], log)
                return
            except LLMError as exc:
                self._fail(job_id, None, [str(exc)], log)
                return
            log.flush()
            log.event(f"Got {len(filled)} of {len(batch)} usable")
            for problem in problems[:5]:
                log.event(problem, "warning")
            taken = {positions[index] for index, _ in filled}
            remaining = [slot for i, slot in enumerate(remaining) if i not in taken]
            draft = {**draft, "questions": [*draft["questions"], *(q for _, q in filled)]}
            params = {**params, "plan": remaining}
            self._set(job_id, draft=draft, params=params, progress=len(draft["questions"]))
        self._set(job_id, status="done")
        log.event(f"Done: {len(draft['questions'])} questions")

    def run_regenerate(self, job_id: int, question_id: str) -> None:
        """Replace one question by a new one of the same id, type and topic."""
        with Session(self.engine) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.draft is None:
                return
            params, model, material = job.params, job.model, self._material(job)
            existing = job.draft["questions"]
        old = next((q for q in existing if q["id"] == question_id), None)
        if old is None:
            self._fail(job_id, question_id, [])
            return
        others = [q for q in existing if q["id"] != question_id]
        log = JobLog(self.engine, job_id)
        log.event(f"Regenerating {question_id} ({old['type']}, topic {old['topic']})")
        try:
            [(_, new)], _problems = generate_batch(
                self._llm(), model, params, material, plan_slots([old["type"]], old["topic"]),
                others, [question_id], on_text=log.on_text,
            )  # fmt: skip
        except GenerationError as exc:
            self._fail(job_id, question_id, [str(exc), *exc.errors[:3]], log)
            return
        except LLMError as exc:
            self._fail(job_id, question_id, [str(exc)], log)
            return
        log.flush()
        log.event(f"Replaced {question_id}")
        with Session(self.engine) as session:  # re-read: the user may have edited meanwhile
            job = session.get(GenerationJob, job_id)
            if job is None or job.draft is None:
                return
            questions = [new if q["id"] == question_id else q for q in job.draft["questions"]]
            job.draft = {**job.draft, "questions": questions}
            job.regenerating = [q for q in job.regenerating if q != question_id]
            job.updated_at = utcnow()
            session.add(job)
            session.commit()

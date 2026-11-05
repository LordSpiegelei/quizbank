import json
from pathlib import Path

import pytest
from sqlmodel import select

from app.builder import normalize_batch, plan_types
from app.jobs import INTERRUPTED, JobRunner, drafts_dir
from app.llm import LLMError, LLMReplyError
from app.models import Exam, GenerationJob
from tests.factories import FakeLLM, db, exam, single

FORM = {"course_new": "Kurs", "title": "Entwurf", "single": "3", "multiple": "1", "open": "1"}


def question(kind: str, n: int = 1) -> dict:
    base = {"type": kind, "topic": "Thema", "question": f"Frage {kind} {n}?", "explanation": "weil"}
    if kind == "open":
        return base | {
            "scenario": "Lage",
            "points": 5,
            "model_answer": "Antwort",
            "rubric": [{"criterion": "Kriterium", "points": 5}],
        }
    correct = [1] if kind == "single" else [0, 2]
    return base | {"options": ["a", "b", "c", "d"], "correct": correct}


def reply(*kinds: str, start: int = 1) -> dict:
    return {"questions": [question(k, start + i) for i, k in enumerate(kinds)]}


FIVE = ("single", "single", "single", "multiple", "open")


def create(client, files=None, **overrides):
    return client.post(
        "/builder", data=FORM | overrides, files=files or None, follow_redirects=False
    )


def job_of(client, job_id: int = 1) -> GenerationJob:
    with db(client) as session:
        return session.get(GenerationJob, job_id)


def test_builder_hidden_without_llm(client) -> None:
    assert client.get("/builder").status_code == 404
    assert "/builder" not in client.get("/").text


def test_builder_page_and_nav(llm_client) -> None:
    page = llm_client.get("/builder").text
    assert 'name="model"' in page and '<option value="other"' in page
    assert "/builder" in llm_client.get("/").text


def test_builder_page_when_llm_down(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM(models=LLMError("down"))
    page = llm_client.get("/builder")
    assert page.status_code == 200
    assert "disabled" in page.text and "down" in page.text


def test_generates_draft_and_imports_it(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply(*FIVE))
    response = create(llm_client, time_limit="30", pass_mark="3,5", multiple_scoring="partial")
    assert response.status_code == 303 and response.headers["location"] == "/builder/drafts/1"

    job = job_of(llm_client)
    assert job.status == "done" and job.progress == job.total == 5
    ids = [q["id"] for q in job.draft["questions"]]
    assert ids == [f"ai-1-{n:03}" for n in range(1, 6)]
    assert job.draft["time_limit_minutes"] == 30 and job.draft["pass_mark"] == 3.5
    assert "open" not in job.draft["questions"][0]  # foreign/empty fields dropped
    assert llm.calls[0]["model"] == "fake" and llm.calls[0]["schema"]["required"] == ["questions"]

    page = llm_client.get("/builder/drafts/1").text
    assert "Frage single 1?" in page and 'id="q-ai-1-001"' in page

    result = llm_client.post("/builder/drafts/1/import", follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"].startswith("/exams/")
    with db(llm_client) as session:
        imported = session.exec(select(Exam)).one()
        assert (imported.title, imported.source_file, len(imported.questions)) == (
            "Entwurf",
            None,
            5,
        )
        assert imported.multiple_scoring == "partial"
        assert session.get(GenerationJob, 1) is None  # draft is gone


def test_generates_in_batches_and_avoids_repeats(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(
        reply(*["single"] * 5), reply("single", "single", start=6)
    )
    create(llm_client, single="7", multiple="0", open="0")
    job = job_of(llm_client)
    assert job.status == "done" and len(job.draft["questions"]) == 7
    assert [q["id"] for q in job.draft["questions"]][-1] == "ai-1-007"
    assert len(llm.calls) == 2
    second = llm.calls[1]["messages"][0]["content"]
    assert "Frage single 1?" in second and "Do not repeat" in second


def test_repair_retry_feeds_back_the_problem(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply("single"), reply(*FIVE))
    create(llm_client)
    assert job_of(llm_client).status == "done" and len(llm.calls) == 2
    last = llm.calls[1]["messages"][-1]["content"]
    assert "unusable" in last and "expected question types" in last


def test_failed_batch_keeps_progress_and_can_resume(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(
        reply(*["single"] * 5), reply("single"), LLMReplyError("not json")
    )
    create(llm_client, single="7", multiple="0", open="0")
    job = job_of(llm_client)
    assert job.status == "failed" and len(job.draft["questions"]) == 5
    assert "no usable questions" in job.errors[0]
    page = llm_client.get("/builder/drafts/1").text
    assert "Fortsetzen" in page  # Resume

    llm.replies.append(reply("single", "single", start=6))
    assert llm_client.post("/builder/drafts/1/retry", follow_redirects=False).status_code == 303
    job = job_of(llm_client)
    assert job.status == "done" and len(job.draft["questions"]) == 7


def test_unreachable_llm_fails_job_without_retry(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(LLMError("cannot reach the LLM"))
    create(llm_client)
    job = job_of(llm_client)
    assert job.status == "failed" and job.errors == ["cannot reach the LLM"]
    assert len(llm.calls) == 1


def test_cancel_between_batches(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM()
    create(llm_client)  # reply queue is empty: fails at once, then we cancel a fresh run
    with db(llm_client) as session:
        job = session.get(GenerationJob, 1)
        job.status, job.cancel_requested, job.errors = "running", True, []
        session.add(job)
        session.commit()
    llm_client.app.state.jobs.run_exam(1)
    assert job_of(llm_client).status == "cancelled" and len(llm.calls) == 1


def test_validation_of_form(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM(reply(*FIVE))
    assert create(llm_client, title="").status_code == 422
    assert create(llm_client, course_new="").status_code == 422
    assert create(llm_client, single="0", multiple="0", open="0").status_code == 422
    assert create(llm_client, options="1").status_code == 422
    assert create(llm_client, single="abc").status_code == 422
    assert llm_client.app.state.llm.calls == []


def test_existing_exam_title_is_refused(llm_client) -> None:
    from tests.factories import upload

    upload(llm_client, ("a.json", exam(single(), course="Kurs", title="Entwurf")))
    response = create(llm_client)
    assert response.status_code == 422 and "bereits eine Klausur" in response.text


def test_text_files_are_part_of_the_prompt(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply(*FIVE))
    create(
        llm_client,
        files=[("files", ("notes.md", "Äpfel sind Obst".encode(), "text/markdown"))],
        material="Eingefügter Text",
    )
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "Äpfel sind Obst" in prompt and "### notes.md" in prompt and "Eingefügter Text" in prompt


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("notes.pdf", b"%PDF-1.4", "nur Textdateien"),
        ("notes.docx", b"PK", "nur Textdateien"),
        ("notes.txt", b"\xff\xfe\x00bad", "kein UTF-8"),
        ("pic.png", b"\x89PNG", "keine Bilder"),  # the fake model has no vision
    ],
)
def test_unusable_files_are_refused(llm_client, name, content, message) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply(*FIVE))
    response = create(llm_client, files=[("files", (name, content, "application/octet-stream"))])
    assert response.status_code == 422 and message in response.text
    assert llm.calls == []
    assert job_of(llm_client) is None


def test_images_need_vision_and_are_sent_then_cleaned_up(llm_client, settings) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply(*FIVE), vision=True)
    create(llm_client, files=[("files", ("pic.png", b"\x89PNGdata", "image/png"))])
    parts = llm.calls[0]["messages"][0]["content"]
    assert parts[0]["type"] == "text" and parts[1]["image_url"]["url"].startswith("data:image/png")
    folder = drafts_dir(settings.db_path) / "1"
    assert (folder / "img0.png").is_file()

    llm_client.post("/builder/drafts/1/discard", follow_redirects=False)
    assert not folder.exists() and job_of(llm_client) is None


def test_material_over_context_is_refused(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply(*FIVE), context_tokens=2000)
    response = create(llm_client, material="x" * 20000)
    assert response.status_code == 422 and "passt nicht" in response.text
    assert llm.calls == []


def _draft(llm_client, *kinds: str) -> FakeLLM:
    llm = llm_client.app.state.llm = FakeLLM(reply(*kinds))
    n = {k: kinds.count(k) for k in ("single", "multiple", "open")}
    create(llm_client, **{k: str(v) for k, v in n.items()})
    return llm


def test_regenerate_keeps_id_type_topic_and_position(llm_client) -> None:
    llm = _draft(llm_client, "single", "multiple", "open")
    new = question("multiple", 9) | {"topic": "Egal"}
    llm.replies.append({"questions": [new]})
    llm_client.post("/builder/drafts/1/questions/ai-1-002/regenerate", follow_redirects=False)
    job = job_of(llm_client)
    questions = job.draft["questions"]
    assert [q["id"] for q in questions] == ["ai-1-001", "ai-1-002", "ai-1-003"]
    assert questions[1]["question"] == "Frage multiple 9?" and questions[1]["topic"] == "Egal"
    assert job.regenerating == []
    prompt = llm.calls[-1]["messages"][0]["content"]
    assert 'topic "Thema"' in prompt and "Frage single 1?" in prompt
    assert "Frage multiple 2?" not in prompt  # the replaced question is not in the avoid list


def test_regenerate_failure_is_reported_and_keeps_question(llm_client) -> None:
    llm = _draft(llm_client, "single")
    llm.replies.append(LLMError("cannot reach the LLM"))
    llm_client.post("/builder/drafts/1/questions/ai-1-001/regenerate", follow_redirects=False)
    job = job_of(llm_client)
    assert job.draft["questions"][0]["question"] == "Frage single 1?"
    assert job.regenerating == [] and job.errors == ["cannot reach the LLM"]


def test_drop_question(llm_client) -> None:
    _draft(llm_client, "single", "multiple")
    llm_client.post("/builder/drafts/1/questions/ai-1-001/drop", follow_redirects=False)
    assert [q["id"] for q in job_of(llm_client).draft["questions"]] == ["ai-1-002"]


def test_edit_question_json(llm_client) -> None:
    _draft(llm_client, "single")
    edited = question("single", 1) | {"question": "Neu formuliert?", "id": "ignored"}
    ok = llm_client.post(
        "/builder/drafts/1/questions/ai-1-001/edit",
        data={"json": json.dumps(edited)},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    saved = job_of(llm_client).draft["questions"][0]
    assert saved["question"] == "Neu formuliert?" and saved["id"] == "ai-1-001"

    bad = llm_client.post(
        "/builder/drafts/1/questions/ai-1-001/edit",
        data={"json": json.dumps(edited | {"correct": [9]})},
    )
    assert bad.status_code == 422 and "out of range" in bad.text
    syntax = llm_client.post("/builder/drafts/1/questions/ai-1-001/edit", data={"json": "{nope"})
    assert syntax.status_code == 422
    assert job_of(llm_client).draft["questions"][0]["question"] == "Neu formuliert?"


def test_import_refused_when_exam_appeared_meanwhile(llm_client) -> None:
    from tests.factories import upload

    _draft(llm_client, "single")
    upload(llm_client, ("a.json", exam(single(), course="Kurs", title="Entwurf")))
    response = llm_client.post("/builder/drafts/1/import", follow_redirects=False)
    assert response.status_code == 422 and job_of(llm_client) is not None


def test_import_with_no_questions_shows_errors(llm_client) -> None:
    _draft(llm_client, "single")
    llm_client.post("/builder/drafts/1/questions/ai-1-001/drop", follow_redirects=False)
    response = llm_client.post("/builder/drafts/1/import", follow_redirects=False)
    assert response.status_code == 422 and job_of(llm_client) is not None


def test_busy_draft_cannot_be_changed(llm_client) -> None:
    _draft(llm_client, "single")
    with db(llm_client) as session:
        job = session.get(GenerationJob, 1)
        job.status = "running"
        session.add(job)
        session.commit()
    assert llm_client.post("/builder/drafts/1/questions/ai-1-001/drop").status_code == 409
    assert llm_client.get("/api/builder/drafts/1").json()["status"] == "running"


def test_restart_fails_interrupted_jobs(llm_client) -> None:
    with db(llm_client) as session:
        session.add(GenerationJob(status="running", regenerating=["x"]))
        session.add(GenerationJob(status="done"))
        session.commit()
    runner = JobRunner(llm_client.app.state.engine, lambda: None, drafts_dir(Path("x/y.db")), False)
    runner.start()
    first, second = job_of(llm_client, 1), job_of(llm_client, 2)
    assert (first.status, first.errors, first.regenerating) == ("failed", [INTERRUPTED], [])
    assert second.status == "done"


def test_model_info_endpoint(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM(vision=True, context_tokens=4096)
    data = llm_client.get("/api/builder/models/some/model:7b").json()
    assert data["vision"] and data["context_tokens"] == 4096 and ".png" in data["accept"]
    llm_client.app.state.llm = FakeLLM(vision=False)
    assert ".png" not in llm_client.get("/api/builder/models/m").json()["accept"]


def test_normalize_batch_rejects_wrong_option_count() -> None:
    with pytest.raises(ValueError, match="expected 3 options"):
        normalize_batch(reply("single"), ["single"], 3, ["x-1"])


def test_plan_types_orders_by_type() -> None:
    assert plan_types(2, 1, 1) == ["single", "single", "multiple", "open"]

import json
from pathlib import Path

import pytest
from sqlmodel import select

from app.builder import accept_batch, plan_slots, plan_types, question_schema
from app.jobs import INTERRUPTED, JobRunner, drafts_dir
from app.llm import LLMError, LLMReplyError
from app.models import Exam, GenerationJob
from tests.factories import FakeLLM, db, exam, single

FORM = {"course_new": "Kurs", "title": "Entwurf", "single": "3", "multiple": "1", "open": "1"}


WORDS = [
    "Kernel",
    "Scheduler",
    "Paging",
    "Semaphore",
    "Deadlock",
    "Interrupt",
    "Dateisystem",
    "Pipe",
    "Signal",
    "Cache",
    "Monitor",
    "Spooler",
    "Treiber",
    "Shell",
    "Mutex",
    "Stack",
    "Heap",
    "Segment",
    "Quantum",
    "Prozess",
]


def question(kind: str, n: int = 1) -> dict:
    # distinct texts per n, or the duplicate check would drop them
    text = f"Frage {kind} {n}? " + " ".join(WORDS[(n * 7 + i) % len(WORDS)] for i in range(4))
    base = {"type": kind, "topic": "Thema", "question": text, "explanation": "weil"}
    if kind == "open":
        return base | {
            "scenario": f"Lage {WORDS[n % len(WORDS)]}",
            "points": 5,
            "model_answer": "Antwort",
            "rubric": [{"criterion": "Kriterium", "points": 5}],
        }
    correct = [1] if kind == "single" else [0, 2]
    options = [f"{WORDS[(n + i) % len(WORDS)]} {i}" for i in range(4)]
    return base | {"options": options, "correct": correct}


def reply(*kinds: str, start: int = 1) -> dict:
    return {"questions": [question(k, start + i) for i, k in enumerate(kinds)]}


FIVE = ("single", "single", "single", "multiple", "open")


def per_type(*kinds: str) -> list[dict]:
    """Replies in the order the runner asks: one type per batch, single → multiple → open."""
    replies, n = [], 1
    for kind in ("single", "multiple", "open"):
        mine = [k for k in kinds if k == kind]
        for i in range(0, len(mine), 5):
            chunk = mine[i : i + 5]
            replies.append(reply(*chunk, start=n))
            n += len(chunk)
    return replies


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
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*FIVE))
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
    second = llm.calls[1]["prompt"]
    assert "Frage single 1?" in second and "Do not repeat" in second


def test_partial_reply_is_kept_and_rest_follows(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(
        reply("single"),
        reply("single", "single", start=2),
        reply("multiple", start=4),
        reply("open", start=5),
    )
    create(llm_client)
    job = job_of(llm_client)
    assert job.status == "done" and len(job.draft["questions"]) == 5 and len(llm.calls) == 4
    assert [q["id"] for q in job.draft["questions"]] == [f"ai-1-{n:03}" for n in range(1, 6)]
    assert [len(c["schema"]["properties"]["questions"]["items"]["required"]) > 5 for c in llm.calls]


def test_oversized_reply_is_trimmed(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply(*["open"] * 8))
    create(llm_client, single="0", multiple="0", open="2")
    job = job_of(llm_client)
    assert job.status == "done" and len(job.draft["questions"]) == 2 and len(llm.calls) == 1


def test_repair_retry_only_when_nothing_is_usable(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(
        reply("open"), reply("single", "single", "single"), reply("multiple", start=4)
    )
    create(llm_client, single="3", multiple="1", open="0")
    assert job_of(llm_client).status == "done" and len(llm.calls) == 3
    last = llm.calls[1]["messages"][-1]["content"]
    assert "unusable" in last and "missing" in last


def test_failed_batch_keeps_progress_and_can_resume(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(
        reply(*["single"] * 5), LLMReplyError("not json"), LLMReplyError("not json")
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
    llm_client.app.state.llm = FakeLLM(*per_type(*FIVE))
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
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*FIVE))
    create(
        llm_client,
        files=[("files", ("notes.md", "Äpfel sind Obst".encode(), "text/markdown"))],
        material="Eingefügter Text",
    )
    prompt = llm.calls[0]["prompt"]
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
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*FIVE))
    response = create(llm_client, files=[("files", (name, content, "application/octet-stream"))])
    assert response.status_code == 422 and message in response.text
    assert llm.calls == []
    assert job_of(llm_client) is None


def test_images_need_vision_and_are_sent_then_cleaned_up(llm_client, settings) -> None:
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*FIVE), vision=True)
    create(llm_client, files=[("files", ("pic.png", b"\x89PNGdata", "image/png"))])
    parts = llm.calls[0]["messages"][1]["content"]
    assert parts[0]["type"] == "text" and parts[1]["image_url"]["url"].startswith("data:image/png")
    folder = drafts_dir(settings.db_path) / "1"
    assert (folder / "img0.png").is_file()

    llm_client.post("/builder/drafts/1/discard", follow_redirects=False)
    assert not folder.exists() and job_of(llm_client) is None


def test_material_over_context_is_refused(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*FIVE), context_tokens=2000)
    response = create(llm_client, material="x" * 20000)
    assert response.status_code == 422 and "passt nicht" in response.text
    assert llm.calls == []


def _draft(llm_client, *kinds: str) -> FakeLLM:
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*kinds))
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
    assert (
        questions[1]["question"].startswith("Frage multiple 9?")
        and questions[1]["topic"] == "Thema"
    )
    assert job.regenerating == []
    prompt = llm.calls[-1]["prompt"]
    assert 'topic "Thema"' in prompt and "Frage single 1?" in prompt
    assert "Frage multiple 2?" not in prompt  # the replaced question is not in the avoid list


def test_regenerate_failure_is_reported_and_keeps_question(llm_client) -> None:
    llm = _draft(llm_client, "single")
    llm.replies.append(LLMError("cannot reach the LLM"))
    llm_client.post("/builder/drafts/1/questions/ai-1-001/regenerate", follow_redirects=False)
    job = job_of(llm_client)
    assert job.draft["questions"][0]["question"].startswith("Frage single 1?")
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


def test_accept_batch_fills_slots_and_reports_problems() -> None:
    slots = plan_slots(["single", "open"]) + [{"type": "single", "topic": "Fix"}]
    bad = question("single") | {"correct": [7]}
    items = {"questions": [bad, question("open"), question("single", 2), question("open", 3), "x"]}
    filled, problems = accept_batch(items, slots, 4, ["a", "b", "c"])
    assert [(i, q["id"], q["type"]) for i, q in filled] == [(0, "a", "single"), (1, "b", "open")]
    assert any("correct" in p for p in problems) and any("unknown type" in p for p in problems)
    assert (
        "1 extra question(s) dropped" in problems
        and "1 of 3 requested question(s) missing" in problems
    )


def test_accept_batch_slot_topic_wins_and_option_count_checked() -> None:
    filled, _ = accept_batch(reply("single"), [{"type": "single", "topic": "Fix"}], None, ["a"])
    assert filled[0][1]["topic"] == "Fix"
    filled, problems = accept_batch(reply("single"), plan_slots(["single"]), 3, ["a"])
    assert filled == [] and "expected 3 options" in problems[0]


def test_accept_batch_rejects_non_list() -> None:
    with pytest.raises(ValueError):
        accept_batch({"nope": 1}, plan_slots(["single"]), None, ["a"])


def test_schema_limits_count_and_types() -> None:
    schema = question_schema(plan_slots(["open", "open"]), None, False)["properties"]["questions"]
    assert schema["maxItems"] == 2 and schema["items"]["properties"]["type"]["enum"] == ["open"]
    required = schema["items"]["required"]
    assert {"scenario", "model_answer", "rubric", "points"} <= set(required)
    assert "source" not in required and "options" not in required


def test_plan_types_orders_by_type() -> None:
    assert plan_types(2, 1, 1) == ["single", "single", "multiple", "open"]


def test_builder_preselects_env_default_and_flags_missing_one(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM(models=("a-model", "fake", "z-model"))
    page = llm_client.get("/builder").text
    assert '<option value="fake" selected' in page
    assert "LLM_MODEL" not in page

    llm_client.app.state.llm = FakeLLM(models=("a-model", "z-model"))
    page = llm_client.get("/builder").text
    assert '<option value="a-model" selected' in page and "LLM_MODEL" in page


def outline_reply(*kinds: str, topic: str = "Thema") -> dict:
    return {"questions": [{"type": k, "topic": topic} for k in kinds]}


def test_free_mode_outlines_then_generates(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(
        outline_reply("open", "single", "single"), reply("single", "single"), reply("open", start=3)
    )
    create(llm_client, single="", multiple="", open="", options="")
    job = job_of(llm_client)
    assert job.status == "done" and job.total == 3
    assert [q["type"] for q in job.draft["questions"]] == ["single", "single", "open"]
    first = llm.calls[0]
    assert "Do not write the questions yet" in first["prompt"]
    assert first["schema"]["properties"]["questions"]["maxItems"] == 100
    assert "2-10 (as the question needs)" in llm.calls[1]["prompt"]


def test_free_mode_with_fixed_total_and_one_fixed_type(llm_client) -> None:
    # the outline ignores the rules: 1 open instead of 2, plus an excluded multiple
    llm = llm_client.app.state.llm = FakeLLM(
        outline_reply("open", "multiple", "single", "single", "single", "single"),
        reply("single", "single", "single"),
        reply("open", "open", start=4),
    )
    create(llm_client, single="", multiple="0", open="2", total="5")
    job = job_of(llm_client)
    assert job.status == "done"
    assert [q["type"] for q in job.draft["questions"]] == ["single"] * 3 + ["open"] * 2
    schema = llm.calls[0]["schema"]["properties"]["questions"]
    assert schema["minItems"] == schema["maxItems"] == 5
    assert schema["items"]["properties"]["type"]["enum"] == ["single", "open"]


def test_outline_unusable_fails_job(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM({"nope": 1}, outline_reply("multiple"))
    create(llm_client, single="", multiple="0", open="0")
    job = job_of(llm_client)
    assert job.status == "failed" and "outline" in job.errors[0]


def test_total_below_fixed_counts_is_refused(llm_client) -> None:
    response = create(llm_client, single="4", multiple="", open="", total="2")
    assert response.status_code == 422


def test_all_fixed_needs_no_outline(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*FIVE))
    create(llm_client, total="99")  # ignored: every type is fixed
    assert job_of(llm_client).total == 5 and len(llm.calls) == 3  # one call per type


def test_old_draft_params_are_migrated_on_resume(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply("single"), reply("open", start=2))
    params = {
        "course": "Kurs", "title": "Alt", "single": 1, "multiple": 0, "open": 1,
        "options": 4, "language": "de",
    }  # fmt: skip
    with db(llm_client) as session:
        session.add(GenerationJob(params=params, model="fake", status="failed"))
        session.commit()
    llm_client.post("/builder/drafts/1/retry", follow_redirects=False)
    job = job_of(llm_client)
    assert job.status == "done" and len(job.draft["questions"]) == 2 and len(llm.calls) == 2


def test_job_log_records_steps_and_live_output(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM(outline_reply("single"), reply("single"))
    create(llm_client, single="", multiple="0", open="0", options="")
    job = job_of(llm_client)
    texts = [event["text"] for event in job.log]
    assert texts[0] == "Started with fake"
    assert "Planning the exam (outline)" in texts and "Outline: 1 questions (1 single)" in texts
    assert any(t.startswith("Writing 1 questions") for t in texts)
    assert texts[-1] == "Done: 1 questions"
    assert [e["n"] for e in job.log] == list(range(1, len(job.log) + 1))
    assert {seg["kind"] for seg in job.live} == {"thinking", "output"}


def test_job_log_records_errors(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM(LLMError("cannot reach the LLM"))
    create(llm_client)
    log = job_of(llm_client).log
    assert log[-1] == log[-1] | {"kind": "error", "text": "cannot reach the LLM"}


def test_log_endpoint_pages_by_number(llm_client) -> None:
    _draft(llm_client, "single")
    data = llm_client.get("/api/builder/drafts/1/log").json()
    assert data["next"] == len(data["events"]) and data["status"] == "done"
    assert data["busy"] is False and data["live"]
    later = llm_client.get(f"/api/builder/drafts/1/log?after={data['next'] - 1}").json()
    assert [e["n"] for e in later["events"]] == [data["next"]]


def test_draft_page_shows_log(llm_client) -> None:
    _draft(llm_client, "single")
    page = llm_client.get("/builder/drafts/1").text
    assert "Live-Protokoll" in page and "Started with fake" in page


def test_job_log_caps_live_text(llm_client) -> None:
    from app.jobs import JobLog

    _draft(llm_client, "single")
    log = JobLog(llm_client.app.state.engine, 1)
    log.on_text("output", "a" * 5000)
    log.on_text("thinking", "b" * 5000)
    log.flush()
    live = job_of(llm_client).live
    assert sum(len(seg["text"]) for seg in live) == JobLog.MAX_LIVE_CHARS
    assert live[-1] == {"kind": "thinking", "text": "b" * 5000}


def test_choice_schema_bounds_options_and_single_correct() -> None:
    single_items = question_schema(plan_slots(["single"]), 4, True)["properties"]["questions"][
        "items"
    ]
    props = single_items["properties"]
    assert props["options"]["minItems"] == props["options"]["maxItems"] == 4
    assert props["correct"]["maxItems"] == 1 and "source" in single_items["required"]
    assert {"explanation", "option_explanations"} <= set(single_items["required"])
    free = question_schema(plan_slots(["multiple"]), None, False)["properties"]["questions"]
    assert free["items"]["properties"]["options"]["minItems"] == 2
    assert "maxItems" not in free["items"]["properties"]["correct"]


def test_batches_hold_one_type_and_send_language_and_temperature(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(*per_type(*FIVE))
    create(llm_client)
    enums = [
        c["schema"]["properties"]["questions"]["items"]["properties"]["type"]["enum"]
        for c in llm.calls
    ]
    assert enums == [["single"], ["multiple"], ["open"]]
    first = llm.calls[0]
    assert first["messages"][0]["role"] == "system" and "Deutsch" in first["messages"][0]["content"]
    assert first["prompt"].rstrip().endswith(first["messages"][0]["content"])
    assert first["temperature"] == 0.4


def test_question_without_model_answer_or_explanation_is_rejected() -> None:
    no_answer = question("open") | {"model_answer": ""}
    filled, problems = accept_batch({"questions": [no_answer]}, plan_slots(["open"]), None, ["a"])
    assert filled == [] and "model_answer is missing" in problems[0]
    no_why = question("single") | {"explanation": ""}
    filled, problems = accept_batch({"questions": [no_why]}, plan_slots(["single"]), None, ["a"])
    assert filled == [] and "explanation is missing" in problems[0]


def test_strip_option_labels() -> None:
    from app.builder import strip_option_labels

    assert strip_option_labels(["A. eins", "B. zwei", "C. drei"]) == ["eins", "zwei", "drei"]
    assert strip_option_labels(["a) eins", "b) zwei"]) == ["eins", "zwei"]
    assert strip_option_labels(["1. eins", "2. zwei"]) == ["eins", "zwei"]
    # not a consistent sequence: real content stays
    assert strip_option_labels(["A. eins", "zwei"]) == ["A. eins", "zwei"]
    assert strip_option_labels(["B. zwei", "A. eins"]) == ["B. zwei", "A. eins"]
    assert strip_option_labels(["3.5 GHz", "4.0 GHz"]) == ["3.5 GHz", "4.0 GHz"]


def test_labels_are_stripped_in_accepted_questions() -> None:
    labelled = question("single") | {"options": ["A. Kernel", "B. Shell", "C. Pipe", "D. Heap"]}
    filled, _ = accept_batch({"questions": [labelled]}, plan_slots(["single"]), 4, ["a"])
    assert filled[0][1]["options"] == ["Kernel", "Shell", "Pipe", "Heap"]


def test_duplicates_are_dropped_within_batch_and_against_draft() -> None:
    first, again = question("single", 1), question("single", 1) | {"explanation": "anders"}
    filled, problems = accept_batch(
        {"questions": [first, again]}, plan_slots(["single", "single"]), None, ["a", "b"]
    )
    assert len(filled) == 1 and "duplicates an existing question" in problems[0]
    filled, problems = accept_batch(
        {"questions": [question("single", 1)]}, plan_slots(["single"]), None, ["a"], [first]
    )
    assert filled == [] and "duplicates" in problems[0]


def test_variants_of_a_task_are_not_duplicates() -> None:
    from app.builder import is_duplicate

    a = "Berechnen Sie die mittlere Wartezeit bei RR mit Quantum 1. P1=7, P2=3, P3=1"
    b = "Berechnen Sie die mittlere Wartezeit bei FCFS. P1=2, P2=9, P3=4, P4=6"
    assert not is_duplicate(a, [b])
    assert is_duplicate("Was ist ein Thread?", ["Was ist ein Thread ?"])


@pytest.mark.parametrize(
    "text",
    [
        "Using the given coordination operations, order PA and PB.",
        "Beschreiben Sie, wie die gegebenen Operationen wirken.",
        "Siehe oben: wie lange dauert es?",
        "Welche Aufgabe im Material behandelt Paging?",
    ],
)
def test_dangling_references_are_dropped(text) -> None:
    item = question("open") | {"question": text}
    filled, problems = accept_batch({"questions": [item]}, plan_slots(["open"]), None, ["a"])
    assert filled == [] and "context it does not contain" in problems[0]


def test_single_topic_outline_is_retried_once(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(
        outline_reply(*["single"] * 6, topic="Betriebssysteme"),
        {"questions": [{"type": "single", "topic": t} for t in "ABCABC"]},
        reply(*["single"] * 5),
        reply("single", start=6),
    )
    create(llm_client, single="", multiple="0", open="0", options="")
    job = job_of(llm_client)
    assert job.status == "done" and len(llm.calls) == 4
    assert "distinct sub-topics" in llm.calls[1]["messages"][-1]["content"]
    assert {q["topic"] for q in job.draft["questions"]} == {"A", "B", "C"}


def test_single_topic_outline_is_accepted_on_second_try(llm_client) -> None:
    same = outline_reply(*["single"] * 6, topic="Betriebssysteme")
    llm_client.app.state.llm = FakeLLM(same, same, reply(*["single"] * 5), reply("single", start=6))
    create(llm_client, single="", multiple="0", open="0", options="")
    assert job_of(llm_client).status == "done"


def answers(**picked: list[int]) -> dict:
    return {"answers": [{"id": qid.replace("_", "-"), "correct": c} for qid, c in picked.items()]}


def _checked_draft(llm_client, *extra) -> FakeLLM:
    llm = llm_client.app.state.llm = FakeLLM(
        reply("single", "single"), answers(ai_1_001=[1], ai_1_002=[3]), *extra
    )
    create(llm_client, single="2", multiple="0", open="0", verify="1")
    return llm


def test_cross_check_flags_disagreeing_answer_keys(llm_client) -> None:
    llm = _checked_draft(llm_client)
    job = job_of(llm_client)
    assert job.status == "done" and job.flags == {"ai-1-002": [3]}
    check = llm.calls[1]
    assert check["temperature"] == 0.0 and "weil" not in check["prompt"]  # no explanations
    assert check["schema"]["properties"]["answers"]["items"]["properties"]["id"]["enum"] == [
        "ai-1-001",
        "ai-1-002",
    ]
    assert any(e["text"] == "Cross-check: 1 of 2 disagreed" for e in job.log)
    page = llm_client.get("/builder/drafts/1").text
    assert "wählte das Modell D, die Lösung sagt B" in page
    assert "1 Frage zum Prüfen markiert" in page


def test_cross_check_failure_is_only_a_warning(llm_client) -> None:
    llm_client.app.state.llm = FakeLLM(reply("single"), LLMError("cannot reach the LLM"))
    create(llm_client, single="1", multiple="0", open="0", verify="1")
    job = job_of(llm_client)
    assert job.status == "done" and job.flags == {} and len(job.draft["questions"]) == 1
    assert any(e["kind"] == "warning" and "Cross-check failed" in e["text"] for e in job.log)


def test_no_cross_check_without_checkbox(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply("single", "single"))
    create(llm_client, single="2", multiple="0", open="0")
    assert len(llm.calls) == 1 and job_of(llm_client).flags == {}


def test_open_questions_are_not_cross_checked(llm_client) -> None:
    llm = llm_client.app.state.llm = FakeLLM(reply("open"))
    create(llm_client, single="0", multiple="0", open="1", verify="1")
    assert len(llm.calls) == 1


def test_edit_and_drop_clear_flags(llm_client) -> None:
    _checked_draft(llm_client)
    edited = question("single", 2) | {"question": "Ganz neu formuliert: Paging?"}
    llm_client.post(
        "/builder/drafts/1/questions/ai-1-002/edit",
        data={"json": json.dumps(edited)},
        follow_redirects=False,
    )
    assert job_of(llm_client).flags == {}


def test_regenerate_rechecks_the_new_question(llm_client) -> None:
    _checked_draft(llm_client, reply("single", start=9), answers(ai_1_002=[1]))
    llm_client.post("/builder/drafts/1/questions/ai-1-002/regenerate", follow_redirects=False)
    assert job_of(llm_client).flags == {}  # the new key agrees with the check


def test_cross_check_ignores_out_of_range_indices() -> None:
    from app.builder import Material, cross_check

    q = question("single") | {"id": "x"}
    llm = FakeLLM({"answers": [{"id": "x", "correct": [1, 7, -1]}, {"id": "nope", "correct": [0]}]})
    assert cross_check(llm, "m", Material(), [q]) == {}


def test_verify_checkbox_defaults_on_and_keeps_choice_on_errors(llm_client) -> None:
    assert 'name="verify" value="1" checked' in llm_client.get("/builder").text
    page = create(llm_client, title="").text
    assert 'name="verify" value="1" >' in page or 'name="verify" value="1">' in page

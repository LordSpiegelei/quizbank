# quizbank – contributor guide

Context for working on this codebase (humans and coding agents alike).

## 1. Purpose

A self-hosted web app (Docker) for practicing **mock exams** that are imported as **JSON** (e.g. generated with an LLM, see [docs/generate-exam-prompt.md](docs/generate-exam-prompt.md)).
Focus: single/multiple choice with automatic scoring, a realistic exam simulation (timer, pass mark) and targeted repetition of mistakes. Open questions (case studies) are answered as free text and self-assessed against a model answer and rubric.

Core features: topic and tag filters, source references per question, explanations (overall and per option), attempt history per exam, a persistent wrong-answer pool per exam.

### Non-goals
- No AI grading inside the app (free text is compared with the model answer, or pasted into a chat via "copy for Claude")
- No login and no user management: **one user per instance** (one SQLite file). Several users = several instances with their own `./data`
- No runtime language switching: the UI language is fixed per instance (`APP_LANG`, default `en`, `de` available)
- No PDF import, only structured JSON
- No MCP server, no CLI

Planned and possible work lives in [ROADMAP.md](ROADMAP.md).

## 2. Tech stack

| Area | Choice |
|---|---|
| Backend | Python 3.12+, **FastAPI** |
| Database | **SQLite** via **SQLModel**, migrations with **Alembic** (in `migrations/`, not `alembic/`, to avoid a name clash with the package) |
| Frontend | **Jinja2** templates for regular pages, **Alpine.js** for the quiz page (questions as JSON, answers saved via `fetch`). No HTMX, no build step |
| Styling | Hand-written CSS design system in `app/static/app.css` (tokens with `light-dark()`, light/dark via `data-theme`), **Atkinson Hyperlegible Next/Mono** fonts vendored in `app/static/vendor/fonts/`, Lucide icons as sprite `app/static/icons.svg` |
| Charts | CSS bars and an SVG line from Jinja macros, no chart library |
| Content | **Markdown** (markdown-it-py, sanitized with nh3) in questions, scenarios, options and explanations |
| Validation | Pydantic models for the import JSON, exported as `schema/exam.schema.json` |
| Packaging | **uv** (`pyproject.toml` + `uv.lock`) |
| Tests / lint | pytest + httpx TestClient, ruff |
| Deployment | Dockerfile + docker-compose, SQLite on volume `./data`, exam files in `./questions` |

### Genericity
- Nothing course-specific in code: 2–10 options per question, keyboard shortcuts adapt (1–9 / A–J)
- **Auto-import**: every `*.json` in `QUESTIONS_DIR` is imported on startup (idempotent); disable with `AUTO_IMPORT=false`
- UI texts live in `app/locales/en.py` and `de.py` (dicts with identical keys; a test checks keys and placeholders). Another language = another module

## 3. Commands

```bash
uv sync --locked                          # install deps incl. dev
uv run uvicorn app.main:app --reload      # dev server on :8000
uv run pytest                             # tests
uv run python -m app.schemas              # regenerate schema/exam.schema.json (test fails if stale)
uv run ruff check . && uv run ruff format --check .   # what CI runs
docker compose up --build                 # container; binds 127.0.0.1 only (no login)
```
- Docker uses `/data` and `/questions` (env set in the Dockerfile); locally the defaults are `./data` and `./questions`
- `[tool.uv] package = false`: the app is not installed as a package; pytest finds it via `pythonpath = ["."]`

## 4. Project layout

```
quizbank/
├── app/
│   ├── main.py            # FastAPI app, startup (migrations, auto-import), routers
│   ├── config.py          # settings from env vars
│   ├── db.py              # engine, migrations
│   ├── models.py          # SQLModel tables
│   ├── schemas.py         # Pydantic import format (+ schema export)
│   ├── importer.py        # JSON → DB
│   ├── exporter.py        # DB → JSON
│   ├── scoring.py         # pure scoring functions
│   ├── attempts.py, learning.py, stats.py, queries.py, forms.py
│   ├── markdown.py, templating.py
│   ├── locales/           # UI texts: en.py (default), de.py
│   ├── routes/            # courses, exams, attempts, questions
│   ├── templates/         # Jinja2
│   └── static/            # app.css, icons.svg, shell.js (theme, menus, form helpers), quiz.js (Alpine), vendor/
├── migrations/            # Alembic
├── schema/exam.schema.json
├── examples/              # neutral sample exams (validated by tests)
├── docs/                  # generation prompt, screenshots
├── data/                  # SQLite (gitignored)
├── questions/             # your own exam files (gitignored)
├── tests/
├── ROADMAP.md
└── Dockerfile, docker-compose.yml, pyproject.toml, uv.lock, alembic.ini
```

## 5. Import format (JSON)

One file = one exam. The Pydantic model in `app/schemas.py` is the source of truth; `schema/exam.schema.json` is generated from it.

```json
{
  "format_version": 1,
  "course": "Example Course",
  "title": "Practice exam 1",
  "time_limit_minutes": 60,
  "pass_mark": 10,
  "questions": [
    {
      "id": "ex-p1-001",
      "type": "single",
      "topic": "Topic A",
      "tags": ["concept-x"],
      "question": "Which statement …?",
      "options": ["…", "…", "…", "…"],
      "correct": [2],
      "points": 1,
      "explanation": "Why C is correct …",
      "option_explanations": ["A is wrong because …", "…", "…", "…"],
      "source": "Chapter 3, p. 42"
    },
    {
      "id": "ex-p1-002",
      "type": "open",
      "topic": "Case study",
      "scenario": "Situation …",
      "question": "Analyse …",
      "points": 10,
      "model_answer": "…",
      "rubric": [
        { "criterion": "Names risks X and Y", "points": 4 },
        { "criterion": "Derives a recommendation", "points": 6 }
      ]
    }
  ]
}
```

Rules:
- `type`: `single` | `multiple` | `open`
- `topic`: main topic (required), used for filters and statistics; `tags`: optional keywords, also filterable
- `source`: free-form reference (optional)
- `correct`: 0-based indices; exactly one for `single`
- `option_explanations`, `explanation`, `scenario`, `rubric`: optional
- `pass_mark`, `time_limit_minutes`: optional (fallback: course defaults); `multiple_scoring` optional (see §7)
- Import is **idempotent** by `id` (same ID → question is updated, not duplicated)
- Exam identity = `course` (matched against `Course.name`) + `title`. **Files win** over the UI: the file is authoritative and questions no longer in it are deleted (attempts keep their snapshots, see §6)
- Browser uploads only go to the DB, not to `QUESTIONS_DIR`. Deleting an exam in the UI warns "comes back on restart" only if `QUESTIONS_DIR/source_file` actually exists
- Validation errors are shown per question with a path (e.g. `questions[4].correct: index 5 out of range`)

## 6. Data model and attempt flow

- `Course` (id, name, display_name, default_time_limit, default_pass_mark)
  - `name` = import key, immutable for imported courses; the UI only renames `display_name` (fallback `name`). Search/sort use the displayed name. Exam titles have no display name
- `Exam` (id, course_id, title, time_limit_minutes, pass_mark, multiple_scoring, source_file, imported_at)
- `Question` (id, external_id [unique per exam], exam_id, position, type, topic, tags, source, question, scenario, options, correct, points, explanation, option_explanations, model_answer, rubric, starred, disabled, in_wrong_pool, pool_streak)
  - `starred`, `disabled`, `in_wrong_pool`, `pool_streak` = local learning state; imports never touch them
- `Attempt` (id, exam_id, mode [`exam` | `practice` | `retry_wrong`], feedback [`instant` | `end`], filter, pass_mark, time_limit_minutes, multiple_scoring, deadline, started_at, finished_at, duration_seconds, score, max_score, passed)
  - `pass_mark`, `time_limit_minutes`, `multiple_scoring` = effective values, frozen at start
- `Answer` (id, attempt_id, question_id [nullable, `ON DELETE SET NULL`], position, snapshot, selected, text_answer, rubric_scores, self_score, revealed, is_correct)
  - **One row per question in the attempt**, created at start (unanswered = `selected is None`)
  - `snapshot` = frozen question **in display order** (options shuffled, `correct`/`option_explanations` remapped). `selected` always refers to the snapshot. Review and statistics read only the snapshot, so re-imports never change history
  - `question_id` links to the live question (star, pool, retry); `None` once the question was removed from its file
  - `self_score` = sum of `rubric_scores` (entered directly when there is no rubric)

### Attempts
- **Exam mode**: always **all** questions (ignores `disabled`), `feedback = end`. `disabled` and filters only apply to practice and retry
- **Timer**: server deadline `deadline = started_at + limit`; time keeps running while the page is closed. The client gets the remaining seconds from the server. Expired attempts are finalized **lazily** (`finalize_expired`, on reading attempts and on every write) with `finished_at = deadline`; unanswered questions score 0. Without exam mode there is no countdown, only elapsed time
- **Practice**: topics and tags each filter with OR (no selection = all), combined with AND; disabled questions are dropped; "count" draws a random sample (kept in file order unless shuffling). No countdown, no pass mark. Shuffling questions/options works in all modes
- **Instant feedback**: a "Check" button per question (also for `multiple`/`open`), then "Next". Start-dialog settings are stored in `Attempt.filter`; "Discard and restart" reuses them
- **At most one unfinished attempt per exam**: "Resume" or "Discard and restart" (discard deletes it)
- **Solutions reach the client only when they may be shown**: start data contains question, options, scenario, topic, points and current `selected`. Instant feedback goes through a `reveal` endpoint (sets `revealed`, the answer is locked afterwards); otherwise via the review page after submitting
- **Saving answers**: `PUT /api/attempts/{id}/answers/{answer_id}` with `{selected}` or `{text_answer}`; **409** if the attempt is finished, the deadline has passed (triggers finalization) or the answer is `revealed`. Validates indices, and exactly one for `single`
- `score`/`max_score`/`passed` are stored on finalization and recomputed from snapshots only on self-assessment

### Wrong pool (per exam)
- **In**: questions that were wrong, partially correct or unanswered in a **finished attempt of any mode** (`open`: self-score below full points); `pool_streak` reset to 0. Updated on finalization (also lazy), never during an attempt; discarded attempts change nothing. `open` questions count at their **first** self-assessment only
- **Out**: after **2 correct answers in a row** (any mode) or manually. Pure function `update_pool(in_pool, streak, is_correct)` in `scoring.py`, threshold as a constant
- **Retry** (`retry_wrong`): the whole pool without disabled questions, optionally a random sample. The exam page shows pool size and list; the button is disabled when the pool is empty
- **Per-topic statistics**: snapshot topic of finished attempts in all modes; `open` only after self-assessment; partially correct counts as wrong

### Reset and export
- "Reset learning state" (stars, disabled, pool, streaks; per exam and global) and "Delete history" (per exam, leaves the pool alone) are separate actions, each with its own confirmation
- Export = exactly `format_version: 1` from the live questions in `position` order (round-trips unchanged on re-import), without learning state; `course` = import name, the exam's own values instead of course defaults

## 7. Scoring rules

- `single`: correct = full points, otherwise 0
- `multiple`: all-or-nothing by default; per-exam field `multiple_scoring` (`all_or_nothing` | `partial`)
  - `partial`: `points × max(0, correct picks − wrong picks) / number of correct options`
- `open`: sum of self-assigned rubric points (clamped to 0…`points`); correct at full points; `is_correct = None` until self-assessed
- No negative marking
- `passed = score >= pass_mark`; stays `None` while an `open` question is not yet self-assessed
- `passed` only for `mode = exam` over all questions; practice and retry show points and percent only (`passed = None`)
- "Wrong" (pool and statistics) = `is_correct is False` **or unanswered**; partially correct `multiple` counts as wrong, unassessed `open` does not count

## 8. Conventions

- **UI texts** only in `app/locales/` (new keys always in both `en.py` and `de.py`), never hard-coded in templates; tests run with `APP_LANG=de`
- Code, identifiers, commits and docs in English; Conventional Commits (`feat:`, `fix:`, `docs:` …)
- Scoring logic in `scoring.py` as pure functions with unit tests
- Schema changes go through an Alembic migration; user databases must never break on update
- After changing `app/schemas.py`, regenerate `schema/exam.schema.json`
- Before committing: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
- Never commit exam files based on copyrighted course material: `questions/` and `data/` are gitignored. Examples in `examples/` must be neutral and self-written
- Configuration via env vars: `DB_PATH` (default `./data/quizbank.db`), `PORT` (`8000`), `QUESTIONS_DIR` (`./questions`), `AUTO_IMPORT` (`true`), `APP_LANG` (`en`)

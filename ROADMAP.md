# Roadmap

## Shipped (v0.1.0)

- JSON import (browser upload and auto-import from `./questions`) with per-question validation errors, idempotent by question `id`
- Courses and exams: rename, delete, course defaults for time limit and pass mark
- Exam mode with server-side countdown, auto-submit and pass/fail; practice mode with topic/tag filters, question count and "starred only"
- Instant feedback or review at the end, with explanations per option and source references
- Attempt history, per-topic statistics and a score chart per exam
- Persistent wrong-answer pool per exam (out after 2 correct answers in a row) with a retry mode
- Star and disable questions, shuffle questions and options, reset learning state, delete history
- Open questions (case studies): free text, model answer, rubric-based self-assessment, "copy for Claude"
- Export an exam back to JSON (lossless round-trip)
- Keyboard control, light/dark theme, English and German UI
- Docker image for amd64 and arm64 on GHCR

## Ideas

- Math rendering with KaTeX (`$…$` and `$$…$$`), vendored like the other front-end assets

Suggestions are welcome as GitHub issues.

## Not planned

- Login or multiple users per instance (run one instance per person instead)
- AI grading inside the app
- Importing PDFs or other unstructured material
- Switching the UI language at runtime

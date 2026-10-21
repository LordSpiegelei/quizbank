# quizbank

[![CI](https://github.com/LordSpiegelei/quizbank/actions/workflows/ci.yml/badge.svg)](https://github.com/LordSpiegelei/quizbank/actions/workflows/ci.yml)
[![Docker](https://github.com/LordSpiegelei/quizbank/actions/workflows/docker.yml/badge.svg)](https://github.com/LordSpiegelei/quizbank/actions/workflows/docker.yml)

Self-hosted web app for practicing mock exams. Bring your own question banks as JSON
(e.g. generated with an LLM), organise them by course, and drill them with a timer,
pass marks, explanations and "retry wrong answers".

> **Status:** early development – not usable yet.

## Quickstart (Docker)

```sh
docker compose up -d
```

Then open <http://localhost:8000>. Data is stored in `./data`, question files can be
placed in `./questions`.

> The app has no login. It is bound to `127.0.0.1` by default – do not expose it to
> the internet without putting authentication in front of it.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                   # install dependencies (incl. Python 3.12)
uv run pre-commit install                 # ruff on every commit
uv run uvicorn app.main:app --reload      # dev server on :8000
uv run pytest                             # tests
uv run ruff check . && uv run ruff format .
```

## Releases

Pushing a tag `vX.Y.Z` publishes a multi-arch image to
`ghcr.io/lordspiegelei/quizbank`.

## License

[MIT](LICENSE)

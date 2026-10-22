FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app


FROM python:3.12-slim

RUN useradd --create-home --uid 1000 quizbank \
    && mkdir -p /data /questions \
    && chown quizbank:quizbank /data /questions

WORKDIR /app
COPY --from=builder --chown=quizbank:quizbank /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DB_PATH=/data/quizbank.db \
    QUESTIONS_DIR=/questions

USER quizbank
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

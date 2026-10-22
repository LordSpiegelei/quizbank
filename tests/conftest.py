from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session

from app.config import Settings
from app.db import make_engine, run_migrations
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        questions_dir=tmp_path / "questions",
        auto_import=False,
        app_lang="de",
    )


@pytest.fixture
def engine(settings) -> Iterator[Engine]:
    """Fresh, migrated SQLite database per test (without the web app)."""
    engine = make_engine(settings.db_path)
    run_migrations(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client

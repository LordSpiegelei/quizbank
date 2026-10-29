from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from alembic import command
from alembic.config import Config
from fastapi import Depends, HTTPException, Request
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from app.attempts import finalize_expired

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _casefold(value: str | None) -> str | None:
    return None if value is None else value.casefold()


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        # Unicode-aware casefold() for search/sort; SQLite's lower() only folds ASCII.
        dbapi_connection.create_function("casefold", 1, _casefold, deterministic=True)

    return engine


def run_migrations(engine: Engine) -> None:
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        # Lazy auto-submit: every request sees attempts whose deadline passed as finished.
        finalize_expired(session)
        request.state.session = session  # reused by render() for the sidebar
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_or_404[M: SQLModel](session: Session, model: type[M], row_id: int) -> M:
    row = session.get(model, row_id)
    if row is None:
        raise HTTPException(status_code=404)
    return row

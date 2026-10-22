from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.config import Settings
from app.db import make_engine

config = context.config


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=SQLModel.metadata,
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
    )
    with context.begin_transaction():
        context.run_migrations()


# The app passes its own connection (app.db.run_migrations); the alembic CLI uses DB_PATH.
if (connection := config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
    with make_engine(Settings.from_env().db_path).connect() as connection:
        run_migrations(connection)

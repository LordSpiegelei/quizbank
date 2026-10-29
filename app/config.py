import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: Path
    questions_dir: Path
    auto_import: bool
    app_lang: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=Path(os.environ.get("DB_PATH", "./data/quizbank.db")),
            questions_dir=Path(os.environ.get("QUESTIONS_DIR", "./questions")),
            auto_import=_env_bool("AUTO_IMPORT", True),
            app_lang=os.environ.get("APP_LANG", "en"),
        )

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str) -> str | None:
    """Trimmed value without one pair of surrounding quotes: in a compose file's list form
    (`- LLM_MODEL="x"`) the quotes are part of the value. Empty means not set."""
    value = os.environ.get(name, "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value or None


def _env_int(name: str) -> int | None:
    value = _env_str(name)
    return int(value) if value else None


@dataclass(frozen=True)
class Settings:
    db_path: Path
    questions_dir: Path
    auto_import: bool
    app_lang: str
    # Optional LLM (any OpenAI-compatible server, e.g. Ollama); off unless llm_base_url is set.
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout: float = 300.0
    llm_capabilities: frozenset[str] = frozenset()  # e.g. {"vision"}, for servers without probing
    llm_context_tokens: int | None = None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url)

    @classmethod
    def from_env(cls) -> "Settings":
        capabilities = _env_str("LLM_CAPABILITIES") or ""
        return cls(
            db_path=Path(os.environ.get("DB_PATH", "./data/quizbank.db")),
            questions_dir=Path(os.environ.get("QUESTIONS_DIR", "./questions")),
            auto_import=_env_bool("AUTO_IMPORT", True),
            app_lang=os.environ.get("APP_LANG", "en"),
            llm_base_url=(_env_str("LLM_BASE_URL") or "").rstrip("/") or None,
            llm_model=_env_str("LLM_MODEL"),
            llm_api_key=_env_str("LLM_API_KEY"),
            llm_timeout=float(_env_str("LLM_TIMEOUT") or 300),
            llm_capabilities=frozenset(
                part.strip().lower() for part in capabilities.split(",") if part.strip()
            ),
            llm_context_tokens=_env_int("LLM_CONTEXT_TOKENS"),
        )

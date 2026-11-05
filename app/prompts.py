"""Prompt texts for the LLM: plain-text Jinja templates in `templates/llm/`, in English whatever
the UI language (models follow English instructions best). Unlike the HTML templates these are
not autoescaped. The language of the *output* is passed in and named in the prompt."""

from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.templating import TEMPLATE_DIR

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR / "llm"),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)

LANGUAGE_NAMES = {"en": "English", "de": "German"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def render_prompt(name: str, **context: Any) -> str:
    return _env.get_template(name).render(**context).strip()

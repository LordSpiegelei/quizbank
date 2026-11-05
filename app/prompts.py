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
_LANGUAGE_ALIASES = {"deutsch": "de", "german": "de", "englisch": "en", "english": "en"}
# Said in the target language itself: small models follow that far better than a line inside
# an English prompt. Sent as the system message and repeated at the end of the prompt.
LANGUAGE_RULES = {
    "de": (
        "Schreibe alle Texte – Fragen, Optionen, Erklärungen, Musterlösungen, Feedback – "
        "ausschließlich auf Deutsch, auch wenn Anweisungen oder Material englisch sind."
    ),
    "en": (
        "Write every text – questions, options, explanations, model answers, feedback – "
        "in English only, even if the material is in another language."
    ),
}


def language_code(language: str) -> str:
    """'de', 'Deutsch', 'German' -> 'de'; unknown values are kept as given."""
    value = language.strip().casefold()
    return _LANGUAGE_ALIASES.get(value, value)


def language_name(language: str) -> str:
    code = language_code(language)
    return LANGUAGE_NAMES.get(code, language)


def language_rule(language: str) -> str:
    code = language_code(language)
    return LANGUAGE_RULES.get(code, f"Write every text in {language_name(language)} only.")


def system_message(language: str) -> dict[str, str]:
    return {"role": "system", "content": language_rule(language)}


def render_prompt(name: str, **context: Any) -> str:
    return _env.get_template(name).render(**context).strip()

"""UI texts. Add a language by creating `<lang>.py` with a `TEXTS` dict using the same keys."""

from collections.abc import Callable
from importlib import import_module

Translator = Callable[..., str]


def get_translator(lang: str) -> Translator:
    texts: dict[str, str] = import_module(f"app.locales.{lang}").TEXTS

    def t(key: str, **kwargs: object) -> str:
        text = texts.get(key, key)
        return text.format(**kwargs) if kwargs else text

    return t

"""UI texts. Add a language by creating `<lang>.py` with a `TEXTS` dict using the same keys.

A text with an `n` placeholder may have a `<key>.one` variant, used when n is 1 (quiz.js
applies the same rule)."""

from collections.abc import Callable
from importlib import import_module

Translator = Callable[..., str]


def get_translator(lang: str) -> Translator:
    texts: dict[str, str] = import_module(f"app.locales.{lang}").TEXTS

    def t(key: str, **kwargs: object) -> str:
        n = kwargs.get("n")  # a number, or already formatted by the `num` filter
        if (n == 1 or n == "1") and f"{key}.one" in texts:
            key = f"{key}.one"
        text = texts.get(key, key)
        return text.format(**kwargs) if kwargs else text

    return t

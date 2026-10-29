from dataclasses import replace
from string import Formatter

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.locales import de, en, get_translator
from app.main import create_app


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name}


@pytest.mark.parametrize("other", [de.TEXTS], ids=["de"])
def test_locales_match_english(other: dict[str, str]) -> None:
    """Every language has the same keys and placeholders as the default (en)."""
    assert other.keys() == en.TEXTS.keys()
    for key, text in en.TEXTS.items():
        assert _placeholders(other[key]) == _placeholders(text), key


def test_singular_variant_when_n_is_one() -> None:
    t = get_translator("en")
    assert t("common.points", n=1) == "1 point"
    assert t("common.points", n=1.0) == "1.0 point"
    assert t("common.points", n="1") == "1 point"  # numbers arrive formatted by `num`
    assert t("common.points", n="1.5") == "1.5 points"
    assert t("common.minutes", n=1) == "1 min"  # no .one variant: the plain text


def test_english_is_the_default(monkeypatch) -> None:
    monkeypatch.delenv("APP_LANG", raising=False)
    assert Settings.from_env().app_lang == "en"


def test_english_ui(settings) -> None:
    with TestClient(create_app(replace(settings, app_lang="en"))) as client:
        page = client.get("/").text
    assert '<html lang="en">' in page
    assert "No courses yet" in page

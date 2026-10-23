from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.locales import Translator

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def make_templates(t: Translator) -> Jinja2Templates:
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    decimal_sep = t("format.decimal_sep")
    datetime_format = t("format.datetime")

    def format_number(value: float | None) -> str:
        """30.0 -> "30", 7.5 -> "7,5" with the locale's decimal separator."""
        return "" if value is None else f"{value:g}".replace(".", decimal_sep)

    def format_datetime(value: datetime | None) -> str:
        return "" if value is None else value.astimezone().strftime(datetime_format)

    @pass_context
    def path(context: dict[str, Any], name: str, **params: Any) -> str:
        """Relative URL path of a named route (url_for would build absolute URLs)."""
        return str(context["request"].app.url_path_for(name, **params))

    templates.env.globals["t"] = t
    templates.env.globals["path"] = path
    templates.env.filters["num"] = format_number
    templates.env.filters["dt"] = format_datetime
    return templates


def render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, name, context, status_code=status_code
    )

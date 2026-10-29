from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.learning import FLAGS
from app.locales import Translator
from app.markdown import markdown_text, render_markdown, render_markdown_inline
from app.queries import nav_courses

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def make_templates(t: Translator, url_path_for: Callable[..., Any]) -> Jinja2Templates:
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    decimal_sep = t("format.decimal_sep")
    datetime_format = t("format.datetime")

    def format_number(value: float | None) -> str:
        """30.0 -> "30", 7.5 -> "7,5", 6.3333 -> "6,33" with the locale's decimal separator."""
        return "" if value is None else f"{round(value, 2):g}".replace(".", decimal_sep)

    def format_datetime(value: datetime | None) -> str:
        return "" if value is None else value.astimezone().strftime(datetime_format)

    def format_duration(seconds: int | None) -> str:
        """3725 -> "1:02:05", 125 -> "2:05"."""
        if seconds is None:
            return ""
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"

    def path(name: str, **params: Any) -> str:
        """Relative URL path of a named route (url_for would build absolute URLs). Needs no
        template context, so imported macros can use it too."""
        return str(url_path_for(name, **params))

    templates.env.globals["t"] = t
    templates.env.globals["path"] = path
    templates.env.globals["flags"] = FLAGS
    templates.env.globals["flag_icons"] = {"starred": "star", "disabled": "eye-off"}
    templates.env.globals["icons_url"] = str(url_path_for("static", path="icons.svg"))
    templates.env.filters["num"] = format_number
    templates.env.filters["dt"] = format_datetime
    templates.env.filters["duration"] = format_duration
    templates.env.filters["md"] = render_markdown
    templates.env.filters["md_inline"] = render_markdown_inline
    templates.env.filters["plain"] = markdown_text
    return templates


def redirect(request: Request, name: str, fragment: str = "", **params: Any) -> RedirectResponse:
    """303 See Other to a named route, the answer to a successful form POST."""
    url = str(request.app.url_path_for(name, **params))
    return RedirectResponse(f"{url}#{fragment}" if fragment else url, status_code=303)


def redirect_back(request: Request, next_url: str, fallback: str, **params: Any) -> Response:
    """303 to the page a form came from; only local paths, otherwise to `fallback`."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return RedirectResponse(next_url, status_code=303)
    return redirect(request, fallback, **params)


def _nav_courses(request: Request) -> Sequence[Any]:
    """The sidebar's course list, on the request's session (routes without one open their own)."""
    session = getattr(request.state, "session", None)
    if session is not None:
        return nav_courses(session)
    with Session(request.app.state.engine) as session:
        return nav_courses(session)


def render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    # Called by base.html inside its shell block only, so pages without the sidebar skip it.
    context.setdefault("nav_courses", lambda: _nav_courses(request))
    return request.app.state.templates.TemplateResponse(
        request, name, context, status_code=status_code
    )

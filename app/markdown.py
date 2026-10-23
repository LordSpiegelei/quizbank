"""Markdown in question texts, rendered to sanitized HTML."""

import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup

# Raw HTML in the source is escaped by markdown-it; nh3 is a second line of defense.
_md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")


def render_markdown(text: str | None) -> Markup:
    return Markup(nh3.clean(_md.render(text))) if text else Markup()


def render_markdown_inline(text: str | None) -> Markup:
    """Without the surrounding <p>, for short texts such as answer options."""
    return Markup(nh3.clean(_md.renderInline(text))) if text else Markup()

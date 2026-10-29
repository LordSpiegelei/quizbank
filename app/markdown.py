"""Markdown in question texts, rendered to sanitized HTML."""

from functools import lru_cache

import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup

# Raw HTML in the source is escaped by markdown-it; nh3 is a second line of defense.
_md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")


@lru_cache(maxsize=2048)  # the same question texts render on every page load
def render_markdown(text: str | None) -> Markup:
    return Markup(nh3.clean(_md.render(text))) if text else Markup()


@lru_cache(maxsize=2048)
def render_markdown_inline(text: str | None) -> Markup:
    """Without the surrounding <p>, for short texts such as answer options."""
    return Markup(nh3.clean(_md.renderInline(text))) if text else Markup()


@lru_cache(maxsize=2048)
def markdown_text(text: str | None) -> Markup:
    """Plain text of a Markdown snippet (code fences and markup dropped), for one-line previews.
    Markup because nh3 already escaped it; escaping again would show `&lt;` literally."""
    return Markup(" ".join(nh3.clean(_md.render(text), tags=set()).split())) if text else Markup()

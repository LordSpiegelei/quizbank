from app.markdown import markdown_text, render_markdown, render_markdown_inline


def test_renders_markdown() -> None:
    assert render_markdown("**a** `b`") == "<p><strong>a</strong> <code>b</code></p>\n"
    assert "<table>" in render_markdown("| a |\n|---|\n| 1 |")
    assert render_markdown_inline("*x*") == "<em>x</em>"
    assert render_markdown(None) == ""


def test_escapes_raw_html_and_unsafe_links() -> None:
    assert "<script" not in render_markdown("<script>alert(1)</script>")
    assert "href" not in render_markdown("[x](javascript:alert(1))")
    assert "<img" not in render_markdown_inline('<img src=x onerror="alert(1)">')


def test_markdown_text_for_previews() -> None:
    assert markdown_text("Given:\n\n```css\np { color: red; }\n```\n\nWhy **this**?") == (
        "Given: p { color: red; } Why this?"
    )
    assert markdown_text("`<p>`") == "&lt;p&gt;"  # escaped once, rendered as-is
    assert markdown_text(None) == ""

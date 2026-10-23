from app.markdown import render_markdown, render_markdown_inline


def test_renders_markdown() -> None:
    assert render_markdown("**a** `b`") == "<p><strong>a</strong> <code>b</code></p>\n"
    assert "<table>" in render_markdown("| a |\n|---|\n| 1 |")
    assert render_markdown_inline("*x*") == "<em>x</em>"
    assert render_markdown(None) == ""


def test_escapes_raw_html_and_unsafe_links() -> None:
    assert "<script" not in render_markdown("<script>alert(1)</script>")
    assert "href" not in render_markdown("[x](javascript:alert(1))")
    assert "<img" not in render_markdown_inline('<img src=x onerror="alert(1)">')

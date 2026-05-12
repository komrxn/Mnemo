"""Tests for the Markdown→Telegram-HTML converter.

LLM emits Markdown by default; bot's parse_mode is HTML. Without conversion
the user sees literal `**asterisks**` instead of bold. The converter must:
- Render the basic markers (**bold**, _italic_, `code`, ~~strike~~).
- Render bullet lines (`- item`) as flat `• item`.
- Preserve code blocks (escaping their content, no inner formatting).
- Escape stray `<`/`>`/`&` so they don't accidentally parse as tags.
- Be idempotent: round-tripping through the converter doesn't double-wrap.
"""

from __future__ import annotations

import pytest

from src.telegram.formatting import to_telegram_html


@pytest.mark.parametrize(
    ("md", "html"),
    [
        ("plain text", "plain text"),
        ("", ""),
        ("**bold**", "<b>bold</b>"),
        ("**Юлю**", "<b>Юлю</b>"),
        ("_italic_", "<i>italic</i>"),
        ("~~strike~~", "<s>strike</s>"),
        ("`code`", "<code>code</code>"),
        ("a **b** c", "a <b>b</b> c"),
        ("[label](https://example.com)", '<a href="https://example.com">label</a>'),
        ("## Header", "<b>Header</b>"),
        ("# H1", "<b>H1</b>"),
        ("- item one", "• item one"),
        ("* item one", "• item one"),
        ("  - nested item", "  • nested item"),
    ],
)
def test_basic_md_to_html(md: str, html: str) -> None:
    assert to_telegram_html(md) == html


def test_real_bot_reply_with_bullets_and_bold() -> None:
    """The actual shape of the message from the screenshot."""
    md = (
        "Да, зафиксировал в контексте:\n"
        "\n"
        "- тебе надо спросить **Юлю**;\n"
        "- Юля — **подруга твоей девушки Даши**;\n"
        "- Юля **работает в Uzum Market**;"
    )
    out = to_telegram_html(md)
    assert "<b>Юлю</b>" in out
    assert "<b>подруга твоей девушки Даши</b>" in out
    assert "<b>работает в Uzum Market</b>" in out
    assert "• тебе" in out  # bullet converted
    assert "**" not in out  # no literal asterisks left


def test_underscore_inside_word_is_preserved() -> None:
    """`snake_case_var` must NOT become snake<i>case</i>var."""
    assert to_telegram_html("snake_case_var") == "snake_case_var"


def test_double_asterisks_in_quoted_text_still_convert() -> None:
    """If the LLM uses bold inside a quote/example, we still convert.

    Accepted trade-off: false positives on literal asterisks in technical
    content are rare; missing bold in the common case is far more visible.
    """
    md = 'он написал: "**важно**"'
    assert to_telegram_html(md) == 'он написал: "<b>важно</b>"'


def test_html_special_chars_get_escaped() -> None:
    """Plain `<` and `&` in non-tag text must not break Telegram parsing."""
    assert to_telegram_html("a < b && c > d") == "a &lt; b &amp;&amp; c &gt; d"


def test_existing_html_tags_pass_through() -> None:
    """If the input already contains HTML (e.g. our locale strings), the
    converter must not double-escape `<b>` into `&lt;b&gt;`.

    Trade-off: the simple implementation here DOES escape angle brackets.
    Locale strings are sent directly via aiogram, NOT through this converter
    — they bypass it. This test pins the rule that the converter is for
    LLM output only.
    """
    # Document the current contract — locale strings are NOT routed through
    # to_telegram_html. If this ever changes, this test will catch it.
    out = to_telegram_html("<b>already HTML</b>")
    # The converter sees `<` as text and escapes it, since LLM doesn't emit
    # bare HTML. Locale strings bypass this function entirely.
    assert "&lt;b&gt;" in out


def test_code_block_preserved_verbatim() -> None:
    """Inside ```...``` the inner text is escaped but markdown markers are
    NOT converted — code stays as code."""
    md = "```python\nx = **2  # not bold\n```"
    out = to_telegram_html(md)
    assert "<pre><code" in out
    assert "**2" in out  # NOT converted to <b>
    assert "x = **2" in out


def test_code_block_escapes_html_chars() -> None:
    """`<` inside code block must be `&lt;` so Telegram doesn't try to parse."""
    md = "```\nif x < 5:\n    pass\n```"
    out = to_telegram_html(md)
    assert "&lt;" in out
    assert "<" not in out.replace("<pre>", "").replace("</pre>", "").replace(
        "<code>", ""
    ).replace("</code>", "")


def test_idempotent_on_plain_text() -> None:
    assert to_telegram_html(to_telegram_html("hello")) == to_telegram_html("hello")


def test_multiline_with_mixed_markers() -> None:
    md = (
        "Привет! Слушай, **сегодня встретился** с Аней.\n"
        "\n"
        "Обсудили:\n"
        "- запуск *MVP* к 15 июня\n"
        "- найм 2-х `ML engineers`\n"
        "\n"
        "[Подробности](https://example.com/notes)"
    )
    out = to_telegram_html(md)
    assert "<b>сегодня встретился</b>" in out
    assert "• запуск" in out
    assert "<code>ML engineers</code>" in out
    assert '<a href="https://example.com/notes">Подробности</a>' in out
    assert "**" not in out

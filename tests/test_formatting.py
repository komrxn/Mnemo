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
    """Locale strings (and any LLM output that happens to use HTML directly)
    must survive the converter unchanged. Earlier the converter escaped every
    `<` indiscriminately, so a YAML string like `Буду <b>{{ bot_name }}</b>!`
    turned into `Буду &lt;b&gt;…&lt;/b&gt;!` — and Telegram rendered the
    literal tags as text. The converter is now idempotent on the allow-list
    of Telegram HTML tags.
    """
    assert to_telegram_html("<b>already HTML</b>") == "<b>already HTML</b>"


def test_onboarding_locale_with_html_round_trip() -> None:
    """Regression anchor for the user-visible bug observed 2026-05-18:
    onboarding `ask_personality` reached the user with literal `<b>` text."""
    src = (
        "Буду <b>Johnny Silverhand</b>! Теперь выбери <b>стиль общения</b>:"
    )
    assert to_telegram_html(src) == src


def test_html_with_attributes_passes_through() -> None:
    """<a href="..."> should survive — the href is the source's responsibility."""
    src = '<a href="https://example.com">link</a>'
    assert to_telegram_html(src) == src


def test_mixed_existing_html_and_markdown() -> None:
    """An LLM reply that mixes pre-formatted HTML with raw markdown still
    converts the markdown without mangling the HTML."""
    md = "Hi <b>boss</b>, you said **really**?"
    out = to_telegram_html(md)
    assert "<b>boss</b>" in out
    assert "<b>really</b>" in out
    assert "&lt;" not in out


def test_bare_lt_still_escaped_outside_tags() -> None:
    """Stray `<` in plain text (no tag follows) must still be escaped — we
    don't want users sending `3 < 5` to crash the Telegram HTML parser."""
    assert to_telegram_html("3 < 5") == "3 &lt; 5"


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

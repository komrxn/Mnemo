"""Format bot reply for Telegram's HTML parse mode.

The bot is created with `default=DefaultBotProperties(parse_mode=ParseMode.HTML)`
in `src/telegram/bot.py`, so every `send_message` / `edit_message_text` call is
parsed as HTML. Our hardcoded UI strings already use `<b>`/`<i>` (see
`src/locales/*.yaml`), but LLM-generated replies arrive as **Markdown** by
default — `**bold**`, `_italic_`, `` `code` `` — which Telegram renders
*literally* (asterisks visible, no formatting).

This module converts the subset of Markdown the LLM actually emits into
Telegram-flavored HTML and escapes ambiguous `<`/`>`/`&` characters in plain
text so they don't accidentally parse as tags.

Telegram's HTML support is **limited**: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`,
`<pre>`, `<a href>`, `<blockquote>`. No `<ul>`/`<li>`/`<h1>` — bullets must be
flat `• ` prefixes.

Idempotent: passing already-converted text returns it unchanged (because the
regexes match Markdown markers, not HTML tags).
"""

from __future__ import annotations

import html
import re

# Order matters here — we walk through `text` once, replacing Markdown spans
# with HTML, while keeping a parallel "is this region inside <code>?" flag
# (LLMs sometimes write `**not bold**` inside backticks; we must leave it).
#
# Approach: split by code fences first, convert code regions safely (escape +
# wrap in <pre>/<code>), then convert formatting in non-code regions.

_CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+\-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")

# Bold: **text** (must not match across lines, and must have content)
_BOLD_RE = re.compile(r"\*\*([^*\n][^*]*?[^*\n]|[^*\n])\*\*")
# Italic with underscore: _text_ (single underscore, not __; word-boundary aware
# to avoid matching inside_words_like_this)
_UNDERSCORE_ITALIC_RE = re.compile(r"(?<![A-Za-z0-9_])_([^_\n]+?)_(?![A-Za-z0-9_])")
# Strikethrough: ~~text~~
_STRIKE_RE = re.compile(r"~~([^~\n]+?)~~")
# Headers (#, ##, ###) at line start → render as bold (Telegram has no headers)
_HEADER_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# Bullet lines starting with `-` or `*` (only at line start, with space after)
_BULLET_RE = re.compile(r"^([ \t]*)[\-*][ \t]+", re.MULTILINE)
# Markdown link [text](url)
_LINK_RE = re.compile(r"\[([^\]\n]+?)\]\(([^)\n\s]+?)\)")

# Allowed Telegram HTML tags (https://core.telegram.org/bots/api#html-style).
# Captured as a single group so re.split returns alternating [text, tag, ...]
# chunks — we pass tags through verbatim and only apply escape+markdown to
# the text chunks between them. This is what makes the converter truly
# idempotent on locale strings like "Буду <b>{{ bot_name }}</b>!" — without
# this guard, html.escape would mangle the existing `<b>` into `&lt;b&gt;`
# and Telegram would render the tags literally.
_ALLOWED_TAG_RE = re.compile(
    r"(</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|a|tg-spoiler|span)"
    r"(?:\s+[^>]*)?>)",
    re.IGNORECASE,
)


def _escape_text(s: str) -> str:
    """HTML-escape `<`, `>`, `&` so non-tag occurrences don't break the parser."""
    return html.escape(s, quote=False)


def _convert_text_chunk(s: str) -> str:
    """Apply escape + Markdown→HTML to a chunk of plain text (no allowed tags).

    Order avoids cross-contamination:
        1. Escape `<`/`>`/`&`
        2. Markdown links → <a>
        3. Bold (**...**)
        4. Italic underscore (_..._)
        5. Strikethrough (~~...~~)
        6. Headers (#) → bold
        7. Bullets (-, *) → • prefix
        8. Inline code (`...`)
    """
    s = _escape_text(s)
    s = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", s)
    s = _UNDERSCORE_ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", s)
    s = _STRIKE_RE.sub(lambda m: f"<s>{m.group(1)}</s>", s)
    s = _HEADER_RE.sub(lambda m: f"<b>{m.group(1)}</b>", s)
    s = _BULLET_RE.sub(lambda m: f"{m.group(1)}• ", s)
    s = _INLINE_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", s)
    return s


def _convert_inline(segment: str) -> str:
    """Convert all inline Markdown markers in a non-code segment to HTML.

    Splits the segment around allowed Telegram HTML tags so existing tags
    (from YAML locales or LLM-emitted HTML) survive unchanged while the
    plain-text regions in between still get escape + Markdown treatment.
    """
    # Fast path: no HTML tags at all → run the whole thing through conversion.
    parts = _ALLOWED_TAG_RE.split(segment)
    if len(parts) == 1:
        return _convert_text_chunk(segment)

    # re.split with one capture group yields alternating [text, tag, text, ...].
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Allowed HTML tag — pass through verbatim. Attribute values are
            # the source's responsibility (locale files / LLM output); we
            # don't try to validate or re-escape them here.
            out.append(part)
        elif part:
            out.append(_convert_text_chunk(part))
    return "".join(out)


def _convert_code_block(language: str, body: str) -> str:
    """Render a fenced code block as Telegram's <pre><code class="lang">…</code></pre>."""
    escaped = _escape_text(body.rstrip("\n"))
    if language:
        return f'<pre><code class="language-{language}">{escaped}</code></pre>'
    return f"<pre><code>{escaped}</code></pre>"


def to_telegram_html(text: str) -> str:
    """Convert an LLM-style Markdown reply into Telegram-flavored HTML.

    Empty/whitespace passes through unchanged. The result is safe to send via
    `bot.send_message` / `bot.edit_message_text` with `parse_mode=HTML`.

    Idempotency: passing the *output* back through is also safe — HTML tags
    are preserved as long as they don't look like Markdown markers (they don't).
    """
    if not text:
        return text

    parts: list[str] = []
    last_end = 0
    for match in _CODE_FENCE_RE.finditer(text):
        # Inline (non-code) prefix → convert with markdown rules.
        prefix = text[last_end : match.start()]
        if prefix:
            parts.append(_convert_inline(prefix))
        # Code block → escape + wrap.
        parts.append(_convert_code_block(match.group(1) or "", match.group(2) or ""))
        last_end = match.end()

    tail = text[last_end:]
    if tail:
        parts.append(_convert_inline(tail))

    return "".join(parts)

"""Tests for the persistent reply-keyboard (main commands UI).

The keyboard surface is tiny but routes a lot — every tap is a regular text
message that must NOT reach the LLM. We verify:
  - Keyboard layout and i18n labels.
  - `match_main_kb_button` resolves labels from any language back to the
    command name (handles cross-lang taps after a /lang switch).
  - Unknown text returns None (no false positives feeding into command
    dispatch).
"""

from __future__ import annotations

import pytest

from src.telegram.keyboards import (
    MAIN_KB_LABEL_KEYS,
    main_reply_keyboard,
    match_main_kb_button,
)

# ── layout ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ui_lang", ["ru", "en", "uz"])
def test_main_keyboard_has_2x2_layout(ui_lang: str) -> None:
    kb = main_reply_keyboard(ui_lang)
    assert len(kb.keyboard) == 2  # two rows
    assert all(len(row) == 2 for row in kb.keyboard)  # two buttons per row


@pytest.mark.parametrize("ui_lang", ["ru", "en", "uz"])
def test_main_keyboard_is_resizable_and_persistent(ui_lang: str) -> None:
    kb = main_reply_keyboard(ui_lang)
    assert kb.resize_keyboard is True
    assert kb.is_persistent is True


@pytest.mark.parametrize("ui_lang", ["ru", "en", "uz"])
def test_main_keyboard_buttons_have_localized_labels(ui_lang: str) -> None:
    """Every button label must be a non-empty string (not a missing-key
    fallback like `kb.save` showing through)."""
    kb = main_reply_keyboard(ui_lang)
    for row in kb.keyboard:
        for btn in row:
            assert btn.text
            assert not btn.text.startswith("kb.")  # i18n didn't fall through


# ── label → command routing ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("💾 Запомнить", "save"),
        ("⚙️ Настройки", "settings"),
        ("↩️ Отменить", "undo"),
        ("🆕 Начать заново", "start"),
        # English variants resolve too — cross-lang tolerance after /lang switch
        ("💾 Remember", "save"),
        ("⚙️ Settings", "settings"),
        # Uzbek
        ("💾 Eslab qol", "save"),
        ("⚙️ Sozlamalar", "settings"),
    ],
)
def test_match_main_kb_button_resolves_known_labels(text: str, expected: str) -> None:
    assert match_main_kb_button(text) == expected


def test_match_main_kb_button_handles_surrounding_whitespace() -> None:
    assert match_main_kb_button("  ⚙️ Настройки  ") == "settings"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "привет",
        "/save",  # the slash-command form is handled by aiogram's Command filter
        "settings",  # bare command name without emoji is NOT a button label
        "Запомнить",  # without emoji isn't a button label
    ],
)
def test_match_main_kb_button_returns_none_for_non_buttons(text: str) -> None:
    assert match_main_kb_button(text) is None


# ── consistency: every command in MAIN_KB_LABEL_KEYS resolves ────────────────


def test_every_main_kb_label_resolves_to_its_command() -> None:
    """Round-trip: build keyboard for each language, every button's text
    must resolve back to a command name matching the i18n key."""
    from src.i18n import t

    for lang in ("ru", "en", "uz"):
        for key in MAIN_KB_LABEL_KEYS:
            label = t(key, lang)
            command = key.split(".", 1)[1]
            assert match_main_kb_button(label) == command, (
                f"{lang}/{key} → {label!r} did not resolve to {command!r}"
            )

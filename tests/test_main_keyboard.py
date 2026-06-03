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
@pytest.mark.parametrize("probe_on", [True, False])
def test_main_keyboard_has_3_plus_2_layout(ui_lang: str, probe_on: bool) -> None:
    """3 buttons on top row (save + probe-toggle + settings), 2 on bottom
    (undo + start). The probe-toggle takes the middle-top slot regardless
    of its current state — only the label changes."""
    kb = main_reply_keyboard(ui_lang, probe_on)
    assert len(kb.keyboard) == 2
    assert len(kb.keyboard[0]) == 3
    assert len(kb.keyboard[1]) == 2


@pytest.mark.parametrize("ui_lang", ["ru", "en", "uz"])
def test_main_keyboard_is_resizable_and_persistent(ui_lang: str) -> None:
    kb = main_reply_keyboard(ui_lang, probe_on=True)
    assert kb.resize_keyboard is True
    assert kb.is_persistent is True


@pytest.mark.parametrize("ui_lang", ["ru", "en", "uz"])
@pytest.mark.parametrize("probe_on", [True, False])
def test_main_keyboard_buttons_have_localized_labels(
    ui_lang: str, probe_on: bool
) -> None:
    """Every button label must be a non-empty string (not a missing-key
    fallback like `kb.save` showing through)."""
    kb = main_reply_keyboard(ui_lang, probe_on)
    for row in kb.keyboard:
        for btn in row:
            assert btn.text
            assert not btn.text.startswith("kb.")  # i18n didn't fall through


@pytest.mark.parametrize("ui_lang", ["ru", "en", "uz"])
def test_main_keyboard_probe_label_reflects_state(ui_lang: str) -> None:
    """When probe is ON, the toggle shows 'probe_on' label (tap → switch OFF).
    When OFF, shows 'probe_off' label (tap → switch ON)."""
    from src.i18n import t

    kb_on = main_reply_keyboard(ui_lang, probe_on=True)
    kb_off = main_reply_keyboard(ui_lang, probe_on=False)

    labels_on = [btn.text for row in kb_on.keyboard for btn in row]
    labels_off = [btn.text for row in kb_off.keyboard for btn in row]

    assert t("kb.probe_on", ui_lang) in labels_on
    assert t("kb.probe_off", ui_lang) not in labels_on
    assert t("kb.probe_off", ui_lang) in labels_off
    assert t("kb.probe_on", ui_lang) not in labels_off


# ── label → command routing ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("💾 Запомнить", "save"),
        ("⚙️ Настройки", "settings"),
        ("⚠️ Отменить", "undo"),  # warning emoji since the button is destructive
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
    must resolve back to a command name. Probe-toggle labels are a special
    case: both states collapse to the synthetic command 'toggle_probe'."""
    from src.i18n import t

    from src.telegram.keyboards import PROBE_OFF_LABEL_KEY, PROBE_ON_LABEL_KEY

    for lang in ("ru", "en", "uz"):
        for key in MAIN_KB_LABEL_KEYS:
            label = t(key, lang)
            expected = (
                "toggle_probe"
                if key in (PROBE_ON_LABEL_KEY, PROBE_OFF_LABEL_KEY)
                else key.split(".", 1)[1]
            )
            assert match_main_kb_button(label) == expected, (
                f"{lang}/{key} → {label!r} did not resolve to {expected!r}"
            )


# ── confirmation flow for destructive buttons ────────────────────────────────


def test_undo_label_carries_warning_emoji() -> None:
    """Undo is destructive — the label itself signals risk via ⚠️."""
    from src.i18n import t

    for lang in ("ru", "en", "uz"):
        assert "⚠️" in t("kb.undo", lang), f"undo missing ⚠️ in {lang}"


def test_save_and_undo_are_in_confirm_actions() -> None:
    """save and undo must both require a Yes/No prompt; settings/start must not."""
    from src.telegram.keyboards import KB_CONFIRM_ACTIONS

    assert KB_CONFIRM_ACTIONS == {"save", "undo"}


@pytest.mark.parametrize("action", ["save", "undo"])
@pytest.mark.parametrize("ui_lang", ["ru", "en", "uz"])
def test_kb_confirm_keyboard_has_yes_and_no(action: str, ui_lang: str) -> None:
    from src.telegram.keyboards import kb_confirm_keyboard

    kb = kb_confirm_keyboard(action, ui_lang)
    assert len(kb.inline_keyboard) == 1
    row = kb.inline_keyboard[0]
    assert len(row) == 2
    callbacks = {btn.callback_data for btn in row}
    assert callbacks == {f"kb_confirm:{action}:yes", f"kb_confirm:{action}:no"}


def test_kb_confirm_keyboard_buttons_are_localized() -> None:
    """Yes/No labels must be non-fallback strings in every locale."""
    from src.i18n import t
    from src.telegram.keyboards import kb_confirm_keyboard

    for lang in ("ru", "en", "uz"):
        kb = kb_confirm_keyboard("save", lang)
        row = kb.inline_keyboard[0]
        yes_text = row[0].text
        no_text = row[1].text
        # Match what t() returns — guards against accidental hardcoding
        assert yes_text == t("kb.confirm_yes", lang)
        assert no_text == t("kb.confirm_no", lang)
        # Sanity: neither is empty or shows the raw key
        assert yes_text and not yes_text.startswith("kb.")
        assert no_text and not no_text.startswith("kb.")


def test_confirm_prompts_present_in_all_locales() -> None:
    """The warning text shown above the Yes/No buttons must exist in ru/en/uz."""
    from src.i18n import t

    for lang in ("ru", "en", "uz"):
        for key in ("kb.confirm_save", "kb.confirm_undo"):
            val = t(key, lang)
            assert val and val != key
            # Should mention what's happening, not be a one-liner placeholder
            assert len(val) > 30, f"{lang}/{key} suspiciously short: {val!r}"

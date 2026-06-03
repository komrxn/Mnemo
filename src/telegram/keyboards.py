from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.i18n import LANGUAGES, Language, t


def confirm_keyboard(correlation_id: str, ui_lang: str = "ru") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("confirm.button_yes", ui_lang),
            callback_data=f"confirm:{correlation_id}:yes",
        ),
        InlineKeyboardButton(
            text=t("confirm.button_no", ui_lang),
            callback_data=f"confirm:{correlation_id}:no",
        ),
    )
    return builder.as_markup()


# ── persistent main keyboard ──────────────────────────────────────────────────


# Keys whose values are the labels rendered on the reply-keyboard buttons.
# Order is rendering order. Probe-toggle labels are both included so
# `match_main_kb_button` recognizes whichever the user actually sees right
# now (state may have flipped since the keyboard was last sent).
PROBE_ON_LABEL_KEY = "kb.probe_on"
PROBE_OFF_LABEL_KEY = "kb.probe_off"
MAIN_KB_LABEL_KEYS: tuple[str, ...] = (
    "kb.save",
    "kb.settings",
    "kb.undo",
    "kb.start",
    PROBE_ON_LABEL_KEY,
    PROBE_OFF_LABEL_KEY,
)


def main_reply_keyboard(ui_lang: str, probe_on: bool) -> ReplyKeyboardMarkup:
    """Persistent reply-keyboard with all bot commands as friendly labels.

    Rendered below the input field, always visible (`is_persistent=True`,
    `resize_keyboard=True`). Tapping a button sends its localized label as
    plain text — `handle_text` routes that text to the matching command
    handler via `match_main_kb_button`.

    Layout: 3+2 grid. Top row holds the most-frequent actions including
    the probe-mode toggle (label depends on current state). Bottom row is
    for rare actions.

    The toggle label is dynamic: when `probe_on=True` it shows "🧠 Копаем"
    (tapping it switches OFF), when `probe_on=False` it shows
    "📝 Записываем" (tapping switches ON). Single source of layout truth.
    """
    probe_label_key = PROBE_ON_LABEL_KEY if probe_on else PROBE_OFF_LABEL_KEY
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=t("kb.save", ui_lang)),
                KeyboardButton(text=t(probe_label_key, ui_lang)),
                KeyboardButton(text=t("kb.settings", ui_lang)),
            ],
            [
                KeyboardButton(text=t("kb.undo", ui_lang)),
                KeyboardButton(text=t("kb.start", ui_lang)),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=t("kb.placeholder", ui_lang),
    )


# Actions that route through a confirmation prompt instead of firing directly.
# Tied to the reply-keyboard buttons of the same name; settings/start do NOT
# need a confirm step (they're idempotent / read-only with respect to memory).
KB_CONFIRM_ACTIONS: frozenset[str] = frozenset({"save", "undo"})


def kb_confirm_keyboard(action: str, ui_lang: str) -> InlineKeyboardMarkup:
    """Two-button inline keyboard (Yes/No) attached to the warning message
    that pops up when a destructive reply-keyboard button is tapped.

    Callback data shape: `kb_confirm:<action>:yes|no`. The handler in
    `handlers/commands.py` reads `<action>` and dispatches to the matching
    impl, or shows a "cancelled" toast on `no`.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("kb.confirm_yes", ui_lang),
                    callback_data=f"kb_confirm:{action}:yes",
                ),
                InlineKeyboardButton(
                    text=t("kb.confirm_no", ui_lang),
                    callback_data=f"kb_confirm:{action}:no",
                ),
            ]
        ]
    )


def match_main_kb_button(text: str) -> str | None:
    """If `text` matches a known reply-keyboard label (any language), return
    the corresponding command name (e.g. "save"). Else None.

    Scans labels for all three languages because the user may have changed
    UI language without the client immediately re-rendering the keyboard —
    so a label they tap can be from the prior locale. `t()` caches yaml
    loads so the per-call cost is negligible.

    Both probe-toggle labels (`probe_on` and `probe_off`) collapse to the
    synthetic command name `"toggle_probe"` — the handler then reads current
    state from the user's profile and flips it. This avoids two parallel
    handler entries for what is conceptually one button.
    """
    if not text:
        return None
    needle = text.strip()
    for lang in LANGUAGES:
        ll: Language = lang
        for key in MAIN_KB_LABEL_KEYS:
            if t(key, ll).strip() == needle:
                if key in (PROBE_ON_LABEL_KEY, PROBE_OFF_LABEL_KEY):
                    return "toggle_probe"
                return key.split(".", 1)[1]
    return None

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.i18n import t


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
